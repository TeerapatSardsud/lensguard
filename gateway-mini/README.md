# gateway-mini — checkpoint firmware variant

Minimal gateway (ESP-NOW from nodes -> Wi-Fi -> NETPIE) used by the team's
checkpoint board and the friend's NETPIE dashboard/notifier project.
Compared to ../gateway (full: local DS18B20/LDR/MQ-135, fan GPIO, offline ring
buffer, events, failsafe rule, OTA), this variant deliberately stays small:

- per-node records -> @shadow/data/update (console feed) AND
  @msg/lensguard/data (repo protocol) - added 2026-11-20 so ai/service.py
  consumes it unchanged;
- subscribes @msg/lensguard/fan; incoming AI commands are logged and echoed
  to shadow (box_X_fan) - drive the fan GPIO at the marked TODO;
- SNTP on Wi-Fi connect + forced re-sync every 15 min (Asia/Bangkok). Fixes
  the runaway-clock class of bug at the source; ts verified live-accurate
  (<2 s) on bench 2026-11-20.

Requires: devices in ONE NETPIE device group (@msg fails silently outside a
group; see docs/architecture.md). Secrets: copy include/secrets.h.example ->
include/secrets.h (git-ignored). Build: pio run -d gateway-mini -t upload
(board esp32doit-devkit-v1). Bench-verified 2026-11-20, not yet soak-tested.
