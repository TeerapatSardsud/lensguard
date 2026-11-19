#!/usr/bin/env python3
"""Dashboard i18n + stale-state test (real browser, no network, no broker).

Serves ai/web/index.html from a tiny local HTTP server with a synthetic /api/state payload, then
drives it with headless Chromium to prove the things a source-grep cannot:

  1. TH is the default language and <html lang> follows the switch
  2. switching to EN translates every visible string EXCEPT the Telegram alert text (by design:
     alerts are the record actually sent to the user, so they stay in the language they were sent in)
  3. no Thai characters remain in EN mode (outside those alert/event originals)
  4. the table, chart tooltip, threshold labels and theme pill are translated too
  5. offline boxes say "Last known <ago>" instead of a bare "Offline", and the stale banner shows
  6. the language choice survives a reload (localStorage)
  7. the page logs no JS errors

Needs playwright + Chromium on this host; skips (exit 0) when playwright is missing so CI stays green
on a plain python image. Run: python tools/e2e_i18n_test.py
"""
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE = os.path.join(ROOT, "ai", "web", "index.html")
CHROME_HINT = os.environ.get("LENSGUARD_CHROME",
                             os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"))

THAI = re.compile(r"[\u0E00-\u0E7F]")
STALE_MIN = 14


def fixture_state():
    """Two boxes with history, one offline, a stale banner key, one alert, one fan event."""
    now = int(time.time() * 1000)
    last_rx = now - STALE_MIN * 60_000 - 30_000
    box = lambda rh, temp: {                                  # noqa: E731
        "rh": rh, "temp": temp, "door": 0, "valid": 1, "fan": 0,
        "last_rx_ms": last_rx, "online": False,
        "door_since": None, "pred": {"pred": rh + 1.2, "fan_cmd": 0, "t": last_rx},
        "series": [[last_rx - i * 60_000, rh + (i % 5) - 2, 0] for i in range(120, 0, -1)],
    }
    return {
        "server_now_ms": now, "data_now_ms": last_rx,
        "range_min": 360, "banner": "", "banner_key": "stale",
        "banner_args": {"mins": int((now - last_rx) / 60_000 + 0.5), "last": "18/11 10:55"},
        "model": "RandomForest", "last_rx_ms": last_rx,
        "thresholds": {"fan_on": 55, "fan_off": 45, "alert": 60},
        "boxes": {"box_A": box(50.0, 28.2), "box_B": box(52.0, 28.4)},
        "gateway": {"online": False, "rx_ms": last_rx, "temp_out": 29.0,
                    "rssi": -66, "uptime_s": 7200, "buffered": 0, "rx_ok": 720, "rx_dup": 0},
        "extra_nodes": [], 
        "alerts": [{"kind": "rh_high", "box": "box_B", "text": "⚠️ LensGuard: กล่อง B ความชื้นสูง 61.0%",
                    "sent_ms": last_rx - 3 * 60_000}],
        "events": [{"event": "fan", "box": "box_B", "value": 0, "detail": "ai", "ts": last_rx - 60_000}],
    }


FLAGS = {"empty_alerts": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        if self.path.startswith("/api/state"):
            st = fixture_state()
            if FLAGS["empty_alerts"]:                  # the "oversized empty panel" case
                st["alerts"] = []
                st["events"] = [
                    {"event": "fan", "box": "box_B", "value": 0, "detail": "fallback",
                     "ts": st["data_now_ms"] - 60_000},
                    {"event": "fan", "box": "box_A", "value": 1, "detail": "ai",
                     "ts": st["data_now_ms"] - 6 * 60_000},
                    {"event": "boot", "box": None, "value": None, "detail": "2026-11-18 09:14",
                     "ts": st["data_now_ms"] - 9 * 60_000},
                ]
            body, ctype = json.dumps(st).encode(), "application/json"
        else:
            FLAGS["empty_alerts"] = "lists=alerts_empty" in self.path
            with open(PAGE, "rb") as f:
                body, ctype = f.read(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed (pip install playwright && playwright install chromium)")
        return 0

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"

    with sync_playwright() as p:
        launch = {"headless": True, "args": ["--no-sandbox"]}
        if os.path.exists(CHROME_HINT):
            browser = p.chromium.launch(executable_path=CHROME_HINT, **launch)
        else:
            browser = p.chromium.launch(**launch)          # playwright's own download
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(700)

        body_th = page.locator("body").inner_text()
        check("TH is the default language", page.locator("html").get_attribute("lang") == "th")
        check("TH renders Thai section titles", "ภาพรวม" in body_th and "เหตุการณ์" in body_th)
        check("TH subtitle translated", "ตู้ควบคุมความชื้นอัจฉริยะ" in body_th)
        check("TH box + gateway names translated",
              "กล่อง A" in body_th and "กล่อง B" in body_th and "เกตเวย์" in body_th,
              "missing Thai box/gateway label")
        check("stale banner appears with duration + timestamp",
              "ไม่ได้รับข้อมูลใหม่มา" in body_th and "10:55" in body_th, body_th[:80])
        box_status = page.locator("#cards article").first.locator(".status").inner_text()
        check("offline box reads as last-known, not bare Offline",
              "ค่าล่าสุด" in box_status, repr(box_status))

        # tooltip must render two series rows (a `t` shadowing bug used to leave it one line)
        box = page.locator("#chart").bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.80, box["y"] + box["height"] * 0.4)
        page.wait_for_timeout(300)
        tip_th = page.locator("#tip").inner_text()
        check("chart tooltip lists both boxes",
              tip_th.count("\n") >= 3 and "กล่อง A" in tip_th and "กล่อง B" in tip_th, repr(tip_th))
        check("chart tooltip fan state is translated", "พัดลม" in tip_th, repr(tip_th))

        page.locator("#tableBtn").click()
        page.wait_for_timeout(200)
        th_headers = page.locator("th").all_inner_texts()
        check("table headers translated (TH)", th_headers[0] == "เวลา" and th_headers[3] == "พัดลม A",
              str(th_headers))
        page.locator("#tableBtn").click()

        page.locator("#langBtn").click()
        page.wait_for_timeout(600)
        body_en = page.locator("body").inner_text()
        check("switch flips <html lang> to en", page.locator("html").get_attribute("lang") == "en")
        check("EN pill shows the active language", page.locator("#langVal").inner_text() == "EN")
        check("lang button aria-label is English in EN mode",
              not THAI.search(page.locator("#langBtn").get_attribute("aria-label") or ""),
              repr(page.locator("#langBtn").get_attribute("aria-label")))
        check("theme label translated", "ธีม" not in page.locator("#theme").inner_text(),
              page.locator("#theme").inner_text().replace("\n", " "))
        check("section titles translated", all(k in body_en for k in ("Overview", "Relative humidity", "Alerts", "Events")))
        check("stat labels translated", all(k in body_en for k in ("Temperature", "Door", "Fan")))
        check("gateway rows translated", "Ambient temperature" in body_en and "Queued readings" in body_en)
        en_wo_alert = body_en.replace("กล่อง B ความชื้นสูง 61.0%", "")
        check("EN box names are Latin", "Box A" in body_en and "กล่อง" not in en_wo_alert)
        check("stale banner translated", "No new data for" in body_en and "fallback rule" in body_en)
        check("offline box translated to last-known", "Last known" in body_en)
        check("stale forecast is not labelled as a fresh prediction",
              "Last AI forecast" in body_en and "AI forecast (next 10 min)" not in body_en)
        check("offline gateway shows how old its values are",
              "Offline · last known" in body_en)
        gw_rows = page.locator("#cards .gw .row").all_inner_texts()
        check("offline gateway qualifies the uptime row (frozen, not a live counter)",
              any(r.startswith("Uptime at last contact") for r in gw_rows), str(gw_rows))
        gw_note = page.locator("#cards .gw .gw-note")
        check("offline gateway explains that Wi-Fi/uptime are last-known readings",
              gw_note.count() == 1 and "not live" in gw_note.inner_text(), gw_note.inner_text()
              if gw_note.count() else "missing .gw-note")

        # every Thai character left in EN mode must come from the stored alert/event originals
        stripped = body_en.replace("กล่อง B ความชื้นสูง 61.0%", "")
        check("no hardcoded Thai left in EN mode", not THAI.search(stripped),
              repr(THAI.search(stripped).group(0) if THAI.search(stripped) else ""))

        page.mouse.move(box["x"] + box["width"] * 0.80, box["y"] + box["height"] * 0.4)
        page.wait_for_timeout(300)
        tip_en = page.locator("#tip").inner_text()
        check("chart tooltip translated", "Fan" in tip_en and "พัดลม" not in tip_en, repr(tip_en))
        page.locator("#tableBtn").click()
        page.wait_for_timeout(200)
        en_headers = page.locator("th").all_inner_texts()
        check("table headers translated (EN)", en_headers[0] == "Time" and en_headers[3] == "Fan A",
              str(en_headers))
        page.locator("#tableBtn").click()

        page.reload(wait_until="networkidle")
        page.wait_for_timeout(600)
        check("language choice survives reload",
              page.locator("#langVal").inner_text() == "EN"
              and page.locator("html").get_attribute("lang") == "en")

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        mobile.goto(url, wait_until="networkidle")
        mobile.wait_for_timeout(600)
        overflow = mobile.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("no horizontal overflow at 390px", overflow <= 0, f"overflow={overflow}px")

        # an empty panel must hug its own content, not stretch to the taller neighbour, and its
        # empty state is one icon + one label (the old markup emitted empty placeholder spans)
        empty = browser.new_page(viewport={"width": 1440, "height": 1000})
        empty.goto(url + "?lists=alerts_empty", wait_until="networkidle")
        empty.wait_for_timeout(700)
        hs = empty.evaluate("""(() => {
          const [al, ev] = document.querySelectorAll('.grid2 > section');
          return {alerts: al.getBoundingClientRect().height, events: ev.getBoundingClientRect().height};
        })()""")
        check("empty Alerts panel hugs its content instead of matching Events",
              hs["alerts"] < hs["events"] - 20, f"alerts={hs['alerts']:.0f} events={hs['events']:.0f}")
        row = empty.locator("#alerts li.empty")
        check("empty list renders one icon + one label",
              row.count() == 1 and row.locator("svg").count() == 1 and row.locator("span").count() == 1,
              row.inner_text() if row.count() else "no li.empty")
        empty.close()

        fan_rows = page.locator("#cards article").first.locator(".ai .row").all_inner_texts()
        check("fan explanation matches current state (OFF here)",
              not any("ON" in r for r in fan_rows), str(fan_rows))
        check("no JS errors", not errors, str(errors[:3]))
        browser.close()

    httpd.shutdown()
    print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
