# Final report outline (≤ 10 pages) — follow the 6 rubric parts of the course
(Fill the real numbers after testing. Keep this file as the single source for the report.)

| Part | What to write | Evidence to collect |
|---|---|---|
| 1. Problem & objective | Humidity/fungus risk for lenses in Thai climate; who has the problem; target RH range; sources [1]–[9] from the proposal | the cited sources |
| 2. System design | Block diagram, hardware list, pin map (`docs/pin_map.md`), message format, why ESP-NOW + gateway, why MQTT/TLS | diagram, photos of wiring |
| 3. Implementation | Firmware structure (tasks/queue/ISR), ACK+retry, offline buffer, cloud/dashboard, AI model + features, fan control, Telegram | code snippets (short), screenshots |
| 4. Testing & results | Packet delivery rate, retry count, latency; AI MAE vs baseline **on real data**; RH over time with/without fan; door-open events; offline recovery test | CSV logs, plots, tables |
| 5. Discussion | Limits (sensor accuracy, small box, short data), what failed and how it was fixed, what you would change | honest failure log |
| 6. Contribution & AI usage | Who did what (match Git history), AI tools used and for what, external code/libraries cited | `git shortlog -sn`, list |

Minimum-requirement checklist (M1–M8): tick each item and point to where it is demonstrated.
Engineering-depth items (E1 FreeRTOS, E2 ISR, E4 reliable comms, E5 TLS/secrets, E6 OTA if done): one paragraph + one evidence each.
