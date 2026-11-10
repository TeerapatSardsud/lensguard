# Test plan (rubric IV: normal use, edge cases, failure conditions — with steps and results)

Fill **Result** with what actually happened (date, numbers, screenshot/log file name). Repeat measured tests ≥ 3 times and
report mean / min / max (`tools/metrics.py` prints them). Keep failed attempts too — they go into the Discussion.

## A. Normal use
| ID | Test | Steps | Pass if | Result |
|---|---|---|---|---|
| N1 | Periodic data | Run gateway + 2 nodes + `ai/service.py` 1 h | both boxes on dashboard every 30 s; delivery ≥ 99 % | |
| N2 | Door event | Open/close lid 10× | each change on dashboard within 2 s (`reason=1`); `tools/metrics.py` door latency | |
| N3 | AI closed loop | Breathe/put damp cloth near box until RH rises | `[cmd] fan=ON` before RH reaches 55 %; fan stops ≤ 45 % | |
| N4 | Fan lowers RH | RH in box ≈ 65 %, fan ON | RH curve falls; time 65 → 45 % recorded | |
| N5 | Telegram RH alert | RH ≥ 60 % for 2 readings | message on phone; alert latency in `alerts.jsonl` | |
| N6 | Sensor accuracy | DHT22 vs reference hygrometer, DHT22 temp vs DS18B20, 3 sessions × 10 readings | mean/min/max error reported | |
| N7 | 24 h run | Leave everything running ≥ 24 h | uptime ≥ 24 h per node (`metrics.py` section 3) | |

## B. Edge cases
| ID | Test | Steps | Pass if | Result |
|---|---|---|---|---|
| E1 | Lid left open | Open lid > 2 min | one "ฝาเปิดค้าง" alert, not repeated every reading | |
| E2 | Door bounce | Tap the lid quickly 5× | no flood of packets; final state correct (50 ms debounce) | |
| E3 | Duplicate packet | Shield gateway briefly so ACKs are lost | node retries (`tries>1`), no duplicate seq in `live.jsonl` | |
| E4 | Fan safety timeout | Keep box humid so fan runs | fan OFF after 30 min, 5 min cool-down, `fan` event `timeout` | |
| E5 | Saturated silica | Use already-wet silica gel | `silica` event + Telegram "เปลี่ยน/อบซิลิกาเจล" | |
| E6 | 3rd node (scalability) | Flash a spare board with `NODE_ID="box_C"` or run a simulated node | gateway forwards it, dashboard key `box_C_rh` appears | |

## C. Failure conditions
| ID | Test | Steps | Pass if | Result |
|---|---|---|---|---|
| F1 | Wi-Fi loss | Turn hotspot off 5 min, then on | gateway buffers (`[buf] offline`), re-sends all records in order; no seq gap in `live.jsonl` | |
| F2 | Cloud/AI down | Stop `ai/service.py` | after 2 min gateway uses fallback 55/45 (`fan` event `fallback`) | |
| F3 | Node power loss | Unplug node 5 min | "ไม่ได้รับข้อมูล" alert after 3 min; "กลับมาแล้ว" after re-plug; new `boot` id | |
| F4 | Gateway power loss | Unplug gateway, replug | reconnects Wi-Fi + MQTT by itself, nodes re-acquire channel | |
| F5 | Sensor unplugged | Disconnect DHT22 data wire | `valid=0`, no fake RH on dashboard, fan does not act on it | |
| F6 | Hotspot channel change | Change hotspot / restart it on another channel | nodes print `[scan] gateway found on channel N` | |
| F7 | Wrong TLS server | Build with a wrong fingerprint in `NETPIE_CERT_SHA256_LIST` | gateway refuses to send credentials | |
| F8 | OTA | `pio run -d gateway -e gateway_ota -t upload` | new firmware runs; fans OFF during update | |
