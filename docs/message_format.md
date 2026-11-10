# Message formats  (single source of truth — code: `common/packet.h`, `common/protocol.h`, `tools/netpie.py`)

```
node (box_A, box_B) ──ESP-NOW DataPacket──▶ gateway ──AckPacket──▶ node
gateway ──MQTT/TLS──▶ NETPIE  @msg/lensguard/data   (full JSON records)  ──▶ AI service (logger, model, alerts)
                              @shadow/data/update   (flat keys)          ──▶ dashboard + feed (cloud storage)
AI service ──▶ NETPIE @msg/lensguard/fan ──▶ gateway fan control
AI service ──▶ Telegram
```

## 1. ESP-NOW (node → gateway) — `common/packet.h`
`DataPacket{type=1, node_id[8], seq, boot_id, temp, rh, door, valid, reason, tries}` (26 bytes, packed)
→ gateway answers `AckPacket{type=2, seq}` to the sender's MAC.

| Field | Meaning |
|---|---|
| `node_id` | `box_A` / `box_B` (any other id is still forwarded → scalability test with simulated nodes) |
| `seq` | +1 per packet, restarts at 1 after reboot |
| `boot_id` | random per boot; the gateway uses (`boot_id`, `seq`) to drop duplicates caused by a lost ACK |
| `reason` | 0 periodic (every 30 s), 1 door change (sent immediately, jumps the queue), 2 first packet after boot |
| `tries` | which attempt got through (1 = first) → retry statistics |

Reliability (E4): node waits `ACK_TIMEOUT_MS` = 150 ms, retries up to `MAX_RETRIES` = 3 with a small back-off.
After `FAILS_BEFORE_SCAN` = 2 failed packets in a row it scans Wi-Fi channels 1–13 to find the gateway
(phone hotspots choose their own channel). `GATEWAY_MAC` all-zero = broadcast, so no MAC editing is needed.

## 2. MQTT (gateway → NETPIE)
Host `mqtt.netpie.io:1884` (TLS). Username = device **Token**, password = device **Secret**, ClientID = device **Client ID**.
TLS: NETPIE signs with a private CA it does not send → both gateway and Python tools **pin the SHA-256 fingerprints**
of NETPIE's certificates (`NETPIE_CERT_SHA256_LIST`). mqtt.netpie.io is load-balanced and the two backends have
different certificates, so the list has 2 entries (checked 2026-11-10).

### 2a. `@msg/lensguard/data` — one full record per message (AI service subscribes)
```json
{"node_id":"box_A","seq":1042,"boot":51234,"ts":1762750000123,"temp":28.4,"rh":52.1,"valid":1,"door":0,"reason":0,"tries":1}
{"node_id":"gateway","seq":88,"ts":1762750000456,"temp_out":30.2,"ldr":310,"gas":118,"fan_box_A":1,"fan_box_B":0,
 "buffered":0,"buf_lost":0,"uptime_s":86400,"rssi":-61,"heap":187000,"rx_ok":5712,"rx_dup":3,"mqtt_connects":2}
{"node_id":"gateway","ts":0,"event":"boot","detail":"TASK_WDT","value":0.0}
{"node_id":"gateway","ts":1762750000789,"event":"fan","box":"box_A","detail":"ai","value":1.0}
{"node_id":"gateway","ts":1762750000999,"event":"silica","box":"box_B","detail":"rh_drop_too_small","value":1.2}
```
- `ts` = epoch **milliseconds** when the gateway received/created the record (NTP). 0 = clock not synced yet.
- `fan` event `detail`: `ai`, `fallback`, `timeout`, `no_data`, `ota`.
- Gateway heartbeat (every 30 s): `uptime_s`, `rssi` (dBm, 0 = Wi-Fi down), free `heap`, `rx_ok` / `rx_dup` (ESP-NOW packets
  forwarded / duplicates dropped), `mqtt_connects` (> 1 = it reconnected by itself), `buffered` / `buf_lost` (offline buffer).
- `boot` event, once per start, `detail` = reset reason (`POWERON`, `SW`, `PANIC`, `TASK_WDT`, `BROWNOUT`, …).
  Crash/watchdog/brown-out reasons trigger a Telegram message.
- Records produced while offline are kept in a 120-entry ring buffer and re-sent in order when MQTT is back (E4).

### 2b. `@shadow/data/update` — same record, flattened with the node id as prefix (dashboard / feed)
```json
{"data":{"box_A_seq":1042,"box_A_ts":1762750000123,"box_A_temp":28.4,"box_A_rh":52.1,"box_A_door":0, "...": "..."}}
{"data":{"gateway_temp_out":30.2,"gateway_ldr":310,"gateway_gas":118,"gateway_fan_box_A":1,"gateway_fan_box_B":0}}
```
Why flat keys: the shadow is ONE document per device. With plain `rh`, box_B would overwrite box_A.
Events are not written to the shadow.

## 3. Fan command (AI service → gateway) — `@msg/lensguard/fan`
```json
{"node_id":"box_A","fan":1,"pred":57.3,"rh":52.0,"ts":1762750020000}
```
Sent every minute per box. The gateway only reads `node_id` and `fan`.

## 4. Fan decision priority (gateway `fanTask`, mirrored in `tools/mqtt_simulator.py`)
1. OTA in progress → OFF.
2. Fan ON for `FAN_MAX_ON_MS` (30 min) → OFF + 5-min cool-down (safety, M7). If RH dropped < 3 %RH during that
   run → `silica` event → Telegram "regenerate the silica gel".
3. In cool-down → OFF.
4. AI command younger than `AI_CMD_TTL_MS` (2 min) → follow it. AI rule: forecast or current RH ≥ 55 % → ON;
   once ON, keep running until measured RH ≤ 45 %.
5. No data from that box for 90 s → OFF.
6. Fallback (AI/cloud down): RH ≥ 55 % → ON, RH ≤ 45 % → OFF.

## 5. Self-recovery (`common/protocol.h`)
Task watchdog 60 s on every node and gateway task (a hung task → reboot) · Wi-Fi down 5 min → `WiFi.begin()` again ·
Wi-Fi up but MQTT failing 15 min → reboot (buffered records are lost in that case) · nodes re-scan channels when the gateway moves.

## 6. Logs written by `ai/service.py` (git-ignored, used by `ai/prepare.py` and `tools/metrics.py`)
`ai/data/live.jsonl` (every record + `rx_ms`), `ai/data/predictions.jsonl`, `ai/data/alerts.jsonl`.
