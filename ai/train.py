#!/usr/bin/env python3
"""Train + evaluate RH predictor. Time-ordered split (last 25% = test).
Reports MAE for: persistence baseline (RH stays the same), LinearRegression, RandomForest.
    python ai/train.py --data ai/data/synthetic.csv
"""
import argparse
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error
from features import make_features, HORIZON

FEATS = ["rh", "temp", "rh_out", "fan", "door", "rh_slope_5", "rh_slope_15", "door_recent_10", "gap_out"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="ai/data/synthetic.csv")
    ap.add_argument("--model", default="ai/model.joblib")
    ap.add_argument("--plot", default="ai/pred_vs_actual.png")
    a = ap.parse_args()

    df = pd.read_csv(a.data)
    f = make_features(df)
    cut = int(len(f) * 0.75)
    tr, te = f.iloc[:cut], f.iloc[cut:]
    X_tr, y_tr, X_te, y_te = tr[FEATS], tr["target"], te[FEATS], te["target"]

    res = {"persistence (baseline)": mean_absolute_error(y_te, te["rh"])}
    models = {"LinearRegression": LinearRegression(),
              "RandomForest": RandomForestRegressor(n_estimators=120, max_depth=10, min_samples_leaf=5,
                                                    n_jobs=-1, random_state=0)}
    preds = {}
    for name, m in models.items():
        m.fit(X_tr, y_tr)
        preds[name] = m.predict(X_te)
        res[name] = mean_absolute_error(y_te, preds[name])

    print(f"train rows={len(tr)} test rows={len(te)} horizon={HORIZON} min")
    for k, v in res.items():
        print(f"  MAE {k:24s}: {v:.3f} %RH")
    best = min(models, key=lambda k: res[k])
    print(f"best model: {best}  (improvement vs baseline: {100*(1-res[best]/res['persistence (baseline)']):.1f}%)")
    joblib.dump({"model": models[best], "feats": FEATS, "horizon": HORIZON, "name": best}, a.model)
    print("saved", a.model)

    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        n = 6 * 60
        plt.figure(figsize=(9, 3.5))
        plt.plot(y_te.values[:n], label="actual RH(t+10)", lw=1.5)
        plt.plot(preds[best][:n], label=f"{best} prediction", lw=1.2)
        plt.plot(te["rh"].values[:n], label="persistence", lw=1, ls="--", alpha=.7)
        plt.xlabel("minutes (test set)"); plt.ylabel("%RH"); plt.legend(); plt.tight_layout()
        plt.savefig(a.plot, dpi=130); print("saved", a.plot)
    except Exception as e:  # plotting is optional
        print("plot skipped:", e)


if __name__ == "__main__":
    main()
