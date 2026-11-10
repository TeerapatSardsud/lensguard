"""Shared NETPIE helpers for the Python tools (simulator, AI service).

TLS: NETPIE's port-1884 certificate is signed by NETPIE's private "NEXPIE Device Broker" CA, which the
server does not send, and the certificate has no subjectAltName. Normal verification therefore fails,
so - exactly like the gateway firmware - we pin the SHA-256 fingerprint of the server certificate.
The check runs right after the TLS handshake, before MQTT sends the Token/Secret.
"""
import hashlib
import os
import ssl
import sys

HOST = "mqtt.netpie.io"
PORT_TLS = 1884
TOPIC_SHADOW = "@shadow/data/update"
TOPIC_DATA = "@msg/lensguard/data"
TOPIC_FAN = "@msg/lensguard/fan"
# keep in sync with NETPIE_CERT_SHA256_LIST in common/protocol.h. mqtt.netpie.io is load-balanced and
# each backend has its own certificate (both valid until 2036-09-12), so all known ones are listed.
CERT_SHA256 = {
    "91D71A005440C3DC952D7D5DBBB2B69435F93F7D474E83AA2C81B1647559C746",
    "2D56F9F3AAB9639EA9A4B64BB1DC20993213BC0A8A7DA99F44FCFE8637F09EA8",
}


class _PinnedSSLSocket(ssl.SSLSocket):
    def do_handshake(self, *args, **kwargs):
        super().do_handshake(*args, **kwargs)
        fp = hashlib.sha256(self.getpeercert(binary_form=True)).hexdigest().upper()
        if fp not in CERT_SHA256:
            self.close()
            raise ssl.SSLError(f"NETPIE certificate fingerprint mismatch ({fp}); refusing to send credentials")


def pinned_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False          # replaced by the fingerprint pin
    ctx.verify_mode = ssl.CERT_NONE     # chain is unverifiable (private CA not served)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.sslsocket_class = _PinnedSSLSocket
    return ctx


def credentials(prefix: str = "NETPIE"):
    """Read <prefix>_CLIENT_ID / _TOKEN / _SECRET from the environment (or a git-ignored .env file)."""
    load_dotenv()
    keys = [f"{prefix}_CLIENT_ID", f"{prefix}_TOKEN", f"{prefix}_SECRET"]
    vals = [os.environ.get(k) for k in keys]
    if not all(vals):
        sys.exit(f"Set {' / '.join(keys)} in the environment or in .env (see .env.example)")
    return vals


def load_dotenv(path: str = ".env"):
    """Tiny .env reader (KEY=VALUE lines) so nobody has to export variables by hand."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def make_client(prefix="NETPIE", host=HOST, port=PORT_TLS, tls=True, on_message=None, subscribe=()):
    """Connected, looping paho client. Raises on failure."""
    import paho.mqtt.client as mqtt
    cid, tok, sec = credentials(prefix)
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
    c.username_pw_set(tok, sec)
    if tls:
        c.tls_set_context(pinned_context())

    def _on_connect(client, userdata, flags, rc, props=None):
        print(f"[mqtt] connected to {host}:{port} rc={rc}")
        for t in subscribe:
            client.subscribe(t)

    c.on_connect = _on_connect
    if on_message:
        c.on_message = on_message
    c.reconnect_delay_set(1, 30)
    c.connect(host, port, keepalive=60)
    c.loop_start()
    return c


def flatten_for_shadow(rec: dict) -> dict:
    """Same transformation as publishRecord() in the gateway: {"node_id":"box_A","rh":52} ->
    {"data": {"box_A_rh": 52}}. Distinct keys per node, otherwise box_A and box_B overwrite each other."""
    p = rec.get("node_id", "unknown") + "_"
    return {"data": {p + k: v for k, v in rec.items() if k != "node_id"}}
