#!/usr/bin/env python3
"""Generate SYNTHETIC 1-minute data for one box so the AI pipeline can be built before hardware.
The physics here is an assumption (see README); replace with real logged data later:
  python ai/gen_synthetic.py --days 14 --out ai/data/synthetic.csv
Real data should use the same columns: ts, temp, rh, rh_out, door, fan
"""
import argparse
import numpy as np
import pandas as pd


def simulate(days=14, seed=0):
    rng = np.random.default_rng(seed)
    n = days * 24 * 60
    t = np.arange(n)
    hour = (t / 60.0) % 24
    temp = 28 + 3 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, .15, n)
    rh_out = 72 - 10 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, .8, n)
    rh_out += np.repeat(rng.normal(0, 4, days), 24 * 60)             # day-to-day weather
    rh, fan, door_left = 50.0, 0, 0
    rows = []
    for i in range(n):
        door = 1 if door_left > 0 else 0
        if door_left > 0:
            door_left -= 1
        elif rng.random() < 0.004 * (1 if 8 <= hour[i] <= 22 else 0.2):
            door_left = int(rng.integers(1, 4))
        # baseline fan rule (what the box does without AI): on >= 60, off <= 52
        if rh >= 60: fan = 1
        elif rh <= 52: fan = 0
        d = 0.008 * (rh_out[i] - rh) + (0.7 * (rh_out[i] - rh) if door else 0)
        d += -0.10 * (rh - 35) if fan else 0.0
        rh = float(np.clip(rh + d + rng.normal(0, .12), 25, 95))
        rows.append((i * 60, temp[i], rh, rh_out[i], door, fan))
    df = pd.DataFrame(rows, columns=["ts", "temp", "rh", "rh_out", "door", "fan"])
    df[["temp", "rh", "rh_out"]] = df[["temp", "rh", "rh_out"]].round(2)
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="ai/data/synthetic.csv")
    a = ap.parse_args()
    df = simulate(a.days, a.seed)
    df.to_csv(a.out, index=False)
    print(f"wrote {len(df)} rows -> {a.out}  (RH range {df.rh.min():.1f}-{df.rh.max():.1f})")
