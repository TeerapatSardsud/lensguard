"""Web dashboard served by ai/service.py (no extra process, no credentials in the browser).

    python ai/service.py                    -> open http://localhost:8080  (on the Pi: http://<pi-ip>:8080)

The service already receives every record from NETPIE, so it keeps the latest state + 1-minute RH history
per box here and serves:
    GET /              ai/web/index.html
    GET /api/state     JSON snapshot (?range=60|360|1440 minutes)
Read-only, standard library only. NETPIE (shadow + feed) stays the cloud storage (M5); this page is the
dashboard/UX layer.
"""
import json
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from alerts import RH_ALERT
from controller import OFF_THRESHOLD, ON_THRESHOLD

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
KEEP_MIN = 48 * 60          # history kept in memory (minutes)
ONLINE_S = 120              # a node is "online" if it sent something within this many seconds


class Dashboard:
    def __init__(self, boxes=("box_A", "box_B"), banner="", model_name=None):
        self.boxes, self.banner, self.model_name = boxes, banner, model_name
        self.lock = threading.Lock()
        self.latest = {}                                   # node -> last record (incl. rx_ms)
        self.fan = {b: 0 for b in boxes}
        self.door_since = {}                               # box -> data time the lid opened
        self.hist = {b: {} for b in boxes}                 # box -> {minute: [rh, temp, fan, door]}
        self.pred = {}                                     # box -> {"pred", "fan_cmd", "t"}
        self.alerts = deque(maxlen=30)
        self.events = deque(maxlen=30)                     # gateway events (fan / silica / boot)
        self.data_now = 0                                  # newest data timestamp seen (ms)
        self.started = int(time.time() * 1000)

    # ---------------------------------------------------------------- input
    def ingest(self, r):
        t = r.get("ts") or r.get("rx_ms") or int(time.time() * 1000)
        nid = r.get("node_id")
        with self.lock:
            self.data_now = max(self.data_now, t)
            if nid == "gateway":
                if r.get("event"):
                    self.events.appendleft(r)
                    if r["event"] == "fan" and r.get("box") in self.fan:
                        self.fan[r["box"]] = int(float(r.get("value", 0)))
                    return
                for b in self.boxes:
                    if f"fan_{b}" in r:
                        self.fan[b] = int(r[f"fan_{b}"])
                self.latest[nid] = r
                return
            if nid not in self.hist:
                self.latest[nid] = r                       # unknown/simulated extra node: shown in status only
                return
            prev = self.latest.get(nid, {})
            self.latest[nid] = r if r.get("valid", 1) else {**prev, **{k: r[k] for k in ("door", "rx_ms", "ts", "valid")
                                                                       if k in r}}
            if r.get("door"):
                self.door_since.setdefault(nid, t)
            else:
                self.door_since.pop(nid, None)
            if r.get("valid", 1) and r.get("rh") is not None:
                m = t // 60_000
                self.hist[nid][m] = [float(r["rh"]), r.get("temp"), self.fan[nid], int(r.get("door", 0))]
                if len(self.hist[nid]) > KEEP_MIN + 60:
                    cutoff = m - KEEP_MIN
                    self.hist[nid] = {k: v for k, v in self.hist[nid].items() if k >= cutoff}

    def on_pred(self, box, pred, fan_cmd, t):
        with self.lock:
            self.pred[box] = {"pred": round(pred, 1), "fan_cmd": int(fan_cmd), "t": t}

    def on_alert(self, kind, box, text, sent_ms):
        with self.lock:
            self.alerts.appendleft({"kind": kind, "box": box, "text": text, "sent_ms": sent_ms})

    def preload(self, path, hours=24):
        """Fill the charts from the service's own log after a restart."""
        if not os.path.exists(path):
            return 0
        since = int(time.time() * 1000) - hours * 3_600_000
        n = 0
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (r.get("rx_ms") or 0) >= since:
                    self.ingest(r); n += 1
        return n

    # ---------------------------------------------------------------- output
    def snapshot(self, range_min=360):
        now = int(time.time() * 1000)
        with self.lock:
            lo = self.data_now // 60_000 - range_min
            boxes = {}
            for b in self.boxes:
                r = self.latest.get(b, {})
                rx = r.get("rx_ms")
                boxes[b] = {
                    "rh": r.get("rh"), "temp": r.get("temp"), "door": r.get("door"), "valid": r.get("valid", 1),
                    "fan": self.fan[b], "last_rx_ms": rx, "online": bool(rx and now - rx < ONLINE_S * 1000),
                    "door_since": self.door_since.get(b), "pred": self.pred.get(b),
                    "series": [[m * 60_000, v[0], v[2]] for m, v in sorted(self.hist[b].items()) if m >= lo],
                }
            gw = dict(self.latest.get("gateway", {}))
            grx = gw.get("rx_ms")
            gw["online"] = bool(grx and now - grx < ONLINE_S * 1000)
            extra = sorted(n for n in self.latest if n not in self.boxes and n != "gateway")
            return {
                "server_now_ms": now, "data_now_ms": self.data_now, "range_min": range_min,
                "banner": self.banner, "model": self.model_name,
                "thresholds": {"fan_on": ON_THRESHOLD, "fan_off": OFF_THRESHOLD, "alert": RH_ALERT},
                "boxes": boxes, "gateway": gw, "extra_nodes": extra,
                "alerts": list(self.alerts)[:15], "events": list(self.events)[:15],
            }

    # ---------------------------------------------------------------- HTTP
    def serve(self, host="0.0.0.0", port=8080):
        dash = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):            # keep the service log clean
                pass

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path in ("/", "/index.html"):
                    with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if u.path == "/api/state":
                    try:
                        rng = int(parse_qs(u.query).get("range", ["360"])[0])
                    except ValueError:
                        rng = 360
                    rng = max(10, min(rng, KEEP_MIN))
                    return self._send(200, json.dumps(dash.snapshot(rng)).encode(), "application/json")
                self._send(404, b"not found", "text/plain")

        httpd = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
