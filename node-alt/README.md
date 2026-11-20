# node-alt — checkpoint node firmware variant (JSON over ESP-NOW)

The firmware actually running (or about to run) on the two **box** ESP32s, i.e. the
boards that are *not* the gateway. Added to the repo by `8bcd03e` as
`node-alt/src/main.cpp` only; this directory now carries the whole PlatformIO project so
it can be built and flashed without the original zip.

Which firmware is which (do not mix them across boards):

| Directory | Radio payload | Runs on |
|---|---|---|
| `node/` | packed binary `DataPacket` (`common/packet.h`) | reference/sim node (Wokwi, repo gateway) |
| `node-alt/` | JSON object per reading | the two real boxes, together with `gateway-mini/` |
| `gateway/` | receives the binary packets | repo gateway (full features) |
| `gateway-mini/` | receives JSON | the checkpoint gateway board (pairs with `node-alt/`) |

A JSON node talking to `gateway/` (or a binary node to `gateway-mini/`) is total silence
in both directions — the two pairs are not interchangeable.

## Hardware / wiring

- DHT22 DATA on **GPIO25** on both built boxes — the DATA wire is too short to reach
  GPIO4, which is what `docs/pin_map.md`, the Wokwi diagram and `node/src/main.cpp`
  assume (builder confirmed 2026-11-20). All bench settings live in `include/config.h`.
- Reed switch on GPIO33 ↔ GND, `INPUT_PULLUP` (matches the repo pin map).
- Env names are **lowercase**: `box_a` / `box_b` (the `node/` project uses `box_A`/`box_B`).
  They set `-DNODE_ID="box_A"` / `"box_B"`, and `include/config.h` defaults to `box_A`, so
  check the boot line prints the right box.

## Build / flash

```bash
pio run -d node-alt -e box_a -t upload     # box A
pio run -d node-alt -e box_b -t upload     # box B
pio device monitor -b 115200               # Node MAC, [cfg], [tx] -> ACK, [stats]
```

Expected first boot: `Node MAC: …`, `[cfg] GATEWAY_MAC is still the placeholder -> sending
broadcast`, `[cfg] node=box_A ch=1 sample=5000ms scan=1`, one
`[tx] {…"type":"boot"…"valid":0…} -> ACK`, then one `periodic` record with `valid:1` every
`SAMPLE_PERIOD_MS`. `[stats] tx=… acked=… lost=… retries=… ch=… heap=…` every
`STATS_EVERY` (default 60) samples is the packet-delivery and memory evidence used in the
report.

## What the 2026-11-20 patch added over the original checkpoint build

1. `GATEWAY_MAC` placeholder → automatic broadcast fallback (ESP-NOW has no discovery; the
   shipped `AA:BB:CC:DD:EE:FF` meant a fresh flash transmitted to a nonexistent station).
2. Channel scan 1..13 after `MAX_RETRIES` without an ACK (hotspots pick their own channel;
   the original hardcoded channel 1). Log line `[ch] gateway found on channel N` — pin that
   into `ESPNOW_CHANNEL` afterwards. Peer registered with `channel = 0` so a channel change
   needs no peer delete/add.
3. Explicit `valid` flag (1 good read, 0 DHT failure) + `[dht] read failed` log, matching
   `common/packet.h` / `docs/message_format.md`. Verified scope: `ai/features.py` already
   interpolates a minute with no trustworthy reading, so this is protocol conformance and a
   measurable sensor-failure rate, not different model inputs.
4. One `type:"boot"` record per start, so a reboot is visible on the timeline.
5. `[stats]` delivery/heap line (report metrics).
6. `DHT_PIN` / `REED_PIN` moved from `main.cpp` into `include/config.h`.

Unchanged on purpose: retry/ACK scheme, queue sizes, task priorities, ISR/door debounce,
`platformio.ini`, and the fact that **the node has no clock at all** — `ts` is stamped by
the gateway (which re-syncs SNTP every 15 min), so the node cannot contribute to the
clock-drift bug class fixed in PR #13/#19.

## Status

Builds clean for `box_a` and `box_b` (platformio 6.2.0, espressif32 resolved to 6.13.0,
ArduinoJson 6.21.6; RAM 13.5 %, Flash 56.6 %, zero warnings from `src/main.cpp`) and is
compiled in CI. **Hardware: pending bench flash** — the gateway counterpart
(`gateway-mini/`) is bench-verified, this one is not yet. If a board reports `NO ACK`
through a full channel scan, check the gateway first (powered? running the `@msg`-patched
build? same Wi-Fi?).
