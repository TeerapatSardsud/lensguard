# LensGuard — status (10 Nov 2026)

**Next deadline:** checkpoint video Fri 20 Nov 23:59 · final Tue 8 Dec

## Done (on `main`, CI green)
- Firmware node + gateway **compile** (FreeRTOS tasks, door ISR, ESP-NOW ACK/retry, TLS, OTA, watchdog) — not yet on boards
- **NETPIE** project `P567622029659`: 2 devices (gateway, ai) in one group — shadow, `@msg` both ways, closed loop verified live
- **AI service**: prediction → fan command → gateway; logs data for training
- **Telegram**: @Lensguardbot → group "Lensguard" (real alerts tested)
- **Web dashboard** at http://localhost:8080 (served by the AI service)
- **Simulator** = fake gateway → checkpoint video can be recorded before hardware arrives
- **Wokwi** wiring: https://wokwi.com/projects/480315294985826305 (`docs/wokwi/diagram.json`, LEDs stand in for the fans). Firmware + `docs/pin_map.md` now use the same pins: reed GPIO33, DS18B20 GPIO16, fans GPIO26/27

## To do
| What | Owner |
|---|---|
| Accept the GitHub invite, clone, work on your own branch | everyone |
| Keep Wokwi and `docs/wokwi/diagram.json` in sync when wiring changes | Wachiraphan / Napat |
| Order parts incl. **2 logic-level MOSFETs**; wire + flash boards | Wachiraphan (node), Napat (gateway) |
| NETPIE **Feed** (cloud storage, M5); own the web dashboard | Natthadon |
| Record checkpoint video (simulator OK, say it is simulated) | everyone |
| ≥ 48 h real data → retrain AI → metrics + test plan results | Methasit |
| Review PRs, integration, architecture part of the report | Teerapat |

## Rules
- One NETPIE device = one connection at a time → only **one** machine runs the simulator/AI with the shared credentials.
- Credentials only in `.env` / `secrets.h` (git-ignored), never in chat or commits.
- Decide in the team: LDR = room light (it sits outside the boxes, cannot detect the lid).
