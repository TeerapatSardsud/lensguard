"""Fan decision from predicted RH (proposal section 1):
  forecast (or current) RH >= 55 %  -> fan ON, before the box actually gets humid
  keep running until measured RH <= 45 % -> fan OFF
The gateway adds the safety limits on top (max on-time + cool-down, see common/protocol.h).
Keep these numbers in sync with RH_FAN_ON / RH_FAN_OFF in common/protocol.h."""
ON_THRESHOLD = 55.0
OFF_THRESHOLD = 45.0


def decide_fan(rh_now: float, rh_pred: float, fan_on: bool) -> bool:
    if fan_on:
        return rh_now > OFF_THRESHOLD                 # run until it is really dry
    return max(rh_now, rh_pred) >= ON_THRESHOLD       # start early if the forecast says so


if __name__ == "__main__":
    assert decide_fan(50, 56, False) is True      # forecast crosses 55 -> start early
    assert decide_fan(50, 52, False) is False
    assert decide_fan(50, 40, True) is True       # running: keep going until measured RH <= 45
    assert decide_fan(45, 40, True) is False
    assert decide_fan(56, 50, False) is True      # already above threshold
    print("controller ok")
