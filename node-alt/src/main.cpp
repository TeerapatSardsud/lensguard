// LensGuard sensor node (checkpoint version): DHT22 + reed switch -> ESP-NOW -> gateway.
// NOT yet compiled or tested on hardware.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <DHT.h>
#include <ArduinoJson.h>
#include "config.h"

#define DHT_PIN   25
#define REED_PIN  33   // door closed (magnet near) = LOW with INPUT_PULLUP

struct AckMsg { uint32_t seq; };
struct TxItem { char json[192]; uint32_t seq; };

DHT dht(DHT_PIN, DHT22);
QueueHandle_t doorIsrQueue;
QueueHandle_t txQueue;
EventGroupHandle_t ackEvents;
const EventBits_t ACK_BIT = BIT0;

volatile uint32_t lastAckSeq = 0;
volatile float lastTemp = NAN, lastRh = NAN;
static uint32_t seqCounter = 0;

void IRAM_ATTR doorIsr() {
  BaseType_t woken = pdFALSE;
  uint8_t tick = 1;
  xQueueSendFromISR(doorIsrQueue, &tick, &woken);
  if (woken) portYIELD_FROM_ISR();
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onRecv(const esp_now_recv_info_t *, const uint8_t *data, int len)
#else
void onRecv(const uint8_t *, const uint8_t *data, int len)
#endif
{
  if (len == (int)sizeof(AckMsg)) {
    AckMsg a;
    memcpy(&a, data, sizeof(a));
    lastAckSeq = a.seq;
    xEventGroupSetBits(ackEvents, ACK_BIT);
  }
}

static void enqueueReading(float t, float h, int door, const char *type) {
  TxItem it;
  it.seq = ++seqCounter;
  StaticJsonDocument<192> doc;
  doc["node_id"] = NODE_ID;
  doc["seq"] = it.seq;
  doc["type"] = type;
  if (!isnan(t)) doc["temp"] = t;
  if (!isnan(h)) doc["rh"] = h;
  doc["door"] = door;
  serializeJson(doc, it.json, sizeof(it.json));
  xQueueSend(txQueue, &it, 0);
}

static int doorOpen() { return digitalRead(REED_PIN) == HIGH ? 1 : 0; }

void taskSensor(void *) {
  for (;;) {
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    if (!isnan(h) && !isnan(t)) { lastRh = h; lastTemp = t; }
    enqueueReading(t, h, doorOpen(), "periodic");
    vTaskDelay(pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
  }
}

void taskDoor(void *) {
  uint8_t tick;
  for (;;) {
    if (xQueueReceive(doorIsrQueue, &tick, portMAX_DELAY) == pdTRUE) {
      vTaskDelay(pdMS_TO_TICKS(50));
      while (xQueueReceive(doorIsrQueue, &tick, 0) == pdTRUE) {}
      enqueueReading(lastTemp, lastRh, doorOpen(), "door");
    }
  }
}

void taskTx(void *) {
  TxItem it;
  for (;;) {
    if (xQueueReceive(txQueue, &it, portMAX_DELAY) != pdTRUE) continue;
    bool acked = false;
    for (int attempt = 0; attempt < MAX_RETRIES && !acked; attempt++) {
      xEventGroupClearBits(ackEvents, ACK_BIT);
      esp_now_send(GATEWAY_MAC, (const uint8_t *)it.json, strlen(it.json) + 1);
      TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(ACK_TIMEOUT_MS);
      while (!acked && xTaskGetTickCount() < deadline) {
        xEventGroupWaitBits(ackEvents, ACK_BIT, pdTRUE, pdFALSE, pdMS_TO_TICKS(20));
        if (lastAckSeq == it.seq) acked = true;
      }
    }
    Serial.printf("[tx] %s -> %s\n", it.json, acked ? "ACK" : "NO ACK");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(REED_PIN, INPUT_PULLUP);
  dht.begin();

  doorIsrQueue = xQueueCreate(8, sizeof(uint8_t));
  txQueue = xQueueCreate(16, sizeof(TxItem));
  ackEvents = xEventGroupCreate();

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  Serial.print("Node MAC: "); Serial.println(WiFi.macAddress());

  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW init failed"); ESP.restart(); }
  esp_now_register_recv_cb(onRecv);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, GATEWAY_MAC, 6);
  peer.channel = ESPNOW_CHANNEL;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) Serial.println("add peer failed");

  attachInterrupt(digitalPinToInterrupt(REED_PIN), doorIsr, CHANGE);

  xTaskCreate(taskSensor, "sensor", 4096, nullptr, 1, nullptr);
  xTaskCreate(taskTx,     "tx",     4096, nullptr, 2, nullptr);
  xTaskCreate(taskDoor,   "door",   4096, nullptr, 3, nullptr);
}

void loop() { vTaskDelay(portMAX_DELAY); }