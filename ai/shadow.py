"""NETPIE shadow-poll input source (fast path for gateways that never publish @msg).

Some gateway builds only PUT their flat keys to @shadow/data/update (visible in the NETPIE
console 'feed'), and the project's Msg service may even reject @msg publishes with rc=4.
This source reads the device shadow over the Device API with plain HTTPS:

    GET https://api.netpie.io/v2/device/shadow/data   Authorization: Device <ClientID>:<Token>

It emits (record, rx_ms) tuples shaped like @msg/lensguard/data rows whenever a node's seq
advances or the shadow rev bumps (gateway), so Engine/alerts/dashboard/metrics work unchanged.
No MQTT connection is made at all - crucially this never occupies the gateway's ClientID, so
the real gateway stays online while the AI service runs.

ts is the shadow timestamp, i.e. the gateway's own clock: honest raw evidence, which
DriftCorrector later rebases against rx_ms (our clock, stamped at fetch time).
"""
import base64
import json
import time
import urllib.request

API = "https://api.netpie.io/v2/device/shadow/data"
BOXES = ("box_A", "box_B")


class ShadowSource:
    def __init__(self, client_id: str, token: str, timeout: float = 10.0):
        if not client_id or not token:
            raise SystemExit("shadow mode needs NETPIE_CLIENT_ID / NETPIE_TOKEN in .env")
        self.auth = "Basic " + base64.b64encode(f"{client_id}:{token}".encode()).decode()
        self.timeout = timeout
        self.rev = None
        self.seq = {b: None for b in BOXES}

    def fetch(self) -> dict:
        req = urllib.request.Request(API, headers={"Authorization": self.auth})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def poll(self) -> list:
        """One fetch -> list of (record, rx_ms). Empty list = nothing new / unreachable."""
        rx = int(time.time() * 1000)
        try:
            d = self.fetch()
        except Exception as e:
            print(f"[shadow] fetch failed: {type(e).__name__} {e}")
            return []
        data, rev = d.get("data") or {}, d.get("rev")
        ts = d.get("timestamp") or rx
        out = []
        for b in BOXES:
            s = data.get(f"{b}_seq")
            if s is not None and s != self.seq[b]:
                self.seq[b] = s
                rec = {"node_id": b, "seq": s, "ts": ts,
                       "temp": data.get(f"{b}_temp"), "rh": data.get(f"{b}_rh"),
                       "door": data.get(f"{b}_door", 0), "valid": data.get(f"{b}_valid", 1)}
                out.append((rec, rx))
        if rev is not None and rev != self.rev:
            self.rev = rev
            out.append(({"node_id": "gateway", "ts": ts, "uptime_s": data.get("gw_uptime_s")}, rx))
        return out
