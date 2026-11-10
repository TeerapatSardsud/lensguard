# Final report outline (≤ 10 pages) — follow the 6 rubric parts of the course
(Fill the real numbers after testing. Keep this file as the single source for the report.)

| Part | What to write | Evidence to collect |
|---|---|---|
| 1. Problem & objective | Humidity/fungus risk for lenses in Thai climate; who has the problem; target RH range; sources [1]–[6] from the proposal | the cited sources |
| 2. System design | Block diagram, hardware list, pin map (`docs/pin_map.md`), message format, why ESP-NOW + gateway, why MQTT/TLS | diagram, photos of wiring |
| 3. Implementation | Firmware structure (tasks/queue/ISR), ACK+retry, offline buffer, cloud/dashboard, AI model + features, fan control, Telegram | code snippets (short), screenshots |
| 4. Testing & results | Packet delivery rate, retry count, latency; AI MAE vs baseline **on real data**; RH over time with/without fan; door-open events; offline recovery test | CSV logs, plots, tables |
| 5. Discussion | Limits (sensor accuracy, small box, short data), what failed and how it was fixed, what you would change | honest failure log |
| 6. Contribution & AI usage | Who did what (match Git history), AI tools used and for what, external code/libraries cited | `git shortlog -sn`, list |

## Requirement traceability (keep updated — examiners ask "where is this?")
| Item | Where in the code | Evidence for the report |
|---|---|---|
| M1 ≥5 sensor types (≥2 node, ≥2 gateway) | node: DHT22, reed — gateway: DS18B20, LDR, MQ-135 | photo + dashboard |
| M2 actuator | 2 DC fans via MOSFET, `fanTask` in `gateway/src/main.cpp` | video of fan |
| M3 node → gateway | ESP-NOW, `common/packet.h` | serial logs |
| M4 AI closed loop | `ai/service.py` → `@msg/lensguard/fan` → gateway | `[cmd]` log + fan event `ai` |
| M5 cloud + dashboard | NETPIE shadow + feed, Freeboard | screenshots |
| M6 notification | `ai/alerts.py` (Telegram) | phone screenshots |
| M7 safety | 5 V/12 V DC only, fan 30-min timeout + cool-down, flyback diode | `FAN_MAX_ON_MS`, test E4 |
| M8 Git | GitHub repo, all 5 members | `git shortlog -sn` |
| E1 FreeRTOS | node: 3 tasks; gateway: 4 tasks; queues + mutex; priorities explained in file headers | task diagram |
| E2 ISR | reed ISR → `doorQueue` → `doorTask` (node) | door latency metric |
| E4 reliable comms | ACK/retry/back-off + duplicate filter + channel scan; gateway ring buffer | tests E3, F1 |
| E5 security | MQTT over TLS + certificate pinning, secrets only in `secrets.h`/`.env` (git-ignored) | test F7 |
| E6 OTA | `ArduinoOTA` + `gateway_ota` env | test F8 |
| E3 low power (spare) | not implemented | — |

Metrics (rubric IV, ≥3 + repetition): delivery rate, latency (record + door + alert), sensor accuracy, AI MAE vs persistence, uptime —
all from `tools/metrics.py` except sensor accuracy (manual, test N6).
