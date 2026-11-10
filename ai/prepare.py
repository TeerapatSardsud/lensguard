#!/usr/bin/env python3
"""Real logged data -> training CSVs (one per box, one row per minute).
ai/service.py logs every record it receives to ai/data/live.jsonl. After 2-3 days of real data:

    python ai/prepare.py --log ai/data/live.jsonl
    python ai/train.py --data ai/data/train_box_A.csv ai/data/train_box_B.csv

Uses the same records_to_minutes() as the live service, so training and live inputs match.
Rows produced by the simulator must not be mixed in: pass --since <epoch ms> to skip old test data.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import records_to_minutes  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", nargs="+", default=["ai/data/live.jsonl"])
    ap.add_argument("--boxes", nargs="+", default=["box_A", "box_B"])
    ap.add_argument("--since", type=int, default=0, help="ignore records with ts before this (epoch ms)")
    ap.add_argument("--out-dir", default="ai/data")
    a = ap.parse_args()

    recs = []
    for path in a.log:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (r.get("ts") or r.get("rx_ms") or 0) >= a.since:
                    recs.append(r)
    recs.sort(key=lambda r: r.get("ts") or r.get("rx_ms") or 0)
    print(f"{len(recs)} records")
    for b in a.boxes:
        df = records_to_minutes(recs, b)
        out = os.path.join(a.out_dir, f"train_{b}.csv")
        df.to_csv(out, index=False)
        hours = len(df) / 60
        print(f"{b}: {len(df)} minutes ({hours:.1f} h) -> {out}" + ("   <-- need more data (aim for 48 h+)"
                                                                  if hours < 48 else ""))


if __name__ == "__main__":
    main()
