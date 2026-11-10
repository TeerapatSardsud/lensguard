// LensGuard sensor node  (skeleton, NOT yet tested on real hardware)
// Tasks:  sensorTask  -> reads DHT22 every SAMPLE_PERIOD_MS or on door change
//         commTask    -> sends over ESP-NOW, waits for app-level ACK, retries
// Arduino-ESP32 core 2.0.x callback signatures (platform espressif32@6.9.0).
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "protocol.h"
#include "packet.h"

#if !SIMULATE_SENSOR
  #include <DHT.h>
  #define DHT_PIN 4
  DHT dht(DHT_PIN, DHT22);
#endif

#define REED_PIN 27            // reed switch to GND, INPUT_PULLUP

static const uint8_t gatewayMac[6] = GATEWAY_MAC;
static QueueHandle_t txQueue;                 // DataPacket items
static TaskHandle_t  sensorTaskHandle;
static volatile uint32_t lastAckSeq = 0;      // written in recv callback
static volatile bool     ackFlag = false;
static uint32_t seqCounter = 0;

// ---- statistics for the report (packet delivery) -------------------------
static uint32_t statSent = 0, statAcked = 0, statRetries = 0, statDropped = 0;

// ---- ISR: door change -> wake sensorTask ---------------------------------
void IRAM_ATTR onDoorChange() {
  BaseType_t woke = pdFALSE;
  vTaskNotifyGiveFromISR(sensorTaskHandle, &woke);
  if (woke) portYIELD_FROM_ISR();
}

// ---- ESP-NOW callbacks (core 2.0.x signatures) ----------------------------
void onRecv(const uint8_t *mac, const uint8_t *data, int len) {
  if (len == (int)sizeof(AckPacket) && data[0] == PKT_ACK) {
    AckPacket a;
    memcpy(&a, data, sizeof(a));
    lastAckSeq = a.seq;
    ackFlag = true;
  }
}
void onSent(const uint8_t *mac, esp_now_send_status_t status) { /* link-layer only; we rely on app ACK */ }

// ---- helpers ---------------------------------------------------------------
static void readSensor(float &t, float &h, uint8_t &ok) {
#if SIMULATE_SENSOR
  t = 27.0f + 2.0f * sinf(millis() / 60000.0f) + (random(-10, 10) / 20.0f);
  h = 55.0f + 8.0f * sinf(millis() / 90000.0f) + (random(-10, 10) / 10.0f);
  ok = 1;
#else
  t = dht.readTemperature();
  h = dht.readHumidity();
  ok = !(isnan(t) || isnan(h));
  if (!ok) { t = 0; h = 0; }
#endif
}

static void sensorTask(void *) {
  uint8_t lastDoor = digitalRead(REED_PIN) == HIGH ? 1 : 0;   // magnet away = open
  for (;;) {
    // wait for either the sample period or a door-change notification
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
    vTaskDelay(pdMS_TO_TICKS(30));                            // debounce
    DataPacket p = {};
    p.type = PKT_DATA;
    strncpy(p.node_id, NODE_ID, sizeof(p.node_id) - 1);
    readSensor(p.temp, p.rh, p.valid);
    p.door = digitalRead(REED_PIN) == HIGH ? 1 : 0;
    p.seq = ++seqCounter;
    if (p.door != lastDoor) { Serial.printf("[door] %s\n", p.door ? "OPEN" : "CLOSED"); lastDoor = p.door; }
    if (xQueueSend(txQueue, &p, 0) != pdTRUE) Serial.println("[warn] tx queue full, reading dropped");
  }
}

static bool sendWithRetry(const DataPacket &p) {
  for (int attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    ackFlag = false;
    esp_err_t r = esp_now_send(gatewayMac, (const uint8_t *)&p, sizeof(p));
    statSent++;
    if (r != ESP_OK) Serial.printf("[tx] esp_now_send error %d\n", r);
    uint32_t t0 = millis();
    while (millis() - t0 < ACK_TIMEOUT_MS) {
      if (ackFlag && lastAckSeq == p.seq) { statAcked++; return true; }
      vTaskDelay(pdMS_TO_TICKS(5));
    }
    statRetries++;
  }
  statDropped++;
  return false;
}

static void commTask(void *) {
  DataPacket p;
  for (;;) {
    if (xQueueReceive(txQueue, &p, portMAX_DELAY) == pdTRUE) {
      bool ok = sendWithRetry(p);
      Serial.printf("[tx] seq=%lu T=%.1f RH=%.1f door=%u -> %s | sent=%lu acked=%lu retries=%lu dropped=%lu\n",
                    (unsigned long)p.seq, p.temp, p.rh, p.door, ok ? "ACK" : "FAIL",
                    (unsigned long)statSent, (unsigned long)statAcked,
                    (unsigned long)statRetries, (unsigned long)statDropped);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.printf("\nLensGuard node %s (simulate=%d)\n", NODE_ID, SIMULATE_SENSOR);
  pinMode(REED_PIN, INPUT_PULLUP);
#if !SIMULATE_SENSOR
  dht.begin();
#endif

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);   // must equal gateway's channel
  Serial.print("MAC: "); Serial.println(WiFi.macAddress());

  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW init failed"); ESP.restart(); }
  esp_now_register_recv_cb(onRecv);
  esp_now_register_send_cb(onSent);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, gatewayMac, 6);
  peer.channel = ESPNOW_CHANNEL;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) Serial.println("add_peer failed");

  txQueue = xQueueCreate(20, sizeof(DataPacket));
  xTaskCreate(sensorTask, "sensor", 4096, nullptr, 2, &sensorTaskHandle);
  xTaskCreate(commTask,   "comm",   4096, nullptr, 3, nullptr);
  attachInterrupt(digitalPinToInterrupt(REED_PIN), onDoorChange, CHANGE);
}

void loop() { vTaskDelay(portMAX_DELAY); }
