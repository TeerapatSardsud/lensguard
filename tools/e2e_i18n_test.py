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
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE = os.path.join(ROOT, "ai", "web", "index.html")
CHROME_HINT = os.environ.get("LENSGUARD_CHROME",
                             os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome"))

THAI = re.compile(r"[\u0E00-\u0E7F]")
STALE_MIN = 14
HIST_MIN = 26 * 60          # fixture history, so 1 h / 6 h / 24 h are all distinct


def fixture_state(range_min=360):
    """Two boxes with history, one offline, a stale banner key, one alert, one fan event.

    Mirrors ai/dashboard.py's range filter (lo = data_now//60_000 - range_min, keep minutes >= lo) because
    a fixture that ignores ?range= makes the 1h/6h/24h buttons look dead when they are not.
    """
    now = int(time.time() * 1000)
    last_rx = now - STALE_MIN * 60_000 - 30_000
    anchor = last_rx // 60_000
    lo = anchor - range_min

    def series(rh):
        return [[m * 60_000, rh + ((anchor - m) % 5) - 2, 0]
                for m in range(anchor - HIST_MIN + 1, anchor + 1) if m >= lo]

    box = lambda rh, temp: {                                  # noqa: E731
        "rh": rh, "temp": temp, "door": 0, "valid": 1, "fan": 0,
        "last_rx_ms": last_rx, "online": False,
        "door_since": None, "pred": {"pred": rh + 1.2, "fan_cmd": 0, "t": last_rx},
        "series": series(rh),
    }
    return {
        "server_now_ms": now, "data_now_ms": last_rx,
        "range_min": range_min, "banner": "", "banner_key": "stale",
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
            q = parse_qs(urlparse(self.path).query)
            try:
                rng = int(q.get("range", ["360"])[0])
            except ValueError:
                rng = 360
            st = fixture_state(rng)
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

        # 1h / 6h / 24h must actually re-fetch and redraw: a fixture that ignores ?range= (or a client
        # that never passes it) leaves the chart identical, which is what a user reports as "dead buttons"
        def range_sig():
            return page.evaluate("""(() => {
              const svg = document.getElementById('chart');
              const ticks = [...svg.querySelectorAll('text')].filter(t => /^\\d\\d:\\d\\d$/.test(t.textContent));
              const d = [...svg.querySelectorAll('path')].map(p => p.getAttribute('d') || '').join('');
              return { first: ticks[0] ? ticks[0].textContent : '', n: ticks.length,
                       verts: (d.match(/[ML]/g) || []).length,
                       pressed: [...document.querySelectorAll('#range button')]
                                 .filter(b => b.getAttribute('aria-pressed') === 'true')
                                 .map(b => b.textContent) };
            })()""")
        seen = {}
        for label, mins in (("1 ชม.", 60), ("6 ชม.", 360), ("24 ชม.", 1440)):
            page.locator(f"#range button:has-text('{label}')").click()
            page.wait_for_timeout(700)
            sig = range_sig()
            seen[mins] = sig
            check(f"range button {mins} min redraws the chart",
                  sig["pressed"] == [label] and sig["verts"] > 0,
                  f"pressed={sig['pressed']} verts={sig['verts']}")
        check("range buttons request different amounts of data",
              seen[60]["verts"] < seen[360]["verts"] < seen[1440]["verts"],
              f"1h={seen[60]['verts']} 6h={seen[360]['verts']} 24h={seen[1440]['verts']} vertices")
        check("range buttons move the chart's time axis",
              seen[60]["first"] != seen[1440]["first"],
              f"1h starts {seen[60]['first']} vs 24h {seen[1440]['first']}")
        page.locator("#range button:has-text('6 ชม.')").click()
        page.wait_for_timeout(600)

        # ---- chart interaction + long-range visualisation (external review P0/P1) ----
        chart = page.locator("#chart")
        cbox = chart.bounding_box()

        def tip_state(frac):
            page.mouse.move(cbox["x"] + cbox["width"] * frac, cbox["y"] + cbox["height"] * 0.35)
            page.wait_for_timeout(300)
            return page.evaluate("""(() => {
              const tip = document.getElementById('tip');
              const card = document.getElementById('chart').parentElement.getBoundingClientRect();
              return { left: parseFloat(tip.style.left || 'NaN'),
                       x: tip.getBoundingClientRect().left - card.left,
                       header: tip.querySelector('.t') ? tip.querySelector('.t').textContent : '' };
            })()""")
        # P0: `geom.X(t)` passed the translator function, so left became "NaNpx" and the tooltip was
        # pinned at the card's left edge for every cursor position.
        t_left, t_mid, t_right = tip_state(0.2), tip_state(0.5), tip_state(0.85)
        check("tooltip x position is a real number, not NaN",
              not any(v != v for v in (t_left["left"], t_mid["left"], t_right["left"])),
              f"lefts={[t_left['left'], t_mid['left'], t_right['left']]}")
        check("tooltip follows the cursor across the plot",
              t_left["x"] < t_mid["x"] < t_right["x"],
              f"x={t_left['x']:.0f} -> {t_mid['x']:.0f} -> {t_right['x']:.0f}")

        # P1: 24 h must aggregate (raw 1-min points are ~1.4px apart) and keep a min-max envelope
        page.locator("#range button:has-text('24 ชม.')").click()
        page.wait_for_timeout(800)
        agg24 = page.evaluate("""(() => ({
          bucket: geom.bucketMs / 60000,
          bands: [...document.querySelectorAll('#chart path[fill-opacity]')].length,
          header: (() => { const t = document.getElementById('tip');
            return t.style.display === 'block' ? (t.querySelector('.t') || {}).textContent : null; })(),
        }))()""")
        check("24 h aggregates to a legible bucket width", agg24["bucket"] >= 2, f"bucket={agg24['bucket']} min")
        check("24 h draws a min-max envelope per series", agg24["bands"] == 2, f"bands={agg24['bands']}")
        cbox = page.locator("#chart").bounding_box()
        page.mouse.move(cbox["x"] + cbox["width"] * 0.6, cbox["y"] + cbox["height"] * 0.35)
        page.wait_for_timeout(350)
        tip24 = page.locator("#tip .t").inner_text()
        check("aggregated tooltip names its bucket span and min-max",
              "–" in tip24 and ("5" in tip24 or "10" in tip24), repr(tip24))
        page.locator("#range button:has-text('1 ชม.')").click()
        page.wait_for_timeout(700)
        check("1 h keeps raw readings (no aggregation)",
              page.evaluate("geom.bucketMs") == 0, f"bucketMs={page.evaluate('geom.bucketMs')}")

        # P1: the axis anchors to now, so a dead feed leaves a visible gap instead of running to the edge
        gap = page.evaluate("""(() => {
          const labels = [...document.querySelectorAll('#chart text.gap')].map(t => t.textContent);
          const rects = [...document.querySelectorAll('#chart rect')]
            .filter(r => r.getAttribute('opacity') === '0.7').length;
          return { labels, rects };
        })()""")
        check("stale feed shades the missing interval on the chart",
              len(gap["labels"]) == 1 and gap["rects"] == 1, str(gap))
        # compare against the series' own last timestamp, not a hardcoded hour: the earlier version
        # asserted "12:" which passed in Bangkok and failed on a UTC runner at 05:46
        gap_ok = page.evaluate("""(() => {
          const label = (document.querySelector('#chart text.gap') || {}).textContent || '';
          const s = state.boxes.box_A.series;
          const last = s[s.length - 1][0];
          const hh = new Date(last).toLocaleTimeString(lang === 'th' ? 'th-TH' : 'en-GB',
            {hour: '2-digit', minute: '2-digit', hour12: false});
          return { label, hh, matches: label.includes(hh) };
        })()""")
        check("gap is labelled with the last reading time", gap_ok["matches"], str(gap_ok))

        # P1: no global dimming — stale state must stay at full contrast
        check("stale state does not dim the whole board",
              page.evaluate("getComputedStyle(document.getElementById('main')).opacity") == "1",
              page.evaluate("getComputedStyle(document.getElementById('main')).opacity"))

        # P2: the table states its row cap instead of silently truncating (needs > 240 rows, so 24 h)
        page.locator("#range button:has-text('24 ชม.')").click()
        page.wait_for_timeout(800)
        page.locator("#tableBtn").click()
        page.wait_for_timeout(400)
        tbl = page.evaluate("""(() => ({ rows: document.querySelectorAll('#tableWrap tbody tr').length,
          note: (document.querySelector('.tbl-note') || {}).textContent || null }))()""")
        check("table says when it is showing a capped subset",
              tbl["rows"] == 240 and tbl["note"] and "240" in tbl["note"], str(tbl))
        page.locator("#tableBtn").click()
        page.wait_for_timeout(200)
        # back to 6 h for the remaining (tooltip / language) checks: at 1 h the tail is empty by design,
        # so a cursor over it legitimately reports "–"
        page.locator("#range button:has-text('6 ชม.')").click()
        page.wait_for_timeout(700)

        # P1 (v2): nothing may be drawn outside the plot box — the fan bars used to extend left under the
        # "พัดลม A/B" labels (measured rect x=10.6 vs plot left 48) and the series started at x=-181
        for label in ("1 ชม.", "6 ชม.", "24 ชม."):
            page.locator(f"#range button:has-text('{label}')").click()
            page.wait_for_timeout(800)
            clip = page.evaluate("""(() => {
              const svg = document.getElementById('chart'), L = geom.m.l, R = geom.W - geom.m.r;
              const fan = [...svg.querySelectorAll('rect')].filter(r => r.getAttribute('opacity') === '0.85')
                .map(r => ({x: +r.getAttribute('x'), w: +r.getAttribute('width')}));
              const paths = [...svg.querySelectorAll('path')].map(p => { const b = p.getBBox();
                return {x: b.x, right: b.x + b.width}; });
              return {
                fanOut: fan.filter(o => o.x < L - 0.5 || o.x + o.w > R + 0.5).length,
                pathOut: paths.filter(o => o.x < L - 0.5 || o.right > R + 0.5).length,
                plot: [L, R],
              };
            })()""")
            check(f"no fan bar is drawn outside the plot at {label}",
                  clip["fanOut"] == 0, f"fanOut={clip['fanOut']} plot={clip['plot']}")
            check(f"no series path is drawn outside the plot at {label}",
                  clip["pathOut"] == 0, f"pathOut={clip['pathOut']} plot={clip['plot']}")

        # P2 (v2): aggregated points sit at the MEAN timestamp of their bucket, not at its left edge
        agg_ts = page.evaluate("""(() => {
          const B = geom.bucketMs, now = Date.now();
          const inside = geom.series[0].pts.filter(p => p[3] != null);
          const offs = inside.slice(-40).map(p => (p[0] - p[3]) / 60000);
          return { bucket: B / 60000, n: inside.length,
                   minOff: +Math.min(...offs).toFixed(2), maxOff: +Math.max(...offs).toFixed(2),
                   anyOnEdge: offs.filter(o => o === 0).length };
        })()""")
        check("aggregated points are timestamped inside their bucket, not on its edge",
              agg_ts["n"] > 0 and agg_ts["maxOff"] > 0 and agg_ts["maxOff"] <= agg_ts["bucket"] + 0.01,
              str(agg_ts))

        # P2 (v2): a missing interval must break the line/envelope instead of being bridged
        hole = page.evaluate("""(() => {
          const now = Date.now(), hs = now - 9 * 3600e3, he = now - 7 * 3600e3;
          for (const k of ['box_A', 'box_B'])
            state.boxes[k].series = state.boxes[k].series.filter(p => p[0] < hs || p[0] > he);
          render(state);
          const svg = document.getElementById('chart');
          const lines = [...svg.querySelectorAll('path[stroke-width="2"]')]
            .map(p => { const b = p.getBBox(); return {x: b.x, right: b.x + b.width}; });
          const left = geom.X(geom.x1 - 9 * 3600e3), right = geom.X(geom.x1 - 7 * 3600e3);
          return { paths: lines.length,
                   bridging: lines.filter(o => o.x < left && o.right > right).length };
        })()""")
        check("a data gap splits the series instead of being bridged",
              hole["paths"] >= 4 and hole["bridging"] == 0, str(hole))
        # restore the fixture for the checks that follow
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(700)
        page.locator("#range button:has-text('6 ชม.')").click()
        page.wait_for_timeout(700)
        page.locator("#langBtn").click()
        page.wait_for_timeout(600)
        page.locator("#langBtn").click()
        page.wait_for_timeout(500)

        page.locator("#range button:has-text('24 ชม.')").click()
        page.wait_for_timeout(800)
        check("aggregation is disclosed in the legend, not only the tooltip",
              page.locator(".legend .agg-hint").count() == 1,
              page.locator(".legend .agg-hint").inner_text() if page.locator(".legend .agg-hint").count() else "missing")
        page.locator("#range button:has-text('6 ชม.')").click()
        page.wait_for_timeout(700)

        # palette contract: semantic chart lines must outrank decorative gridlines, and every text
        # colour must clear WCAG AA. Measured on the real cascade (color-mix included), so a theme
        # tweak that dims a threshold or a label fails here instead of in a projector room.
        contrast = page.evaluate("""(() => {
          const cv = document.createElement('canvas'); cv.width = cv.height = 1;
          const ctx = cv.getContext('2d', {willReadFrequently: true});
          const toRGB = (c) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = c;
            ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2]]; };
          const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= .03928 ? v / 12.92
            : Math.pow((v + .055) / 1.055, 2.4); }; return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
          const ratio = (f, b) => { const a = lum(toRGB(f)), c = lum(toRGB(b));
            return (Math.max(a, c) + .05) / (Math.min(a, c) + .05); };
          const bgOf = (e) => { let n = e, bg = getComputedStyle(n).backgroundColor;
            while ((bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent') && n.parentElement) {
              n = n.parentElement; bg = getComputedStyle(n).backgroundColor; } return bg; };
          const cardBg = bgOf(document.querySelector('.card'));
          const g = (sel, prop) => { const e = document.querySelector(sel); return e ? getComputedStyle(e)[prop] : null; };
          const badge = document.querySelector('.state.critical');
          return {
            thr: ratio(g('#chart line[stroke-dasharray]', 'stroke'), cardBg),
            grid: ratio(g('#chart line', 'stroke'), cardBg),
            series: ratio(g('#chart path', 'stroke'), cardBg),
            badge: badge ? ratio(getComputedStyle(badge).color, bgOf(badge)) : 0,
            note: ratio(g('.gw-note', 'color'), cardBg),
            foot: ratio(g('.foot', 'color'), bgOf(document.querySelector('.foot'))),
            stale: ratio(g('.card.off .hero .num', 'color'), cardBg),
            bar: ratio(getComputedStyle(document.querySelector('.bar')).color,
                       getComputedStyle(document.querySelector('.bar')).backgroundColor),
          };
        })()""")
        check("threshold lines are more visible than gridlines",
              contrast["thr"] > contrast["grid"] * 2, f"thr={contrast['thr']:.2f} grid={contrast['grid']:.2f}")
        check("threshold lines meet 3:1 against the card",
              contrast["thr"] >= 3.0, f"{contrast['thr']:.2f}:1")
        check("chart series meet 3:1 against the card",
              contrast["series"] >= 3.0, f"{contrast['series']:.2f}:1")
        for label, val, target in (("status badge", contrast["badge"], 4.5),
                                   ("gateway note", contrast["note"], 4.5),
                                   ("footer", contrast["foot"], 4.5),
                                   ("stale headline number", contrast["stale"], 4.5),
                                   ("app bar text", contrast["bar"], 4.5)):
            check(f"{label} meets {target}:1 contrast", val >= target, f"{val:.2f}:1")

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
        m_chart = mobile.evaluate("""(() => {
          const svg = document.getElementById('chart');
          const viewW = Number(svg.getAttribute('viewBox').split(' ')[2]);
          return [...svg.querySelectorAll('text')]
            .filter(t => { const b = t.getBBox(); return b.x < -0.5 || b.x + b.width > viewW + 0.5; })
            .map(t => t.textContent);
        })()""")
        check("no chart label is clipped at 390px", not m_chart, str(m_chart))

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
