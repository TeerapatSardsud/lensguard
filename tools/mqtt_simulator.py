#!/usr/bin/env python3
"""Simulated LensGuard gateway + two boxes. Publishes EXACTLY the records the real gateway firmware
publishes and obeys AI fan commands the same way (TTL, 55/45 fallback, 30-min timeout, cool-down,
silica event), so cloud -> dashboard -> AI -> Telegram can be built and filmed before hardware arrives.
Say clearly in any video/report that this data is SIMULATED.

Usage
  python tools/mqtt_simulator.py --dry-run --count 5                # print records, no network
  python tools/mqtt_simulator.py                                    # real time (30 s per step) to NETPIE
  python tools/mqtt_simulator.py --period 1                         # 30x faster (sim clock in "ts")
  python tools/mqtt_simulator.py --period 1 --loss 0.1 --saturate box_B   # packet loss + silica alert demo
Credentials (gateway device): NETPIE_CLIENT_ID / NETPIE_TOKEN / NETPIE_SECRET in env or .env
"""
import argparse
import json
import os
import random
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netpie  # noqa: E402

# keep in sync with common/protocol.h
RH_FAN_ON, RH_FAN_OFF = 55.0, 45.0
AI_CMD_TTL_MS = 120_000
FAN_MAX_ON_MS, FAN_COOLDOWN_MS = 30 * 60_000, 5 * 60_000
SILICA_MIN_DROP = 3.0
STEP_MS = 30_000  # SAMPLE_PERIOD_MS


class Box:
    """Tiny physical model: RH leaks toward outside RH, drops when the fan pushes air through the
    silica gel (efficiency 0..1), jumps when the lid is open."""

    def __init__(self, node_id, rh0, efficiency=1.0):
        self.node_id, self.rh, self.temp, self.eff = node_id, rh0, 28.0, efficiency
        self.seq, self.door, self.door_left, self.boot = 0, 0, 0, random.randint(0, 65535)
        self.fan, self.fan_since, self.rh_at_start, self.cooldown_until = 0, 0, 0.0, 0
        self.ai_cmd, self.ai_ms = None, 0

    def step(self, dt_min, rh_out, t_out):
        leak = 0.01 * (rh_out - self.rh)
        fan = -0.12 * self.eff * (self.rh - 35.0) if self.fan else 0.0
        door = 0.4 * (rh_out - self.rh) if self.door else 0.0
        self.rh = min(95.0, max(25.0, self.rh + (leak + fan + door) * dt_min + random.gauss(0, 0.12)))
        self.temp += 0.05 * (t_out - self.temp) * dt_min
        changed = False
        if self.door_left > 0:
            self.door_left -= 1
            if self.door_left == 0:
                self.door, changed = 0, True
        elif random.random() < 0.01:
            self.door, self.door_left, changed = 1, random.randint(1, 4), True
        return changed


class FakeGateway:
    def __init__(self, loss=0.0, saturate=(), start_ms=None):
        self.boxes = {b.node_id: b for b in (Box("box_A", 50.0), Box("box_B", 58.0))}
        for b in saturate:
            self.boxes[b].eff = 0.03
        self.loss, self.gw_seq, self.lock = loss, 0, threading.Lock()
        self.now = start_ms if start_ms is not None else int(time.time() * 1000)
        self.start, self.rx_ok, self.booted = self.now, 0, False

    def on_fan_cmd(self, cmd: dict):
        with self.lock:
            b = self.boxes.get(cmd.get("node_id"))
            if b:
                b.ai_cmd, b.ai_ms = int(cmd.get("fan", 0)), self.now

    def _set_fan(self, b, on, why, out):
        if on == b.fan:
            return
        b.fan = on
        if on:
            b.fan_since, b.rh_at_start = self.now, b.rh
        out.append({"node_id": "gateway", "ts": self.now, "event": "fan", "box": b.node_id,
                    "detail": why, "value": float(on)})

    def _box_record(self, b, reason):
        b.seq += 1
        return {"node_id": b.node_id, "seq": b.seq, "boot": b.boot, "ts": self.now,
                "temp": round(b.temp + random.gauss(0, 0.1), 1), "rh": round(b.rh, 1), "valid": 1,
                "door": b.door, "reason": reason, "tries": 1 if random.random() > 0.05 else 2}

    def step(self):
        """Advance 30 s; return the records the gateway would publish."""
        out = []
        with self.lock:
            if not self.booted:                                       # firmware emits this once per boot
                self.booted = True
                out.append({"node_id": "gateway", "ts": self.now, "event": "boot", "detail": "POWERON", "value": 0.0})
            self.now += STEP_MS
            hour = (self.now / 3_600_000 + 7) % 24
            rh_out = 72 + 8 * random.random() - 6 * (1 if 10 <= hour <= 17 else 0)
            t_out = 29 + 3 * (1 if 10 <= hour <= 17 else 0) + random.gauss(0, 0.3)
            for b in self.boxes.values():
                if b.step(STEP_MS / 60_000, rh_out, t_out):
                    out.append(self._box_record(b, reason=1))          # door event, sent immediately
                rec = self._box_record(b, reason=0)
                if random.random() >= self.loss:
                    out.append(rec)
                    self.rx_ok += 1
            # fan logic = fanTask() in gateway/src/main.cpp
            for b in self.boxes.values():
                if b.fan and self.now - b.fan_since >= FAN_MAX_ON_MS:
                    drop = b.rh_at_start - b.rh
                    self._set_fan(b, 0, "timeout", out)
                    b.cooldown_until = self.now + FAN_COOLDOWN_MS
                    if drop < SILICA_MIN_DROP:
                        out.append({"node_id": "gateway", "ts": self.now, "event": "silica", "box": b.node_id,
                                    "detail": "rh_drop_too_small", "value": round(drop, 1)})
                    continue
                if self.now < b.cooldown_until:
                    continue
                if b.ai_cmd is not None and self.now - b.ai_ms < AI_CMD_TTL_MS:
                    self._set_fan(b, b.ai_cmd, "ai", out)
                elif b.rh >= RH_FAN_ON:
                    self._set_fan(b, 1, "fallback", out)
                elif b.rh <= RH_FAN_OFF:
                    self._set_fan(b, 0, "fallback", out)
            self.gw_seq += 1
            gw = {"node_id": "gateway", "seq": self.gw_seq, "ts": self.now, "temp_out": round(t_out, 1),
                  "ldr": random.randint(200, 400), "gas": random.randint(100, 140), "buffered": 0, "buf_lost": 0,
                  "uptime_s": (self.now - self.start) // 1000, "rssi": random.randint(-70, -50),
                  "heap": random.randint(180_000, 200_000), "rx_ok": self.rx_ok, "rx_dup": 0, "mqtt_connects": 1}
            gw.update({f"fan_{k}": b.fan for k, b in self.boxes.items()})
            out.append(gw)
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print only, do not connect")
    ap.add_argument("--period", type=float, default=30.0, help="real seconds per 30-s simulation step")
    ap.add_argument("--count", type=int, default=0, help="steps to run (0 = forever)")
    ap.add_argument("--host", default=netpie.HOST)
    ap.add_argument("--port", type=int, default=netpie.PORT_TLS)
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--loss", type=float, default=0.0, help="simulated ESP-NOW packet loss ratio, e.g. 0.1")
    ap.add_argument("--saturate", nargs="*", default=[], help="boxes whose silica gel is saturated")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    if a.seed is not None:
        random.seed(a.seed)

    gw = FakeGateway(loss=a.loss, saturate=a.saturate)
    client = None
    if not a.dry_run:
        def on_msg(c, u, m):
            try:
                cmd = json.loads(m.payload)
                print("[ai->fan]", cmd)
                gw.on_fan_cmd(cmd)
            except ValueError:
                pass
        client = netpie.make_client("NETPIE", a.host, a.port, tls=not a.no_tls,
                                    on_message=on_msg, subscribe=[netpie.TOPIC_FAN])
        time.sleep(1.5)

    n = 0
    try:
        while a.count == 0 or n < a.count:
            n += 1
            for rec in gw.step():
                raw = json.dumps(rec, separators=(",", ":"))
                if client:
                    client.publish(netpie.TOPIC_DATA, raw)
                    if "event" not in rec:
                        client.publish(netpie.TOPIC_SHADOW, json.dumps(netpie.flatten_for_shadow(rec)))
                print(("[dry] " if a.dry_run else "[pub] ") + raw)
            if not a.dry_run:
                time.sleep(a.period)
    except KeyboardInterrupt:
        pass
    if client:
        client.loop_stop(); client.disconnect()


if __name__ == "__main__":
    main()
