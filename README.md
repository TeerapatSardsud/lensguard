# LensGuard — Smart Dry Cabinet (CEDT Embedded System Final Project 2026, group TuaKorm)

Two sealed boxes, each with an ESP32 node (DHT22 + reed switch) → ESP-NOW (ACK/retry) → ESP32 gateway
(+ DS18B20, LDR, MQ-135, one fan per box via MOSFET) → MQTT/TLS → NETPIE → dashboard.
A Python AI service predicts each box's RH 10 min ahead, sends fan commands back, and sends Telegram alerts.
If the AI/cloud is down the gateway runs its own 55 %/45 % rule (failsafe); the fan never runs > 30 min (safety).

```
node/      ESP32 sensor node firmware (PlatformIO, envs box_A / box_B)    compiles; not yet run on hardware
gateway/   ESP32 gateway firmware (PlatformIO, envs gateway / gateway_ota)  compiles; not yet run on hardware
common/    protocol.h (all shared constants), packet.h (ESP-NOW layout)
ai/        features, training, controller, service.py (closed loop + alerts + logger), prepare.py
tools/     mqtt_simulator.py (fake gateway), telegram_test.py, metrics.py, netpie.py (TLS pinning helper)
docs/      message_format, pin_map, test_plan, report_outline, checkpoint_video_script, TEAM_TODO
material/  proposal, course brief, 3D model
```

## Quick start (no hardware, no accounts)
```bash
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt    # then use .venv/bin/python
python tools/mqtt_simulator.py --dry-run --count 5        # the exact JSON the gateway publishes
python ai/gen_synthetic.py --days 14 --out ai/data/synthetic.csv
python ai/train.py                                        # MAE vs persistence baseline
python ai/controller.py                                   # unit test of the fan rule
python ai/service.py --selftest                           # whole closed loop offline: sim → AI → fan → alerts
```

## With NETPIE + Telegram (checkpoint video)
```bash
cp .env.example .env            # fill in 2 NETPIE devices (gateway + AI) and the Telegram bot
python tools/mqtt_simulator.py --period 2                 # terminal 1: fake gateway → NETPIE (sim clock 15x)
python ai/service.py                                      # terminal 2: AI + alerts + logging
```
Later the real gateway replaces the simulator; nothing else changes.

## Firmware
```bash
cp gateway/include/secrets.example.h gateway/include/secrets.h     # fill in Wi-Fi, NETPIE, OTA password
pio run -d node -e box_A -t upload       # and -e box_B for the second box
pio run -d gateway -e gateway -t upload  # USB;   -e gateway_ota for over-the-air (E6)
pio device monitor -b 115200
```
`SIMULATE_SENSOR=1` (default in both `platformio.ini`) fakes the sensors so ESP-NOW/MQTT can be tested on bare boards.
PlatformIO is already installed with the VS Code extension: `~/.platformio/penv/bin/pio`.

## Honest status (2026-11-10)
- **Compiles**: node (box_A, box_B) and gateway (gateway, gateway_ota), with both `SIMULATE_SENSOR=1` and `=0`.
  Never flashed to a board yet.
- **Tested offline**: Python pipeline, AI service closed loop (`--selftest`), prepare → train → metrics on simulated logs.
- **Tested online**: TLS certificate pinning against the real mqtt.netpie.io (10/10 handshakes).
- **Not yet verified**: publishing/subscribing with real NETPIE credentials (topic permissions, `@msg` delivery between
  two devices of one group, feed storage of shadow fields), real Telegram bot, OTA on a board.
- **AI numbers are from synthetic/simulated data — not results for the report.** Retrain on ≥ 48 h of real logs.

## External code / AI usage (keep updated for the report)
Libraries: PubSubClient, ArduinoJson, DHT sensor library, OneWire, DallasTemperature, ArduinoOTA (core),
paho-mqtt, scikit-learn, pandas, numpy, joblib, matplotlib.
AI tools: Claude (Claude Code) was used to draft the scaffold, firmware, Python tools and docs; every member must
review, test and be able to explain the parts they own (course rule 4.6).
