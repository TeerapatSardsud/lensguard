#!/usr/bin/env python3
"""LensGuard AI service = the closed loop (M4) + alerts (M6) + data logger (training data / metrics).

  NETPIE @msg/lensguard/data ──> log every record (ai/data/live.jsonl) ──> 1-min history per box
      ──> RandomForest predicts RH in 10 min ──> controller.decide_fan ──> @msg/lensguard/fan
      ──> Telegram alerts (RH high, lid open, node silent, silica saturated)

The gateway follows a fan command for AI_CMD_TTL_MS (2 min); this service re-sends every minute.
If this service stops, the gateway falls back to its own 55/45 rule (failsafe) automatically.

Usage
  python ai/service.py --selftest                     # offline closed loop with the simulator, no network
  python ai/service.py                                # live: needs AI_NETPIE_* (+ TELEGRAM_*) in env/.env
  python ai/service.py --replay ai/data/live.jsonl    # re-run decisions on a logged file, no network
Dashboard: the live service also serves http://localhost:8080 (ai/dashboard.py + ai/web/index.html).
The AI runs as its OWN NETPIE device (AI_NETPIE_CLIENT_ID/TOKEN/SECRET) in the same device group as the
gateway, because one ClientID cannot be connected twice.
"""
import argparse
import json
import os
import queue
import signal
import sys
import time
from collections import deque

import joblib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from alerts import AlertEngine, Telegram          # noqa: E402
from controller import decide_fan                 # noqa: E402
from dashboard import Dashboard                   # noqa: E402
from features import make_features, records_to_minutes, MIN_HISTORY, HORIZON  # noqa: E402
import netpie                                     # noqa: E402

BOXES = ("box_A", "box_B")
KEEP_MS = 90 * 60_000          # history kept in memory (features need 16 min)


class Engine:
    def __init__(self, bundle, publish, alerts, log_dir=None, dashboard=None):
        self.bundle, self.publish, self.alerts, self.dashboard = bundle, publish, alerts, dashboard
        self.recent = deque()
        self.minute = None
        self.fan_state = {b: 0 for b in BOXES}
        self.last_pred = {b: None for b in BOXES}
        self.stats = {"records": 0, "commands": 0, "warmup": 0}
        self.live_log = self.pred_log = None
        from clock import DriftCorrector
        self.clock = None if os.environ.get("LENSGUARD_CLOCK_CORRECT") == "0" else DriftCorrector()
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            self.live_log = open(os.path.join(log_dir, "live.jsonl"), "a", buffering=1)
            self.pred_log = open(os.path.join(log_dir, "predictions.jsonl"), "a", buffering=1)

    def ingest(self, rec, rx_ms=None):
        rec = dict(rec)
        rec["rx_ms"] = rx_ms if rx_ms is not None else int(time.time() * 1000)
        if self.clock:
            self.clock.correct(rec)                          # fast/skewed gateway clock -> real timeline
            if self.clock.just_activated:
                print("[clock] rebasing: dropping history built on the old timestamps (16-min warmup restarts)")
                self.recent.clear()
                self.minute = None
                if self.dashboard:
                    self.dashboard.forget_all()
        if self.live_log:
            self.live_log.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self.stats["records"] += 1
        t = rec.get("ts") or rec["rx_ms"]
        if rec.get("node_id") == "gateway":                       # actual fan state (after gateway safety)
            if rec.get("event") == "fan" and rec.get("box") in self.fan_state:
                self.fan_state[rec["box"]] = int(float(rec.get("value", 0)))
            for b in BOXES:
                if f"fan_{b}" in rec:
                    self.fan_state[b] = int(rec[f"fan_{b}"])
        self.recent.append(rec)
        while self.recent and (self.recent[-1].get("ts") or self.recent[-1]["rx_ms"]) - \
                (self.recent[0].get("ts") or self.recent[0]["rx_ms"]) > KEEP_MS:
            self.recent.popleft()
        if self.dashboard:
            self.dashboard.ingest(rec)
        self.alerts.on_record(rec, self.last_pred.get(rec.get("node_id")))
        m = t // 60_000                                            # data-driven clock: works for replay/fast sim
        if self.minute is None:
            self.minute = m
        elif m > self.minute:
            self.minute = m
            self.tick(t)

    def preload(self, path, hours=24):
        """Refill the in-memory history from our own log after a restart.

        Without this the AI is blind for MIN_HISTORY (16) minutes after every
        restart: dash.preload() rebuilds the CHARTS only, so `recent` starts
        empty and decide_fan() falls back to gateway rules while the model
        warms up. Here we restore the prediction buffer itself. Records are
        appended straight into `recent` (not via ingest) on purpose: no
        re-logging to live.jsonl, no replayed alerts, no clock re-correction
        of data that was already corrected when it was written.
        """
        if not os.path.exists(path):
            return 0
        rows = []
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("rx_ms"):
                    rows.append(r)
        if not rows:
            return 0
        newest = max(r["rx_ms"] for r in rows)
        since_rx = newest - hours * 3_600_000
        n = 0
        for r in sorted(rows, key=lambda r: r["rx_ms"]):
            if r["rx_ms"] < since_rx:
                continue
            r = dict(r)
            ts = r.get("ts")
            # same physically-impossible-clock clamp the dashboard applies
            if ts and ts - r["rx_ms"] > 10 * 60_000:
                r["ts"] = r["rx_ms"]
            if self.recent and r.get("ts") and (r["ts"] < self.recent[-1].get("ts") or 0):
                continue
            self.recent.append(r)
            n += 1
        while self.recent and (self.recent[-1].get("ts") or self.recent[-1]["rx_ms"]) - \
                (self.recent[0].get("ts") or self.recent[0]["rx_ms"]) > KEEP_MS:
            self.recent.popleft()
        return n

    def predict(self, box):
        df = records_to_minutes(self.recent, box)
        if len(df) < MIN_HISTORY or self.bundle is None:
            return df, None
        f = make_features(df, with_target=False)
        if f.empty:
            return df, None
        x = f.iloc[[-1]][self.bundle["feats"]]
        pred = float(self.bundle["model"].predict(x)[0])
        if not (0.0 <= pred <= 100.0):            # linear models can extrapolate outside physical RH
            self.stats["clamped"] = self.stats.get("clamped", 0) + 1
            pred = min(100.0, max(0.0, pred))
        return df, pred

    def tick(self, t):
        for b in BOXES:
            df, pred = self.predict(b)
            if df.empty:
                continue
            rh_now = float(df["rh"].iloc[-1])
            if pred is None:                                       # not enough history: gateway fallback rules
                self.stats["warmup"] += 1
                continue
            self.last_pred[b] = pred
            fan = decide_fan(rh_now, pred, bool(self.fan_state[b]))
            cmd = {"node_id": b, "fan": int(fan), "pred": round(pred, 1), "rh": round(rh_now, 1), "ts": t}
            self.publish(cmd)
            self.stats["commands"] += 1
            if self.dashboard:
                self.dashboard.on_pred(b, pred, fan, t)
            if self.pred_log:   # pred made at ts for ts + HORIZON min -> live AI MAE in tools/metrics.py
                self.pred_log.write(json.dumps({**cmd, "horizon_min": HORIZON}) + "\n")


def lan_ip():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))          # no packet is sent; just picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def load_model(path):
    if not os.path.exists(path):
        print(f"[ai] no model at {path} -> no AI commands, gateway uses its fallback rule. Run ai/train.py")
        return None
    b = joblib.load(path)
    print(f"[ai] model {b['name']} trained on {b.get('data')}")
    return b


def selftest(bundle, minutes=360):
    from mqtt_simulator import FakeGateway
    import random
    random.seed(1)
    gw = FakeGateway(loss=0.05, saturate=["box_B"], start_ms=1_767_225_600_000)
    cmds = []
    dash = Dashboard(model_name=bundle and bundle["name"])
    eng = Engine(bundle, publish=lambda c: (cmds.append(c), gw.on_fan_cmd(c)),
                 alerts=AlertEngine(Telegram(dry_run=True), door_after_s=10**9, on_sent=dash.on_alert),
                 dashboard=dash)
    events = []
    for _ in range(minutes * 2):
        for rec in gw.step():
            if rec.get("event"):
                events.append(rec)
            eng.ingest(rec, rx_ms=rec["ts"])
    early = [c for c in cmds if c["fan"] == 1 and c["rh"] < 55]
    print(f"selftest: {eng.stats['records']} records, {len(cmds)} AI commands, "
          f"{len(early)} early starts (fan ON while RH<55 because forecast>=55), "
          f"fan events={sum(e['event'] == 'fan' for e in events)}, "
          f"silica events={sum(e['event'] == 'silica' for e in events)}")
    assert cmds, "AI produced no commands"
    # restart warm-start: a fresh Engine preloaded from our own log must be
    # able to predict immediately (no 16-min blind window after a deploy).
    if bundle is not None:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
            for rec in eng.recent:
                tf.write(json.dumps(rec) + "\n")
            log_path = tf.name
        eng2 = Engine(bundle, publish=lambda c: None,
                      alerts=AlertEngine(Telegram(dry_run=True), door_after_s=10**9))
        n = eng2.preload(log_path)
        _, pred = eng2.predict("box_A")
        os.unlink(log_path)
        assert n > 0 and pred is not None, f"preloaded engine could not predict (n={n})"
        print(f"selftest: restart warm-start ok ({n} records reloaded, first prediction immediate)")
    if bundle is not None:
        assert any(e["event"] == "fan" and e["detail"] == "ai" for e in events), "gateway never followed AI"
    assert any(e["event"] == "silica" for e in events), "saturated box_B should raise a silica event"
    # dashboard: HTTP page + JSON snapshot
    import urllib.request
    httpd = dash.serve("127.0.0.1", 0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
    snap = json.load(urllib.request.urlopen(base + "/api/state?range=360", timeout=5))
    httpd.shutdown()
    assert "LensGuard" in page and len(snap["boxes"]["box_A"]["series"]) > 300, "dashboard snapshot incomplete"
    assert snap["boxes"]["box_B"]["pred"] and snap["alerts"], "dashboard missing prediction/alerts"
    print(f"dashboard: page {len(page)} B, {len(snap['boxes']['box_A']['series'])} points/box, "
          f"{len(snap['alerts'])} alerts")
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(HERE, "model.joblib"))
    ap.add_argument("--log-dir", default=None,
                    help=f"jsonl logs dir (default {os.path.join(HERE, 'data')}; replay mode logs only if given)")
    ap.add_argument("--door-after", type=int, default=120, help="seconds a lid may stay open before an alert")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--replay", metavar="JSONL")
    ap.add_argument("--shadow", action="store_true",
                    help="poll the gateway NETPIE shadow (HTTPS) instead of MQTT @msg")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--host", default=netpie.HOST)
    ap.add_argument("--port", type=int, default=netpie.PORT_TLS)
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--http-port", type=int, default=8080, help="dashboard port (0 = no dashboard)")
    ap.add_argument("--http-host", default="0.0.0.0", help="0.0.0.0 = reachable from phones on the same Wi-Fi")
    ap.add_argument("--banner", default="", help='text shown on top of the dashboard, e.g. "ข้อมูลจำลองจาก simulator"')
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)    # logs appear immediately in files / journalctl
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))  # systemd stop = Ctrl+C
    netpie.load_dotenv()
    bundle = load_model(a.model)

    if a.selftest:
        return selftest(bundle)

    if a.replay:
        eng = Engine(bundle, publish=lambda c: print("[cmd]", c),
                     alerts=AlertEngine(Telegram(dry_run=True), door_after_s=a.door_after),
                     log_dir=a.log_dir)     # corrected live/prediction logs when --log-dir given (metrics evidence)
        with open(a.replay) as f:
            for line in f:
                r = json.loads(line)
                eng.ingest(r, rx_ms=r.get("rx_ms"))
        print(eng.stats)
        return

    if a.log_dir is None:
        a.log_dir = os.path.join(HERE, "data")
    dash = None
    if a.http_port:
        dash = Dashboard(banner=a.banner, model_name=bundle and bundle["name"])
        n = dash.preload(os.path.join(a.log_dir, "live.jsonl"))      # charts survive a restart
        dash.serve(a.http_host, a.http_port)
        print(f"[web] dashboard on http://localhost:{a.http_port}  (LAN: http://{lan_ip()}:{a.http_port})"
              + (f", {n} records from the last 24 h reloaded" if n else ""))
    alerts = AlertEngine(Telegram(dry_run=a.no_telegram), door_after_s=a.door_after,
                         log_path=os.path.join(a.log_dir, "alerts.jsonl"),
                         on_sent=dash.on_alert if dash else None)
    q = queue.Queue()
    source = None
    client = None
    if a.shadow:
        from shadow import ShadowSource
        source = ShadowSource(os.environ.get("NETPIE_CLIENT_ID"), os.environ.get("NETPIE_TOKEN"))
        print("[shadow] input = HTTPS shadow polling (no MQTT, does not touch the gateway ClientID)")
    else:
        client = netpie.make_client("AI_NETPIE", a.host, a.port, tls=not a.no_tls,
                                    on_message=lambda c, u, m: q.put((m.payload, int(time.time() * 1000))),
                                    subscribe=[netpie.TOPIC_DATA])

    def publish(cmd):
        if a.shadow or client is None:   # gateway project delivers no @msg: decisions recorded, not delivered
            print(f"[cmd] ({cmd['node_id']}) shadow-mode decision only: fan={'ON ' if cmd['fan'] else 'OFF'} rh={cmd['rh']} pred={cmd['pred']}")
            return
        client.publish(netpie.TOPIC_FAN, json.dumps(cmd, separators=(",", ":")))
        print(f"[cmd] {cmd['node_id']} fan={'ON ' if cmd['fan'] else 'OFF'} rh={cmd['rh']} pred={cmd['pred']}")

    eng = Engine(bundle, publish, alerts, log_dir=a.log_dir, dashboard=dash)
    # charts survive a restart via dash.preload; the model must too, or every
    # deploy blinds the AI for MIN_HISTORY minutes and the fan falls back to
    # gateway rules (observed live 2026-11-20 after the 14:51 restart).
    n_hist = eng.preload(os.path.join(a.log_dir, "live.jsonl"))
    if n_hist:
        newest = max(r.get("ts") or r["rx_ms"] for r in eng.recent)
        eng.minute = newest // 60_000
        print(f"[ai] prediction buffer warm-started from live.jsonl: {n_hist} records "
              f"(up to {time.strftime('%H:%M:%S', time.localtime(newest / 1000))}) - no warmup gap")
    print("[ai] running - Ctrl+C to stop")
    try:
        while True:
            try:
                if source is not None:
                    for rec, rx in source.poll():
                        eng.ingest(rec, rx_ms=rx)
                    time.sleep(5)
                    continue
                payload, rx = q.get(timeout=1)
                eng.ingest(json.loads(payload), rx_ms=rx)
            except queue.Empty:
                pass
            except ValueError:
                print("[ai] bad JSON ignored")
            alerts.tick()                                          # silence / lid-open timers
    except KeyboardInterrupt:
        print("\n[ai] stopped", eng.stats)
    if client is not None:
        client.loop_stop(); client.disconnect()


if __name__ == "__main__":
    main()
