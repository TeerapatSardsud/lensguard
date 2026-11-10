# Things only the team can do (accounts / hardware / people)

Deadlines: **checkpoint video Fri 20 Nov 23:59** (≤ 2 min, end-to-end data flow) · **final Tue 8 Dec**.

## 1. Git (everyone commits — Git history is used for the peer factor)
Repo: https://github.com/TeerapatSardsud/lensguard — accept the collaborator invite, then:
```bash
git clone https://github.com/TeerapatSardsud/lensguard.git && cd lensguard
git config user.name  "<your GitHub username>"
git config user.email "<email of your GitHub account>"      # otherwise commits are not counted as yours
git switch -c <your-name>/<topic>                            # never work on main directly
# ... work ... then
git add -p && git commit -m "node: debounce door ISR" && git push -u origin HEAD   # open a Pull Request
```
Check with `git shortlog -sn` that all 5 appear. **Never commit** `secrets.h`, `.env`, tokens (they are in `.gitignore`).
Every member must be able to explain the code of their own part in Q&A — read it, run it, change it yourself.

## 2. NETPIE (owner: cloud/dashboard)
portal.netpie.io → create a Project → create a **Device Group** → create **2 devices in that group**:
| Device | Used by | Credentials go to |
|---|---|---|
| `lensguard-gateway` | real gateway (and `tools/mqtt_simulator.py` before hardware) | `gateway/include/secrets.h` and `NETPIE_*` in `.env` |
| `lensguard-ai` | `ai/service.py` | `AI_NETPIE_*` in `.env` |

**Status 2026-11-10:** project `P567622029659` with both devices exists; steps 1 and 3 below were verified
(shadow keys appear, `@msg` travels both ways, closed loop works with the simulator). Steps 2 and 4 are still open.

Then, to verify (write down what you find in this file):
1. Run `python tools/mqtt_simulator.py --period 2`; the gateway device's **Shadow** should show `box_A_rh`, `box_B_rh`, `gateway_temp_out` …
2. Enable data storage (**Feed**) on the gateway device; define the numeric fields (`box_A_rh`, `box_A_temp`, `box_A_door`, the same for box_B,
   `gateway_temp_out`, `gateway_ldr`, `gateway_gas`, `gateway_fan_box_A`, `gateway_fan_box_B`). This is the raw-data cloud storage (M5).
3. Run `python ai/service.py` at the same time; it must print received records and `[cmd] box_A fan=…` lines, and the simulator must print
   `[ai->fan]` → this proves `@msg/lensguard/data` and `@msg/lensguard/fan` travel between the two devices of the group.
4. Dashboard (Freeboard in NETPIE): gauges RH box_A / box_B, time-series chart of both RH + 55 % line, door state, fan state per box,
   outside temperature, "last update" time.

## 2b. Web dashboard (owner: Natthadon) — `ai/web/index.html`, served by `ai/service.py` on port 8080
A first version exists (cards per box, RH chart with fan strip, table view, alerts, dark mode). Natthadon owns it from here:
read it, adjust wording/layout with real users (photo club), and explain it in Q&A. NETPIE Freeboard is optional now;
the Feed (step 2 above) is still needed as cloud storage (M5).

## 3. Telegram (owner: cloud/dashboard) — DONE 2026-11-10: @Lensguardbot → group "Lensguard"
Follow the header of `tools/telegram_test.py` (BotFather → token → chat id → test message). Put both values in `.env`.
Add the bot to the group chat so everyone sees the alerts.

## 4. Hardware (when parts arrive) — order **2 logic-level MOSFETs** (one fan per box)
1. Wire per `docs/pin_map.md`. Check polarity before powering.
2. Flash the gateway with `SIMULATE_SENSOR=1` first: serial log must show Wi-Fi, `[mqtt] connecting... ok`.
3. Flash both nodes (`-e box_A`, `-e box_B`) with `SIMULATE_SENSOR=1`: node log `-> ACK`, gateway log `[rx] box_A …`.
   Nodes find the gateway's channel automatically (`[scan] gateway found on channel N`).
4. Set `SIMULATE_SENSOR=0` in both `platformio.ini`, reflash, check real values against the reference hygrometer.
5. Start `ai/service.py` on a laptop that stays on → it logs to `ai/data/live.jsonl`. **Collect ≥ 48 h**, then
   `python ai/prepare.py` → `python ai/train.py --data ai/data/train_box_A.csv ai/data/train_box_B.csv`.
6. Run the test plan in `docs/test_plan.md` and `python tools/metrics.py` for the numbers in the report.

## 5. Owners (from the proposal)
| Area | Owner |
|---|---|
| System architecture, message format, integration | Teerapat |
| Node firmware + box wiring (DHT22, reed, ISR, ESP-NOW ACK/retry) | Wachiraphan |
| Gateway firmware (FreeRTOS, fans/MOSFET, MQTT/TLS, OTA) + gateway sensors | Napat |
| NETPIE dashboard + Telegram | Natthadon |
| Plan, test plan, metrics, AI script | Methasit |
Everyone writes their own part of the report.
