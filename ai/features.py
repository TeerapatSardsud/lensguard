"""Feature engineering shared by training (train.py) and the live service (service.py), so the model
sees exactly the same inputs in both places (no train/serve skew).

Only signals the real system measures are used:
  box node  : rh, temp (DHT22), door (reed switch)
  gateway   : temp_out (DS18B20 outside the boxes), fan state per box
(There is no outside-RH sensor, so outside RH must NOT be a feature.)
"""
import numpy as np
import pandas as pd

HORIZON = 10  # minutes ahead to predict
FEATS = ["rh", "temp", "temp_out", "fan", "door",
         "rh_slope_5", "rh_slope_15", "door_recent_10", "temp_gap", "fan_minutes"]
MIN_HISTORY = 16  # minutes of history needed before a prediction is possible (rh_slope_15)


def make_features(df: pd.DataFrame, horizon: int = HORIZON, with_target: bool = True) -> pd.DataFrame:
    """df: one row per minute with columns rh, temp, temp_out, door, fan. Uses only past/current values."""
    f = pd.DataFrame(index=df.index)
    f["rh"] = df["rh"]
    f["temp"] = df["temp"]
    f["temp_out"] = df["temp_out"]
    f["fan"] = df["fan"]
    f["door"] = df["door"]
    f["rh_slope_5"] = df["rh"] - df["rh"].shift(5)
    f["rh_slope_15"] = df["rh"] - df["rh"].shift(15)
    f["door_recent_10"] = df["door"].rolling(10, min_periods=1).max()
    f["temp_gap"] = df["temp_out"] - df["temp"]
    # how long the fan has been running (minutes, capped) - silica gel slows down as it loads
    run = df["fan"].groupby((df["fan"] != df["fan"].shift()).cumsum()).cumcount() + 1
    f["fan_minutes"] = np.where(df["fan"] > 0, np.minimum(run, 60), 0)
    if with_target:
        f["target"] = df["rh"].shift(-horizon)
    return f.dropna()


def records_to_minutes(records, box: str) -> pd.DataFrame:
    """Turn raw records (dicts as published on @msg/lensguard/data, plus 'rx_ms' added by the logger)
    into one row per minute for one box: ts, rh, temp, temp_out, door, fan."""
    rows_box, rows_gw, rows_fan = [], [], []
    for r in records:
        t = r.get("ts") or r.get("rx_ms")
        if not t:
            continue
        nid = r.get("node_id")
        if nid == box:
            rows_box.append({"t": t, "rh": r.get("rh") if r.get("valid", 1) else None,
                             "temp": r.get("temp") if r.get("valid", 1) else None, "door": r.get("door", 0)})
        elif nid == "gateway" and r.get("event") == "fan" and r.get("box") == box:
            rows_fan.append({"t": t, "fan": float(r.get("value", 0))})
        elif nid == "gateway" and "event" not in r:
            rows_gw.append({"t": t, "temp_out": r.get("temp_out"), "fan": r.get(f"fan_{box}")})
    if not rows_box:
        return pd.DataFrame(columns=["ts", "rh", "temp", "temp_out", "door", "fan"])

    def idx(rows):
        d = pd.DataFrame(rows)
        d.index = pd.to_datetime(d.pop("t"), unit="ms")
        return d.sort_index().apply(pd.to_numeric, errors="coerce")

    b = idx(rows_box)
    out = pd.DataFrame({"rh": b["rh"].resample("1min").mean(),
                        "temp": b["temp"].resample("1min").mean(),
                        "door": b["door"].resample("1min").max()})
    fan = pd.concat([idx(rows_gw)[["fan"]] if rows_gw else pd.DataFrame(columns=["fan"]),
                     idx(rows_fan) if rows_fan else pd.DataFrame(columns=["fan"])]).sort_index()
    fan = fan["fan"].dropna()
    out["fan"] = fan.resample("1min").last().reindex(out.index).ffill() if len(fan) else 0.0
    if rows_gw:
        tout = idx(rows_gw)["temp_out"].dropna()
        out["temp_out"] = tout.resample("1min").mean().reindex(out.index).ffill(limit=10) if len(tout) else np.nan
    else:
        out["temp_out"] = np.nan
    out[["rh", "temp"]] = out[["rh", "temp"]].interpolate(limit=3, limit_area="inside")
    out["door"] = out["door"].fillna(0)
    out["fan"] = out["fan"].fillna(0)
    out["temp_out"] = out["temp_out"].fillna(out["temp"])   # gateway missing: fall back to box temp
    out = out.dropna(subset=["rh", "temp"])
    out.insert(0, "ts", (out.index.astype("int64") // 10**6).astype("int64"))
    return out.reset_index(drop=True)
