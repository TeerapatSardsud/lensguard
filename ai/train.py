#!/usr/bin/env python3
"""Train + evaluate the RH predictor. Time-ordered split per file (last 25 % of each file = test),
so the test data is always "the future" relative to the training data.
Reports MAE (mean, and min/max over the per-file test sets) for:
  persistence baseline (RH stays the same), LinearRegression, RandomForest.

    python ai/train.py --data ai/data/synthetic.csv
    python ai/train.py --data ai/data/train_box_A.csv ai/data/train_box_B.csv     # real data (prepare.py)
"""
import argparse
import json
import os
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from features import make_features, HORIZON, FEATS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", default=["ai/data/synthetic.csv"])
    ap.add_argument("--model", default="ai/model.joblib")
    ap.add_argument("--plot", default="ai/pred_vs_actual.png")
    ap.add_argument("--report", default="ai/train_report.json")
    a = ap.parse_args()

    trs, tes = [], []
    for path in a.data:
        f = make_features(pd.read_csv(path))
        if len(f) < 200:
            print(f"warning: {path} has only {len(f)} usable rows - collect more data")
        cut = int(len(f) * 0.75)
        trs.append(f.iloc[:cut]); tes.append(f.iloc[cut:].assign(_src=os.path.basename(path)))
    tr, te = pd.concat(trs), pd.concat(tes)
    X_tr, y_tr, X_te, y_te = tr[FEATS], tr["target"], te[FEATS], te["target"]

    models = {"LinearRegression": LinearRegression(),
              "RandomForest": RandomForestRegressor(n_estimators=120, max_depth=10, min_samples_leaf=5,
                                                    n_jobs=-1, random_state=0)}
    preds = {"persistence (baseline)": te["rh"].values}
    for name, m in models.items():
        m.fit(X_tr, y_tr)
        preds[name] = m.predict(X_te)

    print(f"files={len(a.data)} train rows={len(tr)} test rows={len(te)} horizon={HORIZON} min")
    report = {"data": a.data, "horizon_min": HORIZON, "train_rows": len(tr), "test_rows": len(te), "mae": {}}
    for name, p in preds.items():
        err = np.abs(y_te.values - p)
        per_file = [err[(te["_src"] == s).values].mean() for s in te["_src"].unique()]
        report["mae"][name] = {"mean": float(err.mean()), "min_file": float(min(per_file)),
                               "max_file": float(max(per_file)), "p90": float(np.percentile(err, 90))}
        print(f"  MAE {name:24s}: {err.mean():.3f} %RH  (per file {min(per_file):.3f}-{max(per_file):.3f}, "
              f"p90 {np.percentile(err, 90):.2f})")
    base = report["mae"]["persistence (baseline)"]["mean"]
    best = min(models, key=lambda k: report["mae"][k]["mean"])
    report["best"] = best
    print(f"best model: {best}  (improvement vs baseline: {100*(1-report['mae'][best]['mean']/base):.1f}%)")
    joblib.dump({"model": models[best], "feats": FEATS, "horizon": HORIZON, "name": best, "data": a.data}, a.model)
    with open(a.report, "w") as fh:
        json.dump(report, fh, indent=2)
    print("saved", a.model, "and", a.report)

    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        n = min(6 * 60, len(y_te))
        plt.figure(figsize=(9, 3.5))
        plt.plot(y_te.values[:n], label=f"actual RH(t+{HORIZON})", lw=1.5)
        plt.plot(preds[best][:n], label=f"{best} prediction", lw=1.2)
        plt.plot(te["rh"].values[:n], label="persistence", lw=1, ls="--", alpha=.7)
        plt.xlabel("minutes (test set)"); plt.ylabel("%RH"); plt.legend(); plt.tight_layout()
        plt.savefig(a.plot, dpi=130); print("saved", a.plot)
    except Exception as e:  # plotting is optional
        print("plot skipped:", e)


if __name__ == "__main__":
    main()
