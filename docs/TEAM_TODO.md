# Things only the team can do (accounts / hardware / people)

## 1. Git (everyone commits — Git history is used for the peer factor)
```bash
# one member
cd lensguard
git init -b main
git add . && git commit -m "Initial scaffold"
# create an EMPTY repo on GitHub (public or give the TAs access), then:
git remote add origin https://github.com/<org-or-user>/lensguard.git
git push -u origin main
```
Add the other 4 as collaborators (Settings → Collaborators). Each person: clone, work on a branch, commit under their own name
(`git config user.name/user.email`), open a pull request. Check with `git shortlog -sn` that all 5 appear.
**Never commit** `secrets.h`, tokens or `.env` (they are in `.gitignore`).

## 2. NETPIE (1 person)
portal.netpie.io → create Project → Device → copy ClientID / Token / Secret → put in `gateway/include/secrets.h` (copy of `secrets.example.h`)
and as env vars for `tools/mqtt_simulator.py`. Build a dashboard with 2 gauges (RH box_A / box_B), a time-series chart, and door/fan state.

## 3. Telegram (1 person)
Follow the header of `tools/telegram_test.py` (BotFather → token → chat id → test message).

## 4. Hardware (when parts arrive)
Wire per `docs/pin_map.md` → set `SIMULATE_SENSOR=0` in both `platformio.ini` → flash gateway first, read its MAC and Wi-Fi channel from the serial log →
put the MAC into `GATEWAY_MAC` and the channel into `ESPNOW_CHANNEL` in `common/protocol.h` → flash nodes.

## 5. Suggested owners (edit names)
| Area | Owner |
|---|---|
| Node firmware + box wiring | member 1 |
| Gateway firmware + fan driver | member 2 |
| Cloud + dashboard + Telegram | member 3 |
| AI (data logging, model, controller) | member 4 |
| Report, video, test logs | member 5 (everyone writes their own part of the report) |
