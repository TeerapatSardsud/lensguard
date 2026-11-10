# Checkpoint video script (≤ 2 min, due 20 Nov 2026 23:59) — "end-to-end data flow"

Goal: show data moving from a sensor (real or simulated) → gateway → cloud → dashboard, plus an alert. Say clearly what is simulated.

| Time | Screen | Say (Thai is fine) |
|---|---|---|
| 0:00–0:15 | Title + block diagram | "LensGuard ตู้กันชื้นอัจฉริยะ — ตอนนี้ฮาร์ดแวร์ [มาถึงแล้ว/ยังไม่ครบ] จึงแสดง data flow ด้วย [ข้อมูลจริง/จำลอง]" |
| 0:15–0:45 | Serial monitor of node + gateway | node ส่ง ESP-NOW → gateway ตอบ ACK; ถ้าไม่ได้ส่ง ให้โชว์ log จำลอง/ simulator แทน (บอกว่าเป็นของจำลอง) |
| 0:45–1:15 | NETPIE / dashboard | ข้อมูลเข้า cloud ทุก ~5–30 วินาที; กราฟ RH ของ box_A/box_B ขยับ; ชี้ `seq` |
| 1:15–1:40 | AI + fan | โชว์ผลพยากรณ์ RH ล่วงหน้า 10 นาที (graph จาก synthetic/จริง) และคำสั่งพัดลม (LED/ log) |
| 1:40–1:55 | Telegram | RH เกินเกณฑ์ → ข้อความแจ้งเตือนเด้งในมือถือ |
| 1:55–2:00 | Plan | "สัปดาห์ถัดไป: ต่อฮาร์ดแวร์จริง, เก็บข้อมูลจริง, เทรนโมเดลใหม่" |

Checklist before recording: dashboard open and showing live values · phone hotspot on · each member appears or is credited · < 2:00 · uploaded before 20 Nov 23:59 (late = −2).
Record the screen in one pass; do not cut so aggressively that the data flow looks faked.
