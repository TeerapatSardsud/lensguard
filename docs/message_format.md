# Message formats

## ESP-NOW (node → gateway)  — see `common/packet.h`
`DataPacket{type, node_id[8], seq, temp, rh, door, valid}` → gateway replies `AckPacket{type, seq}`.
Node waits `ACK_TIMEOUT_MS` (150 ms) and retries up to `MAX_RETRIES` (3). The gateway ignores a repeated `seq`
(so a lost ACK does not create duplicate cloud records).

## MQTT (gateway → NETPIE)
Host `mqtt.netpie.io`, port 1884 (TLS) / 1883 (plain). Username = device **Token**, password = device **Secret**, ClientID = device ID.
Topic `@shadow/data/update`, payload wraps the record in `data`:
```json
{"data": {"node_id":"box_A","seq":1042,"ts":1760000000,"temp":28.4,"rh":52.1,"door":0}}
{"data": {"node_id":"gateway","seq":88,"ts":1760000000,"temp_out":30.2,"ldr":310,"gas":118,"fan":1}}
```
- `seq` → packet delivery rate = received / (max seq − min seq + 1).
- `door`: 1 open / 0 closed. `fan`: current fan state.
- Downlink (AI → fan): gateway subscribes `@msg/fan` with payload `on` / `off` — **verify the exact NETPIE message topic in the portal before relying on it.**

## Fan command priority
1. Fresh AI command (< 2 min old) → follow it.
2. Otherwise local rule: RH ≥ 60 % → ON, RH ≤ 55 % → OFF (hysteresis). The box still works if the cloud/AI is down.
