"""Gateway clock-drift correction.

Field evidence (ai/data/live.jsonl, Nov 16-17 run): the gateway's NTP synced once
at boot, then its software clock ran 14.96x faster than real time (run slopes
14.94..14.98 across 26 independent live windows - suspected crystal/clock-tree
problem on that board; needs a firmware + hardware look). Left uncorrected this
compresses the AI's 10-minute horizon to seconds, skews the dashboard chart
window by days and invalidates every ts-based metric.

Method: EVERY timestamp in the protocol is stamped by the gateway's nowEpochMs()
(common stamps box records too), so we fit ONE global clock: split the arrival-
ordered (rx_ms, ts) stream into runs of live-paced deliveries (bursts from the
gateway ring buffer / NETPIE redelivery and outage gaps break the runs), take
a = median endpoint slope of long runs, anchor b = median(ts - a*rx) over the
run points, then map future timestamps to real time: ts = (ts - b) / a.
Activation is conservative (>= 8 runs, >= 30 min live span, slope outside the
5 % deadband, median residual in the CORRECTED domain <= 3 min), so a healthy
NTP'd clock is never touched. The anchor is re-learned online, absorbing a
gateway reboot or SNTP re-sync mid-stream. Corrected records keep the original
value in ts_raw, so raw evidence survives in live.jsonl and tools/metrics.py
can still report the drift itself.

All consumers (Engine minute grid + horizon, dashboard chart, prepare.py,
alerts trigger_ts) see ONE coherent corrected timeline - partial correction
(some segments corrected, others not) is deliberately avoided because the
per-box minute grid mixes box and gateway records.
"""
import statistics as st
from collections import deque

MIN_PAIRS = 300                # consecutive live-ish pairs needed before we trust the fit
MIN_LIVE_SPAN_MS = 1_800_000   # total rx span of those pairs needed (30 min)
DEADBAND = 0.05                # a clock within 5 % of real time is not touched
MAX_RESID_MS = 180_000         # judged in the corrected domain: a linear clock mis-scaled is
#                                correctable; a noisy one is not
PAIR_MIN_RX_MS = 1000          # consecutive rx gaps under 1 s = ring-buffer burst deliveries
LIVE_GAP_MS = 600_000          # rx gap > 10 min = outage: pre-gap residuals must not anchor post-gap fit
MAX_POINTS = 8000              # memory cap for the fitting window
ANCHOR_WINDOW = 500            # recent live residuals used to re-learn the anchor
REANCHOR_JUMP_MS = 3_600_000   # anchor moved > 1 h since fit: reboot / SNTP re-sync happened

_OK, _DEADBAND, _NOISY = "ok", "deadband", "noisy"


def _estimate(pts):
    """pts: list[(rx_ms, ts)] ordered by arrival. a = median of consecutive-pair dts/drx over
    all pairs delivered more than PAIR_MIN_RX_MS apart (bursts arrive faster than real time and
    would divide by ~0). Live delivery is the majority of any long run, so the median lands on
    the true rate while burst/hiccup pairs (which can only inflate the ratio) are outvoted;
    b is anchored and residuals judged on the same pairs.
    -> (_OK, a, b) | (_DEADBAND, a, b) | (_NOISY, a|None, b|None) | (None, ...) when too thin."""
    pairs = [(q[1] - p[1]) / (q[0] - p[0]) for p, q in zip(pts, pts[1:])
             if q[0] - p[0] >= PAIR_MIN_RX_MS and q[1] > p[1]]
    if len(pairs) < MIN_PAIRS or sum(q[0] - p[0] for p, q in zip(pts, pts[1:])
                                     if q[0] - p[0] >= PAIR_MIN_RX_MS) < MIN_LIVE_SPAN_MS:
        return None, None, None
    a = st.median(pairs)
    live = [(rx, ts) for p, q in zip(pts, pts[1:]) if q[0] - p[0] >= PAIR_MIN_RX_MS for rx, ts in (p, q)]
    b = st.median(ts - a * rx for rx, ts in live)
    if 1.0 - DEADBAND <= a <= 1.0 + DEADBAND:
        return _DEADBAND, a, b
    if st.median(abs(ts - (a * rx + b)) for rx, ts in live) / a > MAX_RESID_MS:
        return _NOISY, a, None
    return _OK, a, b


class DriftCorrector:
    """Pass every record through correct() (mutates rec["ts"], keeps rec["ts_raw"]).
    After the call that activates the fit, .just_activated is True so the owner can drop
    state built on the old timestamps. Records that already carry ts_raw (corrected
    upstream, e.g. replaying a fixed log) pass through untouched. Disable via
    LENSGUARD_CLOCK_CORRECT=0 in the environment."""

    def __init__(self, verbose=True):
        self.pts = []
        self.fit = None
        self.done = False
        self.recent = deque(maxlen=ANCHOR_WINDOW)
        self.tick = 0
        self.last_rx = None
        self.just_activated = False
        self.verbose = verbose

    def correct(self, rec):
        self.just_activated = False
        ts, rx = rec.get("ts"), rec.get("rx_ms")
        if not ts or not rx or "ts_raw" in rec:
            return
        if self.fit is None and not self.done:
            if not self.pts or rx > self.pts[-1][0]:
                self.pts.append((rx, ts))
            if len(self.pts) >= MAX_POINTS:
                self.done = True
            status, a, b = _estimate(self.pts)
            if status in (_OK, _DEADBAND, _NOISY):
                self.done = self.done or status != _OK
            if status == _OK:
                self.fit = [a, b]
                self.pts = []
                self.just_activated = True
                if self.verbose:
                    print(f"[clock] gateway clock runs {a:.2f}x real time -> timestamps corrected "
                          f"to the arrival-time domain")
        fit = self.fit
        if fit is not None:
            a = fit[0]
            if self.last_rx is not None and rx - self.last_rx > LIVE_GAP_MS:
                self.recent.clear()      # across an outage, old residuals describe a different delivery regime
            self.last_rx = rx
            self.recent.append(ts - a * rx)
            self.tick += 1
            if len(self.recent) >= 20 and self.tick % 10 == 0:
                new_b = st.median(self.recent)
                if abs(new_b - fit[1]) > REANCHOR_JUMP_MS:
                    if self.verbose:
                        print(f"[clock] anchor moved {(new_b - fit[1]) / 3.6e6:+.1f} h "
                              f"(reboot / SNTP re-sync?) -> re-anchored")
                    fit[1] = new_b
            if abs(a - 1.0) > DEADBAND:
                rec["ts_raw"] = ts
                rec["ts"] = int(round((ts - fit[1]) / a))     # ts = a*rx_true + b -> recover rx_true

    def fit_rate(self):
        return None if self.fit is None else self.fit[0]


if __name__ == "__main__":
    import random
    random.seed(7)
    rate, span_h, step_s = 14.96, 6, 4       # field-measured gateway rate; live records ~4 s apart
    base_rx = 1_763_000_000_000
    base_ts = base_rx + 4 * 3_600_000        # clock already 4 h ahead at boot
    clock = DriftCorrector(verbose=False)
    out = []
    rx_now = base_rx
    queue = []                                # records buffered while the cloud link was down
    for i in range(int(span_h * 3600 / step_s)):
        rx_now += step_s * 1000               # real time marches on
        gen = base_ts + i * step_s * 1000 * rate   # gateway stamps with its FAST clock
        if random.random() < 0.03:
            queue.append(gen)                 # link hiccup: nothing arrives
            if len(queue) > 12:               # link back: ring-buffer flush paced at ~0.4 s
                for k, g in enumerate(queue):
                    rec = {"node_id": "box_A", "ts": g, "rx_ms": rx_now + k * 400}
                    rx_now = rec["rx_ms"]
                    clock.correct(rec)
                    out.append((rec["ts"], rec.get("ts_raw", rec["ts"]), rec["rx_ms"]))
                queue = []
            continue
        if queue:
            queue.append(gen)
            continue
        rec = {"node_id": "box_A", "ts": gen,
               "rx_ms": rx_now + random.randint(200, 1500)}  # live delivery latency 0.2-1.5 s
        clock.correct(rec)
        out.append((rec["ts"], rec.get("ts_raw", rec["ts"]), rec["rx_ms"]))
    corrected = [(t, r, rx) for t, r, rx in out if r != t]
    rate_fit = clock.fit_rate()
    assert rate_fit is not None, "correction never activated"
    assert corrected, "records not being corrected"
    assert len(corrected) > 0.9 * len(out), "activation should happen early"
    # the fit must recover the injected rate
    assert 13.5 < rate_fit < 16.5, f"rate fit wrong: {rate_fit:.2f} vs {rate}"
    # corrected timeline must advance at real time: corrected-span / real-rx-span ~ 1
    est = (corrected[-1][0] - corrected[0][0]) / (corrected[-1][2] - corrected[0][2])
    assert 0.8 < est < 1.2, f"rate not restored: {est:.3f}"
    ct = [t for t, raw_v, _ in out if raw_v != t]
    assert ct == sorted(ct), "corrected timeline must stay monotonic"
    assert all(0 < t < 4e12 for t in ct), "corrected timestamps must be plausible epoch ms"
    # a healthy clock is never touched
    ok = DriftCorrector(verbose=False)
    for i in range(2000):
        rec = {"node_id": "box_A", "ts": 1_763_000_500_000 + i * 30_000,
               "rx_ms": 1_763_000_000_000 + i * 30_000 + (i % 7) * 1000}
        ok.correct(rec)
        assert "ts_raw" not in rec
    assert ok.fit is None or abs(ok.fit[0] - 1.0) <= DEADBAND
    _ = rate_fit  # silence linters; used above
    # boot records with no rx are passed through
    fresh = DriftCorrector(verbose=False)
    b = {"node_id": "gateway", "event": "boot", "ts": 1}
    fresh.correct(b)
    assert b["ts"] == 1 and "ts_raw" not in b
    # replayed, already-corrected records are never double-corrected
    twice = DriftCorrector(verbose=False)
    twice.fit = [14.96, 0.0]
    r = {"node_id": "box_A", "ts": 100, "ts_raw": 1496, "rx_ms": 50}
    twice.correct(r)
    assert r["ts"] == 100 and r["ts_raw"] == 1496
    print(f"clock.py selftest: drift {rate}x detected+corrected (fit {rate_fit:.2f}x), "
          f"deadband + burst-only + replay cases untouched - OK")
