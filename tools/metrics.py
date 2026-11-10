#!/usr/bin/env python3
"""Quantitative metrics for the report (rubric IV) from the files ai/service.py writes:
  ai/data/live.jsonl         every record received from NETPIE (+ rx_ms = arrival time at the PC)
  ai/data/predictions.jsonl  every AI prediction (made at ts, for ts + 10 min)
  ai/data/alerts.jsonl       every Telegram alert (+ ts of the record that triggered it)

    python tools/metrics.py                        # all metrics
    python tools/metrics.py --since 1762750000000  # only one test run (epoch ms)

Metrics (state the method in the report exactly like this):
  1. Packet delivery rate node -> gateway -> cloud, per node, from gaps in seq (per boot_id).
     Also first-try ratio from 'tries' (ESP-NOW retries).
  2. Latency gateway -> cloud -> PC = rx_ms - ts (both NTP-synced clocks; ~±50 ms clock error).
     Door-event latency uses only reason==1 records. Alert latency = Telegram sent - trigger ts.
  3. Uptime: longest continuous run with no gap > 3 min, per node.
  4. AI MAE on live data: prediction vs the RH actually measured 10 min later.
Each metric is printed as mean / min / max (and p95 where useful), as the rubric asks.
"""
import argparse
import json
import os
import statistics as st


def load(path, since):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if (r.get("ts") or r.get("rx_ms") or r.get("sent_ms") or 0) >= since:
                out.append(r)
    return out


def summary(xs, unit=""):
    if not xs:
        return "no data"
    xs = sorted(xs)
    p95 = xs[min(len(xs) - 1, int(0.95 * len(xs)))]
    return f"mean {st.mean(xs):.1f}{unit}  min {xs[0]:.1f}{unit}  max {xs[-1]:.1f}{unit}  p95 {p95:.1f}{unit}  (n={len(xs)})"


def delivery(recs):
    print("\n1) Packet delivery rate (seq gaps)")
    by = {}
    for r in recs:
        if "seq" in r and "event" not in r:
            by.setdefault((r["node_id"], r.get("boot", 0)), []).append(r)
    per_node = {}
    for (nid, boot), rs in by.items():
        seqs = sorted({r["seq"] for r in rs})
        expected = seqs[-1] - seqs[0] + 1
        d = per_node.setdefault(nid, {"got": 0, "exp": 0, "first": 0, "with_tries": 0})
        d["got"] += len(seqs); d["exp"] += expected
        tr = [r["tries"] for r in rs if "tries" in r]
        d["first"] += sum(t == 1 for t in tr); d["with_tries"] += len(tr)
    for nid, d in sorted(per_node.items()):
        line = f"   {nid:8s} {d['got']}/{d['exp']} = {100 * d['got'] / d['exp']:.2f}%"
        if d["with_tries"]:
            line += f"   delivered on 1st ESP-NOW try: {100 * d['first'] / d['with_tries']:.1f}%"
        print(line)


def latency(recs, alerts):
    print("\n2) Latency")
    lat = [r["rx_ms"] - r["ts"] for r in recs if r.get("ts") and r.get("rx_ms") and "event" not in r]
    print("   gateway -> NETPIE -> PC, all records :", summary(lat, " ms"))
    door = [r["rx_ms"] - r["ts"] for r in recs if r.get("reason") == 1 and r.get("ts") and r.get("rx_ms")]
    print("   door events only                    :", summary(door, " ms"))
    al = [a["sent_ms"] - a["trigger_ts"] for a in alerts if a.get("trigger_ts") and a.get("ok")]
    print("   trigger record -> Telegram sent     :", summary(al, " ms"),
          "\n   (for door latency run the service with --door-after 0 so the alert is immediate)")
    if any(x < 0 for x in lat):
        print("   WARNING: negative latencies -> gateway/PC clocks not synced (NTP) or simulator sim-clock data")


def uptime(recs, gap_ms=180_000):
    print("\n3) Uptime (longest run without a gap > 3 min)")
    by = {}
    for r in recs:
        if "event" not in r and r.get("rx_ms"):
            by.setdefault(r["node_id"], []).append(r["rx_ms"])
    for nid, ts in sorted(by.items()):
        ts.sort()
        cur_start = ts[0]; best_len = 0; gaps = 0
        for a, b in zip(ts, ts[1:]):
            if b - a > gap_ms:
                gaps += 1
                best_len = max(best_len, a - cur_start)
                cur_start = b
        if ts[-1] - cur_start > best_len:
            best_len = ts[-1] - cur_start
        print(f"   {nid:8s} longest {best_len / 3.6e6:.2f} h, total span {(ts[-1] - ts[0]) / 3.6e6:.2f} h, gaps={gaps}")


def ai_mae(recs, preds):
    print("\n4) AI MAE on live data (prediction vs RH measured 10 min later)")
    by = {}
    for r in recs:
        if r.get("rh") is not None and r.get("ts") and r.get("valid", 1):
            by.setdefault(r["node_id"], []).append((r["ts"], float(r["rh"])))
    errs, base = [], []
    for p in preds:
        series = by.get(p["node_id"])
        if not series:
            continue
        target = p["ts"] + p.get("horizon_min", 10) * 60_000
        near = min(series, key=lambda s: abs(s[0] - target))
        if abs(near[0] - target) <= 45_000:
            errs.append(abs(p["pred"] - near[1])); base.append(abs(p["rh"] - near[1]))
    print("   model       :", summary(errs, " %RH"))
    print("   persistence :", summary(base, " %RH"), "(baseline: 'RH stays the same')")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="ai/data")
    ap.add_argument("--since", type=int, default=0)
    a = ap.parse_args()
    recs = load(os.path.join(a.dir, "live.jsonl"), a.since)
    print(f"{len(recs)} records from {a.dir}/live.jsonl")
    if not recs:
        return
    delivery(recs)
    latency(recs, load(os.path.join(a.dir, "alerts.jsonl"), a.since))
    uptime(recs)
    ai_mae(recs, load(os.path.join(a.dir, "predictions.jsonl"), a.since))


if __name__ == "__main__":
    main()
