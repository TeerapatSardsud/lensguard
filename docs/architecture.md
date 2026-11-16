# LensGuard — สถาปัตยกรรมซอฟต์แวร์และแผนรับมือข้อผิดพลาด

อิงอุปกรณ์ตาม BOM ใน `material/LensGuard_Proposal.pdf` (หัวข้อ 3) และข้อ E1 E2 E4 E5 E6 ที่เลือก
สถานะของแต่ละส่วน: **มีแล้ว** = อยู่ในโค้ดบน `main`, **ต้องทำ** = ยังไม่มี, **ทดสอบจริง** = มีโค้ดแล้วแต่ยังไม่เคยรันกับฮาร์ดแวร์จริง
(ตอนนี้เฟิร์มแวร์ทั้งหมดผ่านการคอมไพล์และรันแบบจำลองเท่านั้น ของจริงเพิ่งสั่งซื้อ)

```
 กล่อง A                     กล่อง B
 ┌──────────────────┐        ┌──────────────────┐
 │ ESP32 node box_A │        │ ESP32 node box_B │
 │  DHT22 (GPIO4)   │        │  DHT22 (GPIO4)   │
 │  reed  (GPIO33)  │        │  reed  (GPIO33)  │
 └────────┬─────────┘        └────────┬─────────┘
          │ ESP-NOW DataPacket / AckPacket (E4: ACK+retry, seq, boot_id)
          ▼                           ▼
 ┌────────────────────────────────────────────────────┐
 │ ESP32 gateway                                      │
 │  DS18B20 (GPIO16)  LDR (GPIO34)  MQ-135 (GPIO35)   │
 │  fan A MOSFET (GPIO26)  fan B MOSFET (GPIO27)      │
 │  FreeRTOS 4 tasks (E1)  offline ring buffer (E4)   │
 │  MQTT/TLS pinned (E5)   ArduinoOTA (E6)            │
 └────────────────────────┬───────────────────────────┘
                          │ MQTT over TLS  mqtt.netpie.io:1884
                          ▼
 ┌────────────────────────────────────────────────────┐
 │ NETPIE  (M5)  shadow + feed = ที่เก็บข้อมูลบน cloud │
 │   @msg/lensguard/data   @msg/lensguard/fan          │
 └────────────────────────┬───────────────────────────┘
                          │
 ┌────────────────────────▼───────────────────────────┐
 │ AI service (Python, โน้ตบุ๊ก/Raspberry Pi 4)        │
 │  Engine (ทำนาย RH +10 นาที, M4) ─▶ สั่งพัดลม         │
 │  AlertEngine ─▶ Telegram (M6)                       │
 │  Dashboard  ─▶ http://<ip>:8080  (M5 ส่วน UX)       │
 │  log: live.jsonl / predictions.jsonl / alerts.jsonl │
 └────────────────────────────────────────────────────┘
```

---

## 1. องค์ประกอบซอฟต์แวร์ทั้งหมด

### 1.1 Sensor node (`node/`) — ESP32 DevKit × 2, DHT22, reed switch + แม่เหล็ก
| ส่วน | หน้าที่ | สถานะ |
|---|---|---|
| `sensorTask` (p2) | อ่าน DHT22 ทุก 30 s, ตั้ง `valid=0` เมื่ออ่านไม่ได้ (NaN) | มีแล้ว |
| `doorTask` (p4) | รับ event จาก ISR ผ่าน queue, debounce 50 ms, ส่งทันที (`reason=1`) | มีแล้ว (E2) |
| `commTask` (p3) | ส่ง `DataPacket`, รอ ACK 150 ms, retry 3 ครั้ง back-off, scan ช่อง 1–13 เมื่อพลาดติดกัน 2 แพ็กเก็ต | มีแล้ว (E4) |
| ISR `onDoorEdge` | ขอบสัญญาณ reed → `doorQueue` (ไม่มี logic ใน ISR) | มีแล้ว (E2) |
| watchdog 60 s ทุก task | task ค้าง → reboot | มีแล้ว |
| `boot_id` + `seq` | ให้ gateway กรองซ้ำและนับแพ็กเก็ตหาย | มีแล้ว |
| OTA บน node | proposal เขียน "gateway (และ node)" — node ใช้ ESP-NOW อย่างเดียว ไม่ต่อ Wi-Fi จึงไม่มีช่องทาง OTA | ตัดสินใจแล้ว (15 พ.ย.): E6 ทำที่ gateway เท่านั้น ระบุในรายงาน; node อัปเดตผ่าน USB |
| E3 deep sleep (สำรอง) | ยังไม่ทำ; ถ้าทำต้องย้าย reed ไป pin ที่ปลุกได้ (RTC GPIO 33 ใช้ได้) | สำรอง |

### 1.2 Gateway (`gateway/`) — ESP32 DevKit, DS18B20 + 4.7k, LDR + 10k, MQ-135, พัดลม DC × 2 ผ่าน N-MOSFET + ไดโอด
| ส่วน | หน้าที่ | สถานะ |
|---|---|---|
| `espnowTask` (p4) | รับแพ็กเก็ต, ตอบ ACK ทันที, กรองซ้ำด้วย (`boot_id`,`seq`), อัปเดตสถานะกล่องใต้ mutex, ส่งต่อไป `pubQueue` | มีแล้ว |
| `fanTask` (p3) | ตัดสินใจพัดลมตามลำดับความสำคัญ (ข้อ 2.4) ทุก 1 s, ขับ MOSFET | มีแล้ว (M2 M7) |
| `sensorTask` (p2) | DS18B20, LDR, MQ-135 ทุก 30 s + heartbeat | มีแล้ว (ADC ยังเป็นค่าดิบ) |
| `cloudTask` (p1) | Wi-Fi, NTP, MQTT/TLS pin fingerprint, ring buffer 120 ระเบียน, ArduinoOTA | มีแล้ว (E4 E5 E6) |
| MQTT callback → `fanCmdQueue` | รับคำสั่ง AI, อายุคำสั่ง 2 นาที | มีแล้ว |
| boot event + reset reason | หลักฐาน robustness บน cloud | มีแล้ว |
| calibrate LDR/MQ-135 | แปลง ADC → "สว่าง/มืด" และ "VOC สูงกว่าปกติ" | ต้องทำ (หลังมีของจริง) |
| MQ-135 warm-up | ค่านาที แรก ๆ ใช้ไม่ได้ (heater ต้องอุ่น ≥ 2–3 นาที, ค่านิ่งหลัง 24 h) → ส่ง `gas_ready=0` 3 นาทีแรก | มีแล้ว |

### 1.3 Cloud (NETPIE, M5)
| ส่วน | หน้าที่ | สถานะ |
|---|---|---|
| device `gateway` | ส่ง `@msg/lensguard/data` + `@shadow/data/update` | มีแล้ว ทดสอบกับ NETPIE จริงแล้ว |
| device `ai` | subscribe data, publish fan | มีแล้ว |
| Feed (เก็บย้อนหลัง) | เปิดในพอร์ทัล, ใช้โชว์ว่าข้อมูลถูกเก็บบน cloud จริง | ต้องทำ (กดเปิดเท่านั้น) |
| Freeboard dashboard | ใช้เว็บของเราแทน (หัวข้อ 1.5) ทำ Freeboard ขั้นต่ำไว้สำรอง 1 หน้า | ทางเลือก |

### 1.4 AI service (`ai/`) — Python บนโน้ตบุ๊กหรือ Raspberry Pi 4
| ส่วน | หน้าที่ | สถานะ |
|---|---|---|
| `features.py` | สร้างฟีเจอร์จากค่าที่วัดได้เท่านั้น (`records_to_minutes` ใช้ร่วมทั้ง train/serve) | มีแล้ว |
| `train.py` | RandomForest / LinearRegression, แบ่ง train/test ตามเวลา, รายงาน MAE | มีแล้ว (M4) |
| `service.py` Engine | รับระเบียน, ทำนาย +10 นาที ทุกนาที, สั่งพัดลม, log | มีแล้ว |
| `controller.py` | ทำนายหรือค่าจริง ≥ 55 → ON, ค้างจน RH จริง ≤ 45 → OFF | มีแล้ว |
| `alerts.py` | rh_high, door_open, silent/online, silica, reboot → Telegram | มีแล้ว (M6) |
| `dashboard.py` + `web/index.html` | เว็บ UX (กราฟ RH, สถานะกล่อง, alert, event) | มีแล้ว |
| `prepare.py` | live.jsonl → CSV สำหรับเทรนด้วยข้อมูลจริง | มีแล้ว |
| โมเดลจากข้อมูลจริง | ตอนนี้เทรนจาก synthetic เท่านั้น ต้องเก็บ ≥ 48 h แล้วเทรนใหม่ | ต้องทำ (สัปดาห์ 2–3) |
| รันเป็น service (systemd บน Pi) | ให้ AI กลับมาเองหลังไฟดับ: `deploy/install_pi.sh` + `deploy/lensguard-ai.service` (คู่มือ `docs/deploy_pi.md`) | มีแล้ว (รอทดสอบบน Pi จริง) |

### 1.5 เครื่องมือทดสอบและวัดผล (`tools/`)
`mqtt_simulator.py` (จำลอง gateway+node ตรรกะเดียวกับเฟิร์มแวร์), `metrics.py` (delivery rate, latency, uptime, MAE), `telegram_test.py`, `netpie.py` (TLS pin, .env) — มีแล้ว, CI รันทุก PR

---

## 2. ข้อผิดพลาดที่จะเจอ และวิธีรับมือ (เรียงตามอุปกรณ์)

เกณฑ์ที่ใช้ตลอด: **ตรวจจับได้ → ไม่ทำให้ระบบล้ม → กลับมาเองได้ → มีร่องรอยบน cloud/log**

### 2.1 DHT22 (node)
| อาการ | สาเหตุที่พบบ่อย | การรับมือ | สถานะ |
|---|---|---|---|
| อ่านได้ NaN / checksum ผิด | สายยาว, ไม่มี pull-up 10k, อ่านถี่กว่า 2 s | ส่งแพ็กเก็ตด้วย `valid=0` ค่าก่อนหน้าไม่ถูกทับ, gateway ไม่เอาเข้า fan logic, dashboard แสดง "เซ็นเซอร์ผิดพลาด" | มีแล้ว |
| ค้างค่าเดิมนาน (sensor ตาย/หลุด) | หลุดจาก breadboard | AI service: RH และอุณหภูมิเท่ากันเป๊ะ ≥ 30 นาที → Telegram `sensor_stuck` | มีแล้ว |
| RH ชน 99.9% หรือเพี้ยนหลังโดนความชื้นสูง | DHT22 อิ่มตัว ฟื้นช้า | จำกัดค่าไว้ 0–100, ถือว่า valid แต่ติดธง `rh_sat=1` ถ้า ≥ 99 ติดต่อกัน; รายงานใน sensor accuracy | ต้องทำ |
| DHT22 กับ DS18B20 อุณหภูมิต่างกันมาก (> 2 °C) | DHT22 คุณภาพต่ำ / วางคนละจุด | เป็น metric "sensor accuracy" ตาม proposal ไม่ต้องแก้ แต่เก็บ `temp_gap` เป็นฟีเจอร์ AI | มีแล้ว (ฟีเจอร์) |

### 2.2 Reed switch (node)
| อาการ | สาเหตุ | การรับมือ | สถานะ |
|---|---|---|---|
| สัญญาณกระเด้ง (bounce) หลายครั้งต่อการเปิด 1 ครั้ง | ธรรมชาติของ reed | ISR → queue → `doorTask` debounce 50 ms แล้วอ่านสถานะจริงอีกครั้ง | มีแล้ว |
| แม่เหล็กห่างเกินไป อ่านว่า "เปิด" ตลอด | ติดตั้งผิดตำแหน่ง | alert door_open จะยิงใน 2 นาที → ผู้ใช้รู้ทันที; ตอนติดตั้งใช้ serial log ตรวจ | มีแล้ว (alert) |
| เปิดประตูช่วงส่งแพ็กเก็ตอยู่ | race | door event มีคิวแยก, `reason=1` ลัดคิวข้างหน้าแพ็กเก็ตปกติ | มีแล้ว |

### 2.3 ESP-NOW (node ↔ gateway)
| อาการ | สาเหตุ | การรับมือ | สถานะ |
|---|---|---|---|
| ไม่มี ACK | ช่องสัญญาณไม่ตรง (hotspot เลือกช่องเอง) | retry 3 ครั้ง → scan ช่อง 1–13 → ล็อกช่องที่เจอ | มีแล้ว |
| แพ็กเก็ตซ้ำ | ACK หาย node ส่งซ้ำ | gateway กรองด้วย (`boot_id`,`seq`), นับ `rx_dup` | มีแล้ว |
| แพ็กเก็ตหายถาวร | ระยะไกล / กล่องพลาสติกหนา / สัญญาณรบกวน | นับจาก seq ที่กระโดด → delivery rate; ถ้า > 90 s ไม่มีข้อมูล gateway ปิดพัดลมกล่องนั้น (`no_data`) + Telegram "silent" | มีแล้ว |
| gateway รีบูต ระหว่าง node ส่ง | ไฟตก/OTA | node ไม่มี state ค้าง, seq ของ node ไม่รีเซ็ต, `boot_id` ของ gateway ไม่เกี่ยว → ต่อเนื่องเอง | มีแล้ว |
| node รีบูต | ไฟตก | `seq` เริ่ม 1 ใหม่แต่ `boot_id` ใหม่ → ไม่ถูกมองเป็นซ้ำ, ส่ง `reason=2` | มีแล้ว |
| กล่องสองใบส่งพร้อมกันชนกัน | broadcast | ความถี่ต่ำ (ทุก 30 s) + retry พอ; ถ้า delivery < 95% ใส่ `GATEWAY_MAC` จริงเป็น unicast และเลื่อม offset การส่งกล่อง B ไป 15 s | ทดสอบจริง |

### 2.4 พัดลม DC + MOSFET (gateway, M2/M7)
ลำดับการตัดสินใจใน `fanTask` (ข้อบนชนะข้อล่าง):
1. OTA กำลังทำ → OFF
2. เปิดครบ 30 นาที → OFF + cooldown 5 นาที; ถ้า RH ลด < 3% ตลอดรอบ → event `silica` → Telegram "เปลี่ยน/อบ silica"
3. คำสั่ง AI อายุ < 2 นาที → ทำตาม
4. ไม่มีข้อมูลกล่องนั้น 90 s → OFF
5. fallback: RH ≥ 55 → ON, RH ≤ 45 → OFF

| อาการ | สาเหตุ | การรับมือ | สถานะ |
|---|---|---|---|
| AI ล่ม/เน็ตหลุด แล้วพัดลมค้าง ON | ไม่มีคำสั่งใหม่ | TTL 2 นาที → ตกไป fallback ตามค่า RH จริง | มีแล้ว |
| พัดลมเปิดไม่หยุดเพราะ silica อิ่มตัว | RH ไม่ลง | timeout 30 นาที + cooldown + alert silica | มีแล้ว |
| ไฟตก (brown-out) ตอนพัดลมสตาร์ท | พัดลม 12V/5V กินกระแสพุ่ง, ใช้ไฟร่วมกับ ESP32 | ซอฟต์แวร์: boot event `BROWNOUT` → Telegram; ฮาร์ดแวร์: แยกรางไฟพัดลมกับบอร์ด / ใส่ C 470 µF ที่ขาพัดลม, ไดโอด flyback ตาม BOM | มีแล้ว (ตรวจจับ) / ฮาร์ดแวร์ต้องทำ |
| MOSFET ค้าง ON ช่วงบูต/รีเซ็ต | gate ลอยก่อน `setup()` ทำงาน | ซอฟต์แวร์ตั้ง OUTPUT LOW ใน `setup()` แล้ว; ฮาร์ดแวร์ต้องใส่ R 10k gate→GND (GPIO26/27 ลอยช่วงบูตราว 100 ms) | มีแล้ว / ฮาร์ดแวร์ต้องทำ |
| เปิด-ปิดถี่ (chatter) รอบ 55/45 | ค่า RH แกว่ง | hysteresis 10% อยู่แล้ว + AI ตัดสินใจทุก 1 นาทีเท่านั้น | มีแล้ว |
| พัดลมหมุนแต่ RH ไม่ลงเลยตั้งแต่ต้น | ลมไม่ผ่าน silica / ถุงอุดตัน | event silica จะจับได้ภายใน 30 นาที; ตอนประกอบตรวจทิศทางลม | มีแล้ว |

### 2.5 DS18B20 / LDR / MQ-135 (gateway)
| อาการ | สาเหตุ | การรับมือ | สถานะ |
|---|---|---|---|
| DS18B20 อ่านได้ −127 (`DEVICE_DISCONNECTED`) | ไม่มี 4.7k pull-up, สายหลุด | ส่ง record โดยไม่ใส่ `temp_out`; AI เติมค่าจากอุณหภูมิ DHT22 ของกล่องแทน (`features.py`) | มีแล้ว |
| LDR อ่านค่าเพี้ยนตอน Wi-Fi ส่ง | ใช้ ADC2 | ใช้ ADC1 (GPIO34/35) เท่านั้น | มีแล้ว |
| ADC ไม่เป็นเชิงเส้น/noise | ESP32 ADC | เฉลี่ย 8 ครั้ง (`ADC_SAMPLES`), attenuation 11 dB; เราใช้ LDR แค่ "สว่าง/มืด" ไม่ต้องการ lux จริง | มีแล้ว |
| MQ-135 ค่าสูงผิดปกติช่วงแรก | heater ยังไม่อุ่น | ธง `gas_ready=0` 3 นาทีแรกหลังบูต (`MQ_WARMUP_MS`); ผู้ใช้ค่า gas ต้องเช็คธงนี้ | มีแล้ว |
| MQ-135 กินกระแส ~150 mA ตลอดเวลา | heater | คิดในงบไฟ: อะแดปเตอร์ ≥ 2A ตาม BOM | ไม่ต้องแก้ |

### 2.6 Wi-Fi / MQTT / TLS (gateway ↔ NETPIE, E4 E5)
| อาการ | สาเหตุ | การรับมือ | สถานะ |
|---|---|---|---|
| hotspot หาย/เน็ตหลุด | มือถือเปลี่ยนเครือข่าย | เก็บ 120 ระเบียนล่าสุดใน ring buffer, ส่งย้อนหลังตามลำดับ, นับ `buf_lost` ถ้าล้น; Wi-Fi หาย 5 นาที → `WiFi.begin()` ใหม่ | มีแล้ว |
| Wi-Fi ติดแต่ MQTT ต่อไม่ได้นาน | stack ค้าง / NETPIE ล่ม | ครบ 15 นาที → reboot ตัวเอง (boot event `SW`) | มีแล้ว |
| TLS fingerprint ไม่ตรง | NETPIE เปลี่ยน cert / เพิ่ม backend | log fingerprint ที่เจอ, ไม่ส่ง token; แก้ด้วยการเพิ่มค่าใน `NETPIE_CERT_SHA256_LIST` แล้ว OTA; ฉุกเฉิน build `-DNETPIE_TLS_PIN=0` | มีแล้ว |
| ClientID ชนกัน (เปิด 2 ตัวพร้อมกัน) | ใช้ device เดียวกันทั้ง gateway และ simulator | NETPIE จะเตะตัวเก่าออกวนไปเรื่อย ๆ → `mqtt_connects` พุ่ง; กฎทีม: gateway จริง, simulator, AI ใช้คนละ device | มีแล้ว (กฎ) |
| NTP ยังไม่ sync | เพิ่งบูต | `ts=0` → ฝั่ง AI ใช้เวลาที่รับ (`rx_ms`) แทน | มีแล้ว |
| JSON ยาวเกิน buffer | เพิ่มฟิลด์ heartbeat | `JSON_MAX` 320 / MQTT buffer 1024; `enqueue()` ทิ้ง record ที่ยาวเกินพร้อม log `[pub] record too long` (ไม่ crash ไม่ส่ง JSON พัง) → เวลาเพิ่มฟิลด์ให้ดู serial log | มีแล้ว |
| เวลา TLS handshake ค้าง | เครือข่ายช้า | task watchdog 60 s ครอบ cloudTask | มีแล้ว |

### 2.7 OTA (E6)
| อาการ | การรับมือ | สถานะ |
|---|---|---|
| อัปเดตกลางทางแล้วไฟดับ | ESP32 มี 2 OTA partition; บูตกลับ image เดิมถ้าใหม่ไม่สมบูรณ์ | มีแล้ว (ของ bootloader) |
| เฟิร์มแวร์ใหม่บูตวน | นับ crash reset ติดต่อกันใน RTC memory; ครบ 3 ครั้งโดยไม่เคยรันถึง 10 นาที → `Update.rollBack()` กลับ image เดิม, boot event `ROLLBACK` → Telegram | มีแล้ว (ยังไม่ได้ทดสอบบนบอร์ดจริง) |
| พัดลมทำงานระหว่าง OTA | `fanTask` ข้อ 1 ปิดพัดลม, task อื่นหยุดส่ง | มีแล้ว |
| OTA โดนคนอื่นในเครือข่ายเดียวกัน | รหัส `OTA_PASSWORD` ใน secrets.h | มีแล้ว |

### 2.8 AI service (Python)
| อาการ | การรับมือ | สถานะ |
|---|---|---|
| ไม่มีไฟล์โมเดล / โหลดไม่ได้ | ทำงานต่อแบบไม่มีคำสั่ง AI (logger+alert ยังทำ), gateway ใช้ fallback | มีแล้ว |
| ประวัติไม่พอ (< 16 นาที) หลังเริ่ม | ข้ามการทำนายกล่องนั้น → fallback | มีแล้ว |
| ข้อมูลมาไม่เรียงเวลา / ซ้ำ (จาก ring buffer ย้อนหลัง) | จัดเป็นถังรายนาที (`records_to_minutes`) ค่าใหม่ทับค่าเก่าในนาทีเดียวกัน | มีแล้ว |
| โปรเซสตาย | ย้ายไป Pi + systemd `Restart=always`; ตอนเริ่มใหม่ `preload()` จาก live.jsonl 24 h | ต้องทำ (systemd) |
| ค่าทำนายหลุดช่วง (เช่น 130%) | clamp 0–100 ก่อนตัดสินใจ, นับใน `stats["clamped"]` | มีแล้ว |
| ดิสก์เต็มจาก jsonl | หมุนไฟล์รายวัน `live-YYYYMMDD.jsonl` (ที่ 30 s × 3 ตัว ≈ 3 MB/วัน ไม่รีบ) | ทางเลือก |
| โมเดลเทรนจาก synthetic แต่ใช้กับของจริง | ตัวเลข MAE ปัจจุบันห้ามใส่รายงาน; เทรนใหม่จากข้อมูลจริง ≥ 48 h, เทียบ MAE กับ baseline "RH ไม่เปลี่ยน" | ต้องทำ |

### 2.9 Telegram (M6)
| อาการ | การรับมือ | สถานะ |
|---|---|---|
| API ตอบช้า/ล้มเหลว | timeout 10 s; ล้มเหลวแล้ว retry 5/15/45 s ในเธรดแยก, control loop ไม่รอ, service ไม่ตาย | มีแล้ว |
| alert ถี่เกิน (spam) | กฎต่อชนิด: rh_high ต้อง ≥ 60 สองครั้งติด, door_open หลัง 2 นาที, silent ครั้งเดียวจนกลับมา | มีแล้ว |
| token หลุด | token เคยถูกวางในแชท → `/revoke` กับ BotFather ก่อนส่งงาน แล้วอัปเดต .env | ต้องทำ |

### 2.10 Dashboard
| อาการ | การรับมือ | สถานะ |
|---|---|---|
| ไม่มีข้อมูลมานาน | การ์ดกล่องแสดง "ออฟไลน์" เมื่อเกิน 120 s, เวลาข้อมูลล่าสุดโชว์ตลอด | มีแล้ว |
| เปิดจากมือถือ | ตรวจที่ 375 px แล้ว | มีแล้ว |
| คนนอกเข้าถึง | เปิดเฉพาะใน LAN, ไม่มี credential ในหน้าเว็บ | มีแล้ว |

### 2.11 ไฟและระบบโดยรวม (M7, robustness)
| เหตุการณ์ | พฤติกรรมที่ต้องได้ | สถานะ |
|---|---|---|
| ไฟดับทั้งระบบแล้วกลับมา | ทุกบอร์ดบูตเอง, node หา gateway เอง, gateway ต่อ cloud เอง, AI กลับมาเอง (ต้อง systemd), boot event ขึ้น Telegram | เฟิร์มแวร์มีแล้ว / AI ต้องทำ |
| task ใดค้าง | watchdog 60 s reboot + boot event `TASK_WDT` | มีแล้ว |
| หน่วยความจำรั่ว | heartbeat ส่ง `heap`; ถ้าลดต่อเนื่องใน 24 h จะเห็นบน dashboard | มีแล้ว (ตรวจจับ) |
| ไม่มีไฟ 220 V | ใช้ 5 V/12 V DC ตาม BOM เท่านั้น | ตามแบบ |

---

## 3. สิ่งที่ยังต้องเพิ่มในโค้ด (สรุปจากตารางด้านบน เรียงตามลำดับที่ควรทำ)

| # | งาน | ไฟล์ | เจ้าของตาม proposal |
|---|---|---|---|
| 1 | R 10k gate→GND ที่ MOSFET ทั้งสองตัว + แยกรางไฟพัดลม (ฮาร์ดแวร์ ตอนประกอบ) | วงจร | Napat |
| 2 | ~~MQ-135 `gas_ready` + เฉลี่ย ADC~~ ทำแล้ว 15 พ.ย. | | |
| 3 | ~~OTA rollback เมื่อบูตวน~~ ทำแล้ว 15 พ.ย. — ต้องทดสอบบนบอร์ดจริง: flash image ที่ crash ใน `setup()` ผ่าน OTA แล้วดูว่ากลับมาเอง | `gateway/src/main.cpp` | Napat (ทดสอบ) |
| 4 | ~~AI clamp + sensor_stuck~~ ทำแล้ว 15 พ.ย. | | |
| 5 | ~~Telegram retry~~ ทำแล้ว 15 พ.ย. | | |
| 6 | ~~OTA ของ node~~ ตัดสินใจแล้ว: ทำเฉพาะ gateway เขียนไว้ในรายงาน | รายงาน | Teerapat |
| 7 | ~~systemd บน Pi~~ ทำแล้ว 16 พ.ย. (`deploy/`, `docs/deploy_pi.md`) — เพื่อนที่ดูแล Pi ทดสอบติดตั้งจริง | Pi | เจ้าของ Pi |
| 8 | หลังของมาถึง: calibrate LDR, วัด delivery rate จริง, ตัดสินใจ unicast MAC | ทั้งหมด | ทุกคน (test plan F1–F10) |

การทดสอบของแต่ละข้อผูกกับ `docs/test_plan.md` (N = node, E = ESP-NOW, F = full system) และ metric ใน `tools/metrics.py`
