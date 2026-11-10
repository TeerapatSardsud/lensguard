// LensGuard sensor node — compiles (PlatformIO, Arduino-ESP32 2.0.17); NOT yet tested on real hardware.
//
// FreeRTOS tasks (E1)                         priority  why
//   doorTask   reed switch events (from ISR)     4      a door event must leave immediately
//   commTask   ESP-NOW send + ACK/retry (E4)     3      drain the queue before new readings pile up
//   sensorTask DHT22 every SAMPLE_PERIOD_MS      2      periodic, not time critical
// Communication: reedISR --doorQueue--> doorTask --txQueue--> commTask <--notify(seq)-- onRecv (ACK)
//                sensorTask --txQueue--> commTask;  lastReading shared under readingMutex
// Self-recovery: all three tasks are registered with the task watchdog (WDT_TIMEOUT_S) -> reboot if one hangs.
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_task_wdt.h>
#include "protocol.h"
#include "packet.h"

#ifndef NODE_ID
  #error "NODE_ID must be set in platformio.ini (box_A / box_B)"
#endif

#if !SIMULATE_SENSOR
  #include <DHT.h>
  #define DHT_PIN 4
  DHT dht(DHT_PIN, DHT22);
#endif

#define REED_PIN 33            // reed switch to GND, INPUT_PULLUP: magnet near (lid closed) = LOW (docs/pin_map.md)

static uint8_t gatewayMac[6] = GATEWAY_MAC;
static QueueHandle_t txQueue;          // DataPacket
static QueueHandle_t doorQueue;        // uint32_t tick of the edge (from ISR)
static SemaphoreHandle_t readingMutex; // protects lastT/lastH/lastValid/seqCounter
static TaskHandle_t commTaskHandle;

static float    lastT = 0, lastH = 0;
static uint8_t  lastValid = 0;
static uint32_t seqCounter = 0;
static uint16_t bootId = 0;
static uint8_t  channel = ESPNOW_CHANNEL;

// statistics printed on the serial log (packet delivery metric for the report)
static uint32_t statPackets = 0, statAcked = 0, statRetries = 0, statDropped = 0, statScans = 0;

// ---- ISR: only push the event; all work happens in doorTask (E2: deferred processing) ----
void IRAM_ATTR onDoorEdge() {
  uint32_t tick = xTaskGetTickCountFromISR();
  BaseType_t woke = pdFALSE;
  xQueueSendFromISR(doorQueue, &tick, &woke);
  if (woke) portYIELD_FROM_ISR();
}

// ---- ESP-NOW receive (runs in the Wi-Fi task, not an ISR) ----
void onRecv(const uint8_t *mac, const uint8_t *data, int len) {
  if (len == (int)sizeof(AckPacket) && data[0] == PKT_ACK) {
    AckPacket a;
    memcpy(&a, data, sizeof(a));
    xTaskNotify(commTaskHandle, a.seq, eSetValueWithOverwrite);
  }
}

// ---- helpers ----
static uint8_t readDoor() { return digitalRead(REED_PIN) == HIGH ? 1 : 0; }   // 1 = open

static DataPacket makePacket(uint8_t reason) {
  DataPacket p = {};
  p.type = PKT_DATA;
  strncpy(p.node_id, NODE_ID, sizeof(p.node_id) - 1);
  p.boot_id = bootId;
  p.door = readDoor();
  p.reason = reason;
  xSemaphoreTake(readingMutex, portMAX_DELAY);
  p.seq = ++seqCounter;
  p.temp = lastT; p.rh = lastH; p.valid = lastValid;
  xSemaphoreGive(readingMutex);
  return p;
}

static void readSensor() {
  float t, h; uint8_t ok;
#if SIMULATE_SENSOR
  t = 27.0f + 2.0f * sinf(millis() / 60000.0f) + (random(-10, 10) / 20.0f);
  h = 55.0f + 8.0f * sinf(millis() / 90000.0f) + (random(-10, 10) / 10.0f);
  ok = 1;
#else
  t = dht.readTemperature();
  h = dht.readHumidity();
  ok = !(isnan(t) || isnan(h));
  if (!ok) { t = 0; h = 0; Serial.println("[dht] read failed"); }
#endif
  xSemaphoreTake(readingMutex, portMAX_DELAY);
  lastT = t; lastH = h; lastValid = ok;
  xSemaphoreGive(readingMutex);
}

static void setChannel(uint8_t ch) {
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);
  channel = ch;
}

// one transmission + wait for the matching ACK
static bool sendOnce(DataPacket &p, uint8_t tryNo) {
  p.tries = tryNo;
  xTaskNotifyStateClear(nullptr);
  esp_err_t r = esp_now_send(gatewayMac, (const uint8_t *)&p, sizeof(p));
  if (r != ESP_OK) { Serial.printf("[tx] esp_now_send error %d\n", r); return false; }
  uint32_t acked = 0;
  TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(ACK_TIMEOUT_MS);
  for (;;) {
    TickType_t now = xTaskGetTickCount();
    if (now >= deadline) return false;
    if (xTaskNotifyWait(0, 0, &acked, deadline - now) == pdTRUE && acked == p.seq) return true;
  }
}

static bool sendWithRetry(DataPacket &p) {
  for (uint8_t attempt = 1; attempt <= MAX_RETRIES + 1; attempt++) {
    if (sendOnce(p, attempt)) return true;
    statRetries++;
    vTaskDelay(pdMS_TO_TICKS(10 * attempt));   // small back-off
  }
  return false;
}

// gateway not answering -> it may be on another channel (phone hotspot). Try 1..13.
static bool findGatewayChannel(DataPacket &p) {
  statScans++;
  uint8_t start = channel;
  for (uint8_t i = 0; i < 13; i++) {
    uint8_t ch = (start + i) % 13 + 1;   // starts with the channel after the current one
    setChannel(ch);
    if (sendOnce(p, 1) || sendOnce(p, 2)) {
      Serial.printf("[scan] gateway found on channel %u\n", ch);
      return true;
    }
  }
  setChannel(start);
  Serial.println("[scan] gateway not found on any channel");
  return false;
}

// ---- tasks ----
static void sensorTask(void *) {
  TickType_t last = xTaskGetTickCount();
  esp_task_wdt_add(nullptr);
  for (;;) {
    esp_task_wdt_reset();
    readSensor();
    DataPacket p = makePacket(statPackets == 0 ? REASON_BOOT : REASON_PERIODIC);
    if (xQueueSend(txQueue, &p, 0) != pdTRUE) Serial.println("[warn] tx queue full, reading dropped");
    vTaskDelayUntil(&last, pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
  }
}

static void doorTask(void *) {
  uint8_t lastDoor = readDoor();
  uint32_t tick;
  esp_task_wdt_add(nullptr);
  for (;;) {
    esp_task_wdt_reset();
    if (xQueueReceive(doorQueue, &tick, pdMS_TO_TICKS(1000)) != pdTRUE) continue;
    vTaskDelay(pdMS_TO_TICKS(DOOR_DEBOUNCE_MS));                  // let the contact settle
    while (xQueueReceive(doorQueue, &tick, 0) == pdTRUE) {}       // drop bounce edges
    uint8_t door = readDoor();
    if (door == lastDoor) continue;                               // bounce only, no real change
    lastDoor = door;
    Serial.printf("[door] %s\n", door ? "OPEN" : "CLOSED");
    DataPacket p = makePacket(REASON_DOOR);
    xQueueSendToFront(txQueue, &p, 0);                            // door events jump the queue
  }
}

static void commTask(void *) {
  DataPacket p;
  uint32_t consecutiveFails = 0;
  esp_task_wdt_add(nullptr);
  for (;;) {
    esp_task_wdt_reset();
    if (xQueueReceive(txQueue, &p, pdMS_TO_TICKS(1000)) != pdTRUE) continue;
    statPackets++;
    bool ok = sendWithRetry(p);
    if (!ok && ++consecutiveFails >= FAILS_BEFORE_SCAN) ok = findGatewayChannel(p);
    if (ok) { statAcked++; consecutiveFails = 0; } else statDropped++;
    Serial.printf("[tx] seq=%lu ch=%u T=%.1f RH=%.1f door=%u tries=%u -> %s | pkts=%lu acked=%lu retries=%lu dropped=%lu scans=%lu\n",
                  (unsigned long)p.seq, channel, p.temp, p.rh, p.door, p.tries, ok ? "ACK" : "FAIL",
                  (unsigned long)statPackets, (unsigned long)statAcked, (unsigned long)statRetries,
                  (unsigned long)statDropped, (unsigned long)statScans);
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
  bootId = (uint16_t)esp_random();
  setChannel(ESPNOW_CHANNEL);
  Serial.print("MAC: "); Serial.println(WiFi.macAddress());

  bool zeroMac = true;
  for (int i = 0; i < 6; i++) zeroMac &= gatewayMac[i] == 0;
  if (zeroMac) { memset(gatewayMac, 0xFF, 6); Serial.println("GATEWAY_MAC not set -> using broadcast"); }

  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW init failed"); ESP.restart(); }
  esp_now_register_recv_cb(onRecv);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, gatewayMac, 6);
  peer.channel = 0;              // 0 = whatever channel the radio is on (needed for scanning)
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) Serial.println("add_peer failed");

  esp_task_wdt_init(WDT_TIMEOUT_S, true);       // re-configure Arduino's 5 s default; panic -> reboot
  readingMutex = xSemaphoreCreateMutex();
  txQueue   = xQueueCreate(20, sizeof(DataPacket));
  doorQueue = xQueueCreate(16, sizeof(uint32_t));
  xTaskCreate(commTask,   "comm",   4096, nullptr, 3, &commTaskHandle);
  xTaskCreate(doorTask,   "door",   3072, nullptr, 4, nullptr);
  xTaskCreate(sensorTask, "sensor", 4096, nullptr, 2, nullptr);
  attachInterrupt(digitalPinToInterrupt(REED_PIN), onDoorEdge, CHANGE);
}

void loop() { vTaskDelete(nullptr); }   // all work is in tasks
