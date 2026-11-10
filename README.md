# LensGuard — Smart Dry Cabinet (CEDT Embedded System Final Project 2026)

Two sealed boxes, each with an ESP32 node (DHT22 + reed switch) → ESP-NOW → ESP32 gateway (+ DS18B20, LDR, MQ-135, fan) →
MQTT/TLS → NETPIE → dashboard · Telegram alerts · Python AI predicts RH 10 min ahead and commands the fan.

```
node/      ESP32 sensor node firmware (PlatformIO)         ← skeleton, not yet run on hardware
gateway/   ESP32 gateway firmware (PlatformIO)             ← skeleton, not yet run on hardware
common/    protocol.h, packet.h shared by both
ai/        synthetic data, features, training, fan controller (runs today)
tools/     mqtt_simulator.py, telegram_test.py             (run today)
docs/      pin_map, message_format, report_outline, checkpoint_video_script, TEAM_TODO
```

## Quick start (no hardware)
```bash
pip install -r tools/requirements.txt
python tools/mqtt_simulator.py --dry-run --count 5          # see the JSON
python ai/gen_synthetic.py --days 14 --out ai/data/synthetic.csv
python ai/train.py                                          # MAE vs baseline
python ai/controller.py                                     # self-test
```
Firmware: install PlatformIO, copy `gateway/include/secrets.example.h` → `secrets.h`, then `pio run -d node` / `pio run -d gateway`.

## Honest status
- Python tools and AI pipeline: run and checked on synthetic data. **The MAE numbers are from a made-up physical model — not results for the report.**
- Firmware: written against the Arduino-ESP32 2.0.x API, **never compiled or run** (no PlatformIO here). Expect small fixes on first build.
- NETPIE publish from `mqtt_simulator.py` and the `@msg/fan` downlink topic are **not yet verified** against a live NETPIE project.
- TLS currently uses `setInsecure()` in the gateway; pin the CA before the final.

## External code / AI usage (keep updated for the report)
Libraries: PubSubClient, ArduinoJson, DHT sensor library, OneWire, DallasTemperature, paho-mqtt, scikit-learn, pandas.
AI tools: Claude was used to draft the scaffold, docs and scripts; the team must review, test and understand everything it submits.
