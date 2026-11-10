"""Alert rules + Telegram sender (stdlib only). Rules (proposal section 1):
  rh_high      box RH >= RH_ALERT for 2 readings in a row
  door_open    lid open longer than door_after_s (0 = alert immediately; used to measure latency)
  node_silent  no record from a box / the gateway for SILENT_S  (+ a "back online" message)
  reboot       gateway restarted because of a crash/watchdog/brown-out (not power-on, not OTA)
  silica       gateway reports the fan ran a full cycle but RH barely dropped -> regenerate the gel
Each alert type is rate-limited per box (COOLDOWN_S) so the phone is not spammed.
"""
import json
import os
import time
import urllib.parse
import urllib.request

RH_ALERT = 60.0
SILENT_S = 180
COOLDOWN_S = 30 * 60


class Telegram:
    def __init__(self, token=None, chat_id=None, dry_run=False):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.dry_run = dry_run or not (self.token and self.chat_id)
        if self.dry_run and not dry_run:
            print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -> alerts are only printed")

    def send(self, text: str) -> bool:
        if self.dry_run:
            print("[telegram:dry]", text.replace("\n", " | "))
            return True
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=10) as r:
                return json.load(r).get("ok", False)
        except Exception as e:  # network down: log and carry on, the control loop must not die
            print("[telegram] send failed:", e)
            return False


class AlertEngine:
    def __init__(self, sender, boxes=("box_A", "box_B"), door_after_s=120, log_path=None):
        self.sender, self.boxes, self.door_after_s, self.log_path = sender, boxes, door_after_s, log_path
        self.last_sent = {}                       # (kind, box) -> wall time
        self.high_count = {b: 0 for b in boxes}
        self.door_since = {}                      # box -> (wall time opened, record ts)
        self.door_alerted = set()
        self.last_seen = {}                       # node -> wall time
        self.silent = set()

    def _fire(self, kind, box, text, trigger_ts=None, force=False):
        now = time.time()
        if not force and now - self.last_sent.get((kind, box), 0) < COOLDOWN_S:
            return
        self.last_sent[(kind, box)] = now
        ok = self.sender.send(text)
        if self.log_path:                         # used by tools/metrics.py for the alert-latency metric
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"kind": kind, "box": box, "sent_ms": int(now * 1000),
                                    "trigger_ts": trigger_ts, "ok": ok}) + "\n")

    def on_record(self, r, rh_pred=None):
        nid, now = r.get("node_id"), time.time()
        self.last_seen[nid] = now
        if nid in self.silent:
            self.silent.discard(nid)
            self._fire("online", nid, f"✅ LensGuard: {nid} กลับมาส่งข้อมูลแล้ว", force=True)
        if nid == "gateway" and r.get("event") == "boot":
            why = r.get("detail", "?")
            if why in ("PANIC", "TASK_WDT", "INT_WDT", "WDT", "BROWNOUT"):
                self._fire("reboot", nid, f"🔄 LensGuard: gateway รีสตาร์ทตัวเอง (สาเหตุ {why}) "
                                          "— ระบบกลับมาทำงานแล้ว ถ้าเกิดบ่อยแจ้งคนดูแล gateway", trigger_ts=r.get("ts"))
            return
        if nid == "gateway" and r.get("event") == "silica":
            b, drop = r.get("box", "?"), float(r.get("value", 0))
            change = f"ลดลงแค่ {drop:.1f}%" if drop >= 0 else f"กลับเพิ่มขึ้น {-drop:.1f}%"
            self._fire("silica", b, f"🧂 LensGuard: {b} พัดลมทำงานครบ 30 นาทีแล้ว แต่ความชื้น{change} "
                                    "— ซิลิกาเจลน่าจะอิ่มตัว กรุณาเปลี่ยนหรืออบใหม่", trigger_ts=r.get("ts"))
            return
        if nid not in self.boxes:
            return
        if r.get("valid", 1) and r.get("rh") is not None:
            rh = float(r["rh"])
            self.high_count[nid] = self.high_count[nid] + 1 if rh >= RH_ALERT else 0
            if self.high_count[nid] >= 2:
                msg = f"⚠️ LensGuard: {nid} ความชื้น {rh:.0f}% (เกิน {RH_ALERT:.0f}%)"
                if rh_pred is not None:
                    msg += f"\nAI คาดว่าอีก 10 นาที ≈ {rh_pred:.0f}%"
                self._fire("rh_high", nid, msg + "\nพัดลมกำลังทำงาน / กรุณาตรวจสอบกล่อง", trigger_ts=r.get("ts"))
        if r.get("door"):
            self.door_since.setdefault(nid, (now, r.get("ts")))
        else:
            self.door_since.pop(nid, None)
            self.door_alerted.discard(nid)
        self.tick()

    def tick(self):
        now = time.time()
        for b, (since, ts) in list(self.door_since.items()):
            if b not in self.door_alerted and now - since >= self.door_after_s:
                self.door_alerted.add(b)
                mins = (now - since) / 60
                self._fire("door_open", b, f"🚪 LensGuard: ฝา {b} เปิดค้างไว้"
                                           + (f" {mins:.0f} นาที" if mins >= 1 else "") + " — กรุณาปิดให้สนิท",
                           trigger_ts=ts, force=self.door_after_s == 0)
        for nid in list(self.boxes) + ["gateway"]:
            seen = self.last_seen.get(nid)
            if seen and nid not in self.silent and now - seen > SILENT_S:
                self.silent.add(nid)
                self._fire("silent", nid, f"📡 LensGuard: ไม่ได้รับข้อมูลจาก {nid} มา {int(now - seen)} วินาที "
                                          "— ตรวจสอบไฟ/Wi-Fi/บอร์ด", force=True)
