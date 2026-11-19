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
_EPOCH = pd.Timestamp("1970-01-01")


def _to_ms(dt_index):
    """DatetimeIndex -> epoch milliseconds, independent of the dtype's internal unit
    (pandas <3 stores ns, pandas >=3 stores us/ms - astype('int64')//10**6 silently lies)."""
    return (dt_index - _EPOCH) // pd.Timedelta("1ms")


def make_features(df: pd.DataFrame, horizon: int = HORIZON, with_target: bool = True) -> pd.DataFrame:
    """df: one row per minute with columns rh, temp, temp_out, door, fan. Uses only past/current values.

    Rows are re-indexed onto a gap-complete minute grid first, so a data gap (feed outage, reboot)
    makes the shift/rolling features NaN at the hole instead of silently comparing values across
    it - otherwise the first 15 minutes after any gap get slope features computed against pre-gap
    minutes, which trains and fires on garbage. The gap rows themselves drop out with dropna().
    """
    if "ts" in df.columns and len(df) > 1:
        idx = pd.DatetimeIndex(pd.to_datetime(df["ts"].astype("int64"), unit="ms"))
        g = df.drop(columns=["ts"])
        if idx.duplicated().any():                       # defensive: keep the newest row per minute
            g = g[~idx.duplicated(keep="last")]
            idx = idx[~idx.duplicated(keep="last")]
        g = g.set_index(idx).reindex(pd.date_range(idx.min(), idx.max(), freq="1min"))
        g["ts"] = _to_ms(g.index)
    else:
        g = df.copy()
    f = pd.DataFrame(index=g.index)
    f["rh"] = g["rh"]
    f["temp"] = g["temp"]
    f["temp_out"] = g["temp_out"]
    f["fan"] = g["fan"]
    f["door"] = g["door"]
    f["rh_slope_5"] = g["rh"] - g["rh"].shift(5)
    f["rh_slope_15"] = g["rh"] - g["rh"].shift(15)
    f["door_recent_10"] = g["door"].rolling(10, min_periods=1).max()
    f["temp_gap"] = g["temp_out"] - g["temp"]
    # how long the fan has been running (minutes, capped) - silica gel slows down as it loads
    run = g["fan"].groupby((g["fan"] != g["fan"].shift()).cumsum()).cumcount() + 1
    f["fan_minutes"] = np.where(g["fan"] > 0, np.minimum(run, 60), 0)
    if "ts" in g.columns:
        f["ts"] = g["ts"]
    if with_target:
        f["target"] = g["rh"].shift(-horizon)
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
    out.insert(0, "ts", _to_ms(out.index))
    return out.reset_index(drop=True)


if __name__ == "__main__":
    # regression: slope features must never be computed across a data gap
    step = 60_000
    recs = ([{"node_id": "box_A", "ts": i * step, "rh": 50.0, "temp": 30.0, "valid": 1} for i in range(40)]
            + [{"node_id": "box_A", "ts": (i + 120) * step, "rh": 70.0, "temp": 31.0, "valid": 1}
               for i in range(40)])                       # 80-minute feed gap
    f = make_features(records_to_minutes(recs, "box_A"))
    after = f[f["ts"] >= 120 * step]
    assert not after.empty, "post-gap data was dropped entirely"
    bad = after[after["rh_slope_15"] != 0.0]
    assert bad.empty, f"slope feature crosses the gap: {bad['rh_slope_15'].tolist()[:4]}"
    assert (after["rh_slope_15"].isna() == 0).all(), "NaN feature row survived dropna"
    first = after["ts"].iloc[0]
    assert first >= 120 * step, "post-gap rows missing"
    # a gap-free series must behave exactly like before (no NaN introduced)
    cont = [{"node_id": "box_A", "ts": i * step, "rh": 40.0 + 0.1 * i, "temp": 30.0, "valid": 1}
            for i in range(60)]
    f2 = make_features(records_to_minutes(cont, "box_A"))
    assert len(f2) == 60 - HORIZON - 15 or len(f2) > 30, len(f2)
    assert not f2[FEATS].isna().any().any()
    print(f"features.py selftest: gap-safe grid ok (first post-gap row at minute "
          f"{int(first // step)}, max wrong-cross-gap slope = 0) - OK")
