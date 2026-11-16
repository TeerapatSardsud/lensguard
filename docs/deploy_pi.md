# ติดตั้ง AI service บน Raspberry Pi 4

Pi ทำหน้าที่ **แทนโน้ตบุ๊กเท่านั้น**: รัน `ai/service.py` (ทำนายความชื้น สั่งพัดลม แจ้ง Telegram และเสิร์ฟ dashboard)
ESP32 ทั้ง 3 ตัวและ NETPIE ไม่เปลี่ยน gateway ไม่รู้ด้วยซ้ำว่า AI รันอยู่ที่ไหน เพราะคุยกันผ่าน NETPIE

```
กล่อง A/B ─ESP-NOW─▶ gateway ─MQTT/TLS─▶ NETPIE ◀─MQTT/TLS─ Raspberry Pi (ai/service.py)
                                                              └─ http://<pi-ip>:8080  dashboard
                                                              └─ Telegram
```

## สิ่งที่ต้องมี
- Raspberry Pi 4, **Raspberry Pi OS 64-bit** (Bookworm, Python 3.11 ติดมาแล้ว) และต่ออินเทอร์เน็ตได้ (Wi-Fi หรือสาย)
- รหัส NETPIE ของ device `ai` (Client ID / Token / Secret) และ Telegram bot token + chat id
  → อยู่ในไฟล์ `.env` ของ Teerapat ส่งให้ทางช่องทางส่วนตัว **ห้ามวางในกลุ่มหรือ commit**

## ติดตั้ง (ครั้งแรก ประมาณ 10 นาที)
```bash
sudo apt-get install -y git
git clone https://github.com/TeerapatSardsud/lensguard.git
cd lensguard
bash deploy/install_pi.sh        # รอบแรกจะหยุดให้กรอก .env
nano .env                        # ใส่ AI_NETPIE_* และ TELEGRAM_* (ช่อง NETPIE_* ของ gateway ปล่อยว่างได้)
bash deploy/install_pi.sh        # รอบสองติดตั้งจริงและเปิด service
```
สคริปต์ทำให้ครบ: ติดตั้ง Python packages ใน `.venv`, เทรนโมเดลเริ่มต้นถ้ายังไม่มี, รัน selftest,
ติดตั้ง systemd service ชื่อ `lensguard-ai` ที่ **เปิดเองทุกครั้งที่ Pi บูตและรีสตาร์ทเองถ้าล่ม** (ตอบข้อ robustness)

## ตรวจว่าทำงาน
```bash
sudo systemctl status lensguard-ai     # ต้องขึ้น active (running)
journalctl -u lensguard-ai -f          # ต้องเห็น [mqtt] connected ... และ [cmd] box_A fan=... ทุกนาทีเมื่อมีข้อมูล
```
เปิด dashboard จากมือถือ/คอมใน Wi-Fi เดียวกัน: `http://<ip ของ Pi>:8080` (สคริปต์พิมพ์ที่อยู่ให้ตอนจบ)

ทดสอบโดยไม่ต้องมีฮาร์ดแวร์: รัน simulator จากเครื่องใครก็ได้ที่มี `.env` ของ gateway
`python tools/mqtt_simulator.py --period 2` แล้วดูว่า Pi พิมพ์ `[cmd]` และ dashboard ขยับ

## อัปเดตเมื่อโค้ดเปลี่ยน
```bash
cd ~/lensguard && git pull && bash deploy/install_pi.sh
```

## ปัญหาที่เจอบ่อย
| อาการ | แก้ |
|---|---|
| `pip install` ช้ามากหรือพัง | ต้องเป็น OS 64-bit (`uname -m` ต้องได้ `aarch64`) ไม่งั้น scikit-learn ไม่มี wheel |
| `[mqtt] connected rc=5` / ต่อไม่ติด | รหัสใน `.env` ผิด หรือใช้ device เดียวกับที่โน้ตบุ๊กต่ออยู่ (ClientID ต่อซ้ำไม่ได้ ปิดตัวบนโน้ตบุ๊กก่อน) |
| `certificate fingerprint mismatch` | NETPIE เปลี่ยน cert: เช็คด้วย `openssl` ตาม `common/protocol.h` แล้วเพิ่มใน `tools/netpie.py` |
| Telegram ไม่มา | `.venv/bin/python tools/telegram_test.py` ต้องส่งได้ก่อน |
| dashboard เปิดไม่ได้จากมือถือ | ต้องอยู่ Wi-Fi เดียวกับ Pi; `sudo ss -ltnp | grep 8080` ต้องเห็น python |
| Pi ดับแล้วเปิดใหม่ | ไม่ต้องทำอะไร service เปิดเอง กราฟโหลดข้อมูล 24 ชม. ล่าสุดจาก `ai/data/live.jsonl` กลับมา |

## สิ่งที่อยู่บน Pi แล้วห้ามลืมสำรอง
`ai/data/live.jsonl` = ข้อมูลจริงที่จะใช้เทรนโมเดลและวัด metric (คัดลอกออกมาเป็นระยะด้วย `scp pi@<ip>:~/lensguard/ai/data/*.jsonl .`)
