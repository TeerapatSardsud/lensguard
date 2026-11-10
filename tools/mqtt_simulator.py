#!/usr/bin/env python3
"""Simulate two LensGuard boxes + gateway publishing to NETPIE (or any MQTT broker).

Lets the team test cloud -> dashboard -> alert pipeline BEFORE hardware arrives.

Usage
  python tools/mqtt_simulator.py --dry-run --count 5          # just print JSON
  export NETPIE_CLIENT_ID=... NETPIE_TOKEN=... NETPIE_SECRET=...
  python tools/mqtt_simulator.py --period 5 --count 60        # publish to NETPIE over TLS
  python tools/mqtt_simulator.py --host localhost --port 1883 --no-tls --dry-run=false ...

Credentials come from environment variables only (never hard-code them).
"""
import argparse, json, os, random, ssl, sys, time


class Box:
    """Very small physical model: RH relaxes toward outside RH (leakage),
    drops quickly when the fan runs (silica gel), jumps when the door opens."""

    def __init__(self, node_id, rh0, loss=0.0):
        self.node_id, self.rh, self.seq, self.door, self.loss = node_id, rh0, 0, 0, loss
        self.door_left = 0

    def step(self, dt_min, rh_out, fan_on):
        leak = 0.01 * (rh_out - self.rh)
        fan = -0.12 * (self.rh - 35.0) if fan_on else 0.0
        door = 0.3 * (rh_out - self.rh) if self.door else 0.0
        self.rh += (leak + fan + door) * dt_min + random.gauss(0, 0.15)
        self.rh = min(95.0, max(25.0, self.rh))
        if self.door_left > 0:
            self.door_left -= 1
            if self.door_left == 0:
                self.door = 0
        elif random.random() < 0.03:
            self.door, self.door_left = 1, random.randint(1, 2)

    def message(self, t_amb):
        self.seq += 1
        return {"node_id": self.node_id, "seq": self.seq, "ts": int(time.time()),
                "temp": round(t_amb + random.gauss(0, 0.2), 1), "rh": round(self.rh, 1), "door": self.door}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print only, do not connect")
    ap.add_argument("--period", type=float, default=5.0, help="seconds between rounds (sim time = 1 min/round)")
    ap.add_argument("--count", type=int, default=0, help="rounds to run (0 = forever)")
    ap.add_argument("--host", default="mqtt.netpie.io")
    ap.add_argument("--port", type=int, default=1884)
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--topic", default="@shadow/data/update")
    ap.add_argument("--loss", type=float, default=0.0, help="simulated packet loss ratio, e.g. 0.1")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    if a.seed is not None:
        random.seed(a.seed)

    client = None
    if not a.dry_run:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            sys.exit("pip install paho-mqtt")
        cid, tok, sec = (os.environ.get(k) for k in ("NETPIE_CLIENT_ID", "NETPIE_TOKEN", "NETPIE_SECRET"))
        if not (cid and tok and sec):
            sys.exit("Set NETPIE_CLIENT_ID / NETPIE_TOKEN / NETPIE_SECRET (or use --dry-run)")
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        client.username_pw_set(tok, sec)
        if not a.no_tls:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)   # system CA store
        client.connect(a.host, a.port, keepalive=60)
        client.loop_start()

    boxes = [Box("box_A", 52.0), Box("box_B", 60.0)]
    fan_on, n = False, 0
    while a.count == 0 or n < a.count:
        n += 1
        rh_out = 72 + 6 * random.random()
        t_amb = 28 + random.random()
        # same fallback rule as the gateway firmware
        worst = max(b.rh for b in boxes)
        fan_on = True if worst >= 60 else (False if worst <= 55 else fan_on)
        for b in boxes:
            b.step(1.0, rh_out, fan_on)
            msg = b.message(t_amb)
            if random.random() < a.loss:
                print(f"[loss] dropped {b.node_id} seq={msg['seq']}")
                continue
            payload = json.dumps({"data": msg})
            if client:
                client.publish(a.topic, payload, qos=0)
            print(("[dry] " if a.dry_run else "[pub] ") + payload)
        gw = {"node_id": "gateway", "ts": int(time.time()), "temp_out": round(t_amb + 2, 1),
              "ldr": random.randint(200, 400), "gas": random.randint(100, 140), "fan": int(fan_on)}
        payload = json.dumps({"data": gw})
        if client:
            client.publish(a.topic, payload)
        print(("[dry] " if a.dry_run else "[pub] ") + payload)
        time.sleep(0 if a.dry_run else a.period)
    if client:
        client.loop_stop(); client.disconnect()


if __name__ == "__main__":
    main()
