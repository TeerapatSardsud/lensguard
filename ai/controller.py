"""Fan decision from predicted RH. Hysteresis avoids rapid on/off flapping.
Same thresholds as the gateway fallback (60/55) so behaviour is consistent."""
ON_THRESHOLD = 60.0
OFF_THRESHOLD = 55.0


def decide_fan(rh_now: float, rh_pred: float, fan_on: bool) -> bool:
    risk = max(rh_now, rh_pred)            # act early if the forecast is higher than now
    if risk >= ON_THRESHOLD:
        return True
    if risk <= OFF_THRESHOLD:
        return False
    return fan_on


if __name__ == "__main__":
    assert decide_fan(50, 62, False) is True      # forecast says it will exceed -> fan on early
    assert decide_fan(57, 58, True) is True       # in the band: keep state
    assert decide_fan(57, 58, False) is False
    assert decide_fan(52, 53, True) is False
    print("controller ok")
