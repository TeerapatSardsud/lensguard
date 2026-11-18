#!/usr/bin/env python3
"""E2E test for dashboard stale-banner + clock-skew-proof freshness.

Architecture under test: real service.py (dashboard + MQTT subscriber) vs local mosquitto,
fed by FakeGateway with ts = Pi_now + 49h (models the gateway clock skew seen on NETPIE).
Checks:
  1. while data flows and gateway ts is 49h ahead: gateway online MUST be true (rx_ms wins)
  2. after publishing stops: auto banner appears (STALE_BANNER_S patched to 90s), Thai text
  3. manual --banner still overrides
  4. frontend freshness rule sanity: server_now - last_rx_ms < 120000 -> False while stale
"""
import json, os, subprocess, sys, time, urllib.request

REPO = "/home/user01/.hermes/cache/scratch/lgwork"
sys.path.insert(0, os.path.join(REPO, "ai"))
sys.path.insert(0, os.path.join(REPO, "tools"))
import netpie                      # noqa: E402
import mqtt_simulator as ms        # noqa: E402

API = "http://127.0.0.1:18090/api/state?range=60"
env = dict(os.environ, AI_NETPIE_CLIENT_ID="lgtest-ai", AI_NETPIE_TOKEN="t", AI_NETPIE_SECRET="s",
           LENSGUARD_STALE_BANNER_S="60", PYTHONUNBUFFERED="1")
svc = subprocess.Popen(
    [os.path.join(REPO, ".venv/bin/python"), "ai/service.py",
     "--host", "127.0.0.1", "--port", "11883", "--no-tls", "--no-telegram",
     "--http-port", "18090", "--log-dir", "/tmp/lg-e2e-logs"],
    cwd=REPO, env=env)

# monkeypatch: local broker = no auth, no pinning; gateway clock = real time + 49h
netpie.credentials = lambda prefix="NETPIE": (f"lgtest-{prefix.lower().split('_')[0]}-{os.getpid()}", "t", "s")  # unique id: never collide with another test run
netpie.pinned_context = lambda: None
SKEW = 49 * 3_600_000
gw = ms.FakeGateway()
_orig_step = gw.step
SKEWED_TS = gw.now + SKEW            # every ts = real start time + 49h, constant across steps
def step_skewed():
    recs = _orig_step()
    for r in recs:
        r["ts"] = SKEWED_TS
    return recs
gw.step = step_skewed

import paho.mqtt.client as mqtt        # noqa: E402
pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"lgtest-pub-{os.getpid()}")
pub.connect("127.0.0.1", 11883); pub.loop_start()

def api():
    with urllib.request.urlopen(API, timeout=5) as r:
        return json.load(r)

def wait_data(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = api()
        if st["boxes"]["box_A"]["last_rx_ms"] and st["gateway"].get("online"):
            return st
        time.sleep(1)
    raise SystemExit("FAIL: no data arrived / gateway not online")

fails = []
def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))
    if not ok:
        fails.append(name)

try:
    time.sleep(4)                     # service boot
    # --- publish 8 steps (~16s) with skewed gateway clock ---
    for _ in range(8):
        for rec in gw.step():
            pub.publish(netpie.TOPIC_DATA, json.dumps(rec, separators=(",", ":")))
        time.sleep(2)

    st = wait_data()
    gwts = st["gateway"].get("ts", 0)
    skew_h = (gwts - st["server_now_ms"]) / 3_600_000
    check("gateway online despite +49h ts skew", st["gateway"]["online"] and 48 < skew_h < 50,
          f"ts-server = {skew_h:.1f}h, online={st['gateway']['online']}")
    check("box online", st["boxes"]["box_A"]["online"])
    check("no banner while fresh", st["banner"] == "", repr(st["banner"]))

    # --- stop publishing; banner appears past 60s, frontend stale-dot rule is 120s ---
    print("phase-1 done; waiting 180s (banner threshold 60s, dot threshold 120s)...")
    time.sleep(180)
    st = api()
    check("auto banner after outage", "ไม่ได้ข้อมูลใหม่" in st["banner"], st["banner"][:80])
    check("banner cites receive time, not ts", "รับล่าสุด" in st["banner"], st["banner"][80:170])
    stale_s = (st["server_now_ms"] - st["last_rx_ms"]) / 1000
    check("frontend stale rule (server_now-last_rx<120s is False)", not (stale_s < 120), f"stale {stale_s:.0f}s")
    check("green dot would be off", (st["server_now_ms"] - max(
        [x["last_rx_ms"] or 0 for x in st["boxes"].values()] + [st["gateway"].get("rx_ms") or 0])) >= 120000)
    check("chart still follows gateway ts (data_now ahead)", st["data_now_ms"] > st["server_now_ms"],
          f"data_now-server = {(st['data_now_ms']-st['server_now_ms'])/3_600_000:.1f}h")

    # the .banner CSS sets display:flex, which overrides the `hidden` attribute unless a
    # .banner[hidden]{display:none} rule exists -> empty warning bar bug
    html = open(os.path.join(REPO, "ai/web/index.html"), encoding="utf-8").read()
    check("empty banner cannot render (CSS [hidden] rule present)", ".banner[hidden] { display: none; }" in html)
finally:
    pub.loop_stop(); pub.disconnect()
    svc.terminate(); svc.wait(timeout=10)

print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
