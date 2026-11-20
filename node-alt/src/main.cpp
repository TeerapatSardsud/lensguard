// LensGuard sensor node (checkpoint version, PATCHED): DHT22 + reed switch -> ESP-NOW -> gateway.
//
// What this version adds over the original checkpoint build (see README.md):
//   1. GATEWAY_MAC placeholder -> automatic broadcast fallback, so a fresh flash works
//      before anyone has read the gateway's MAC off its serial log.
//   2. Channel search: a phone hotspot picks its own Wi-Fi channel, and the original
//      hardcoded channel 1. If the hotspot sat elsewhere the node transmitted into
//      silence and looked dead. Now: after MAX_RETRIES without an ACK it walks
//      channels 1..13 until the gateway answers, then stays there.
//   3. Explicit `valid` flag on every record (repo protocol docs/message_format.md):
//      the original dropped temp/rh on a failed DHT read but the gateway still marked
//      the sample valid=1, so a sensor fault entered the dataset as a real reading -
//      which is exactly what corrupts the AI training labels and the MAE numbers.
//   4. One `type:"boot"` record per start, so the timeline shows when a box came up.
//   5. Periodic [stats] line (tx/acked/lost/retry/free heap) -> the delivery-rate and
//      long-run memory evidence the report asks for, straight from the serial log.
//
// Compile status: built with `pio run -e box_a` / `-e box_b` (platformio 6.2,
// espressif32 6.9.0). NOT yet verified on hardware — bench flash required.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <DHT.h>
#include <ArduinoJson.h>
#include "config.h"          // DHT_PIN / REED_PIN / GATEWAY_MAC / channel + timing

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

// Actual ESP-NOW destination: the configured MAC, or broadcast while it is still the
// placeholder from config.h (the gateway unicasts its ACK either way).
static uint8_t peerMac[6];
static bool usingBroadcast = false;
static uint8_t curChannel = ESPNOW_CHANNEL;

// delivery statistics (report evidence)
static uint32_t statTx = 0, statAcked = 0, statLost = 0, statRetries = 0, statChannelHops = 0;

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

// valid=0 means "this record carries no trustworthy temp/rh" (sensor read failed or a
// boot marker). The gateway forwards it and ai/features.py turns the minute into a gap
// instead of a measurement.
static void enqueueReading(float t, float h, int door, const char *type, int valid) {
  TxItem it;
  it.seq = ++seqCounter;
  StaticJsonDocument<192> doc;
  doc["node_id"] = NODE_ID;
  doc["seq"] = it.seq;
  doc["type"] = type;
  doc["valid"] = valid;
  if (!isnan(t)) doc["temp"] = t;
  if (!isnan(h)) doc["rh"] = h;
  doc["door"] = door;
  serializeJson(doc, it.json, sizeof(it.json));
  xQueueSend(txQueue, &it, 0);
}

static int doorOpen() { return digitalRead(REED_PIN) == HIGH ? 1 : 0; }

void taskSensor(void *) {
  static uint32_t cycles = 0;
  for (;;) {
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    bool ok = !isnan(h) && !isnan(t);
    if (ok) { lastRh = h; lastTemp = t; }
    else Serial.println("[dht] read failed -> publishing valid=0 (gap, not a reading)");
    enqueueReading(t, h, doorOpen(), "periodic", ok ? 1 : 0);
    if (++cycles % STATS_EVERY == 0) {
      Serial.printf("[stats] tx=%lu acked=%lu lost=%lu retries=%lu ch=%d heap=%u\n",
                    (unsigned long)statTx, (unsigned long)statAcked, (unsigned long)statLost,
                    (unsigned long)statRetries, (int)curChannel, (unsigned)ESP.getFreeHeap());
    }
    vTaskDelay(pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
  }
}

void taskDoor(void *) {
  uint8_t tick;
  for (;;) {
    if (xQueueReceive(doorIsrQueue, &tick, portMAX_DELAY) == pdTRUE) {
      vTaskDelay(pdMS_TO_TICKS(50));
      while (xQueueReceive(doorIsrQueue, &tick, 0) == pdTRUE) {}
      bool fresh = !isnan(lastRh);
      enqueueReading(lastTemp, lastRh, doorOpen(), "door", fresh ? 1 : 0);
    }
  }
}

// One send + ACK wait on the current channel.
static bool trySend(const TxItem &it) {
  xEventGroupClearBits(ackEvents, ACK_BIT);
  esp_now_send(peerMac, (const uint8_t *)it.json, strlen(it.json) + 1);
  TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(ACK_TIMEOUT_MS);
  while (xTaskGetTickCount() < deadline) {
    xEventGroupWaitBits(ackEvents, ACK_BIT, pdTRUE, pdFALSE, pdMS_TO_TICKS(20));
    if (lastAckSeq == it.seq) return true;
  }
  return false;
}

// The peer was added with channel = 0 ("use the current channel"), so moving the radio
// is enough; no peer delete/add dance.
static void setChannel(uint8_t ch) {
  if (ch == curChannel) return;
  if (esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE) == ESP_OK) {
    curChannel = ch;
    Serial.printf("[ch] switched to %u\n", ch);
  }
}

// Hotspot channel is not ours to choose: walk 1..13 until the gateway answers.
static bool scanForGateway(const TxItem &it) {
#if CHANNEL_SCAN
  uint8_t start = curChannel;
  for (uint8_t ch = 1; ch <= 13; ch++) {
    if (ch == start) continue;
    setChannel(ch);
    if (trySend(it)) {
      statChannelHops++;
      Serial.printf("[ch] gateway found on channel %u (was %u)\n", ch, start);
      return true;
    }
  }
  setChannel(start);
#endif
  return false;
}

void taskTx(void *) {
  TxItem it;
  for (;;) {
    if (xQueueReceive(txQueue, &it, portMAX_DELAY) != pdTRUE) continue;
    statTx++;
    bool acked = false;
    for (int attempt = 0; attempt < MAX_RETRIES && !acked; attempt++) {
      if (attempt) statRetries++;
      acked = trySend(it);
    }
    if (!acked) {
      acked = scanForGateway(it);          // most likely cause of silence: wrong channel
      if (!acked) Serial.printf("[tx] no ACK after %d tries and a channel scan - lost seq=%u\n",
                                MAX_RETRIES, (unsigned)it.seq);
    }
    if (acked) statAcked++; else statLost++;
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

  static const uint8_t PLACEHOLDER[6] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};
  memcpy(peerMac, GATEWAY_MAC, 6);
  usingBroadcast = (memcmp(peerMac, PLACEHOLDER, 6) == 0);
  if (usingBroadcast) {
    memset(peerMac, 0xFF, 6);
    Serial.println("[cfg] GATEWAY_MAC is still the placeholder -> sending broadcast "
                   "(set the real MAC from the gateway's boot log when convenient)");
  }
  Serial.printf("[cfg] node=%s ch=%u sample=%lums scan=%d\n",
                NODE_ID, (unsigned)curChannel, (unsigned long)SAMPLE_PERIOD_MS, CHANNEL_SCAN);

  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW init failed"); ESP.restart(); }
  esp_now_register_recv_cb(onRecv);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, peerMac, 6);
  peer.channel = 0;              // 0 = follow the current Wi-Fi channel (see setChannel)
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) Serial.println("add peer failed");

  attachInterrupt(digitalPinToInterrupt(REED_PIN), doorIsr, CHANGE);

  enqueueReading(NAN, NAN, doorOpen(), "boot", 0);   // one marker per start -> timeline

  xTaskCreate(taskSensor, "sensor", 4096, nullptr, 1, nullptr);
  xTaskCreate(taskTx,     "tx",     4096, nullptr, 2, nullptr);
  xTaskCreate(taskDoor,   "door",   4096, nullptr, 3, nullptr);
}

void loop() { vTaskDelay(portMAX_DELAY); }
