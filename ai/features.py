import numpy as np
import pandas as pd

HORIZON = 10  # minutes ahead to predict


def make_features(df: pd.DataFrame, horizon: int = HORIZON, with_target: bool = True) -> pd.DataFrame:
    """Features use ONLY past/current values (no leakage). Row = 1 minute."""
    f = pd.DataFrame(index=df.index)
    f["rh"] = df["rh"]
    f["temp"] = df["temp"]
    f["rh_out"] = df["rh_out"]
    f["fan"] = df["fan"]
    f["door"] = df["door"]
    f["rh_slope_5"] = df["rh"] - df["rh"].shift(5)
    f["rh_slope_15"] = df["rh"] - df["rh"].shift(15)
    f["door_recent_10"] = df["door"].rolling(10, min_periods=1).max()
    f["gap_out"] = df["rh_out"] - df["rh"]
    if with_target:
        f["target"] = df["rh"].shift(-horizon)
    return f.dropna()
