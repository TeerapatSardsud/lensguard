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
from features import make_features, records_to_minutes, MIN_HISTORY, HORIZON  # noqa: E402
import netpie                                     # noqa: E402

BOXES = ("box_A", "box_B")
KEEP_MS = 90 * 60_000          # history kept in memory (features need 16 min)


class Engine:
    def __init__(self, bundle, publish, alerts, log_dir=None):
        self.bundle, self.publish, self.alerts = bundle, publish, alerts
        self.recent = deque()
        self.minute = None
        self.fan_state = {b: 0 for b in BOXES}
        self.last_pred = {b: None for b in BOXES}
        self.stats = {"records": 0, "commands": 0, "warmup": 0}
        self.live_log = self.pred_log = None
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            self.live_log = open(os.path.join(log_dir, "live.jsonl"), "a", buffering=1)
            self.pred_log = open(os.path.join(log_dir, "predictions.jsonl"), "a", buffering=1)

    def ingest(self, rec, rx_ms=None):
        rec = dict(rec)
        rec["rx_ms"] = rx_ms if rx_ms is not None else int(time.time() * 1000)
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
        self.alerts.on_record(rec, self.last_pred.get(rec.get("node_id")))
        m = t // 60_000                                            # data-driven clock: works for replay/fast sim
        if self.minute is None:
            self.minute = m
        elif m > self.minute:
            self.minute = m
            self.tick(t)

    def predict(self, box):
        df = records_to_minutes(self.recent, box)
        if len(df) < MIN_HISTORY or self.bundle is None:
            return df, None
        f = make_features(df, with_target=False)
        if f.empty:
            return df, None
        x = f.iloc[[-1]][self.bundle["feats"]]
        return df, float(self.bundle["model"].predict(x)[0])

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
            if self.pred_log:   # pred made at ts for ts + HORIZON min -> live AI MAE in tools/metrics.py
                self.pred_log.write(json.dumps({**cmd, "horizon_min": HORIZON}) + "\n")


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
    eng = Engine(bundle, publish=lambda c: (cmds.append(c), gw.on_fan_cmd(c)),
                 alerts=AlertEngine(Telegram(dry_run=True), door_after_s=10**9))
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
    if bundle is not None:
        assert any(e["event"] == "fan" and e["detail"] == "ai" for e in events), "gateway never followed AI"
    assert any(e["event"] == "silica" for e in events), "saturated box_B should raise a silica event"
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(HERE, "model.joblib"))
    ap.add_argument("--log-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--door-after", type=int, default=120, help="seconds a lid may stay open before an alert")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--replay", metavar="JSONL")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--host", default=netpie.HOST)
    ap.add_argument("--port", type=int, default=netpie.PORT_TLS)
    ap.add_argument("--no-tls", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)    # logs appear immediately in files / journalctl
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))  # systemd stop = Ctrl+C
    netpie.load_dotenv()
    bundle = load_model(a.model)

    if a.selftest:
        return selftest(bundle)

    if a.replay:
        eng = Engine(bundle, publish=lambda c: print("[cmd]", c),
                     alerts=AlertEngine(Telegram(dry_run=True), door_after_s=a.door_after))
        with open(a.replay) as f:
            for line in f:
                r = json.loads(line)
                eng.ingest(r, rx_ms=r.get("rx_ms"))
        print(eng.stats)
        return

    alerts = AlertEngine(Telegram(dry_run=a.no_telegram), door_after_s=a.door_after,
                         log_path=os.path.join(a.log_dir, "alerts.jsonl"))
    q = queue.Queue()
    client = netpie.make_client("AI_NETPIE", a.host, a.port, tls=not a.no_tls,
                                on_message=lambda c, u, m: q.put((m.payload, int(time.time() * 1000))),
                                subscribe=[netpie.TOPIC_DATA])

    def publish(cmd):
        client.publish(netpie.TOPIC_FAN, json.dumps(cmd, separators=(",", ":")))
        print(f"[cmd] {cmd['node_id']} fan={'ON ' if cmd['fan'] else 'OFF'} rh={cmd['rh']} pred={cmd['pred']}")

    eng = Engine(bundle, publish, alerts, log_dir=a.log_dir)
    print("[ai] running - Ctrl+C to stop")
    try:
        while True:
            try:
                payload, rx = q.get(timeout=1)
                eng.ingest(json.loads(payload), rx_ms=rx)
            except queue.Empty:
                pass
            except ValueError:
                print("[ai] bad JSON ignored")
            alerts.tick()                                          # silence / lid-open timers
    except KeyboardInterrupt:
        print("\n[ai] stopped", eng.stats)
    client.loop_stop(); client.disconnect()


if __name__ == "__main__":
    main()
