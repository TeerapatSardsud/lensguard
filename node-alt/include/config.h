#pragma once

#ifndef NODE_ID
#define NODE_ID "box_A"
#endif

// ---------- Wiring ----------
// CONFIRMED WITH THE BUILDER 2026-11-20: the DHT22 DATA wire on both real boxes is on
// GPIO25 — the wire is too short to reach GPIO4, which is what the repo's
// docs/pin_map.md, the Wokwi diagram and node/src/main.cpp (#define DHT_PIN 4) assume.
// So keep 25 here; if the repo node/ firmware is ever flashed onto these boxes, its
// DHT_PIN has to become 25 or every DHT read returns NaN.
#define DHT_PIN   25
#define REED_PIN  33   // door closed (magnet near) = LOW with INPUT_PULLUP (matches repo docs)

// ---------- ESP-NOW ----------
// MAC ที่ gateway พิมพ์ตอนบูต (ใส่ให้ตรง)
// ถ้ายังใส่ค่า placeholder AA:BB:CC:DD:EE:FF โค้ดจะสลับไปส่ง broadcast
// (FF:FF:FF:FF:FF:FF) อัตโนมัติ — gateway ยังตอบ ACK unicast กลับมา
// จึงแฟลชได้ทันทีโดยไม่ต้องรู้ MAC ก่อน (broadcast กิน airtime มากกว่าเท่านั้น)
static const uint8_t GATEWAY_MAC[6] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};

// ช่อง Wi-Fi ที่ gateway พิมพ์บอกหลังเชื่อม hotspot สำเร็จ
// เปิด CHANNEL_SCAN ไว้: ถ้าส่งไม่ผ่าน ACK ครบ MAX_RETRIES แล้วโหนดจะไล่หาช่อง
// 1..13 เอง (hotspot มือถือเลือกช่องเอง — hardcode ช่องตายตัวคือเหตุ
// "node ส่งแต่ gateway ไม่ได้ยิน" ที่เจอบ่อยที่สุด)
#define ESPNOW_CHANNEL 1
#define CHANNEL_SCAN   1

#define SAMPLE_PERIOD_MS 5000    // ตอนถ่ายคลิป ใช้ 5 วินาที (ปกติ 30000)
#define ACK_TIMEOUT_MS   150
#define MAX_RETRIES      4

// พิมพ์สรุป [stats] ทุกกี่รอบที่อ่านเซนเซอร์ (ตัวเลข delivery-rate ไว้ใส่ report)
#define STATS_EVERY      60
