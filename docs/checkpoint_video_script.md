# Checkpoint video script (≤ 2 min, due Fri 20 Nov 2026 23:59) — "end-to-end data flow"

Goal: show data moving sensor node → gateway → cloud → dashboard, plus an alert. **Say clearly what is simulated.**

Setup before recording (two options, pick what is ready):
- **A (hardware arrived):** gateway + ≥1 node flashed (`SIMULATE_SENSOR=0` if sensors are wired, else 1), `ai/service.py` running.
- **B (no hardware yet):** `python tools/mqtt_simulator.py --period 2` + `python ai/service.py` — say "ข้อมูลจำลองจาก simulator".
- For a guaranteed alert in the video: `python tools/mqtt_simulator.py --period 1 --saturate box_B`
  (box_B's RH climbs → RH alert; after the 30-min fan timeout in sim time → silica alert),
  or open a real lid with `python ai/service.py --door-after 0` (immediate door alert).

| Time | Screen | Say (Thai is fine) |
|---|---|---|
| 0:00–0:15 | Title + block diagram (proposal) | "LensGuard ตู้กันชื้นอัจฉริยะ กลุ่ม TuaKorm — วันนี้แสดง data flow ด้วย [ฮาร์ดแวร์จริง / ข้อมูลจำลอง]" |
| 0:15–0:45 | Serial monitor node + gateway (or simulator terminal) | node `[tx] seq=… -> ACK`, gateway `[rx] box_A seq=…`; ชี้ `seq` และ ACK/retry |
| 0:45–1:15 | NETPIE shadow + dashboard | กราฟ RH box_A / box_B ขยับตามข้อมูลใหม่ทุก 30 วินาที; ประตู/พัดลมแสดงสถานะ |
| 1:15–1:40 | `ai/service.py` terminal | `[cmd] box_A fan=ON rh=52 pred=56` — AI สั่งพัดลมก่อนความชื้นถึง 55 % (โมเดลยังเทรนด้วยข้อมูลจำลอง) |
| 1:40–1:55 | Phone: Telegram | ข้อความแจ้งเตือน RH สูง / ฝาเปิดค้าง เด้งขึ้นมา |
| 1:55–2:00 | Plan | "ต่อไป: ต่อฮาร์ดแวร์จริง เก็บข้อมูลจริง 48 ชม. เทรนโมเดลใหม่ ทดสอบ 24 ชม." |

Checklist: dashboard open and live · phone hotspot on · every member appears or is credited · < 2:00 ·
uploaded before 20 Nov 23:59 (late = −2). Record in one pass; don't cut so hard that the data flow looks faked.
