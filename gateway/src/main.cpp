// LensGuard gateway (skeleton, NOT yet tested on real hardware)
//  - ESP-NOW receive -> ACK -> JSON -> MQTT (TLS) to NETPIE
//  - offline ring buffer while Wi-Fi/MQTT is down
//  - local sensors (DS18B20 outside temp, LDR, MQ-135) published as node_id "gateway"
//  - fan on GPIO25 via MOSFET: command from cloud (AI) with local RH-threshold fallback
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <time.h>
#include "protocol.h"
#include "packet.h"
#include "secrets.h"           // copy gateway/include/secrets.example.h -> secrets.h

#if !SIMULATE_SENSOR
  #include <OneWire.h>
  #include <DallasTemperature.h>
  OneWire oneWire(18);
  DallasTemperature ds18(&oneWire);
#endif

#define FAN_PIN 25
#define LDR_PIN 34             // ADC1 (ADC2 is unusable while Wi-Fi is on)
#define MQ_PIN  35

// ---- local fallback rule (works even if cloud/AI is down) ----
#define RH_FAN_ON   60.0f
#define RH_FAN_OFF  55.0f
#define AI_CMD_TTL_MS 120000   // after this long without an AI command, use fallback

struct RxItem { uint8_t mac[6]; DataPacket pkt; };
static QueueHandle_t rxQueue;

static WiFiClientSecure tlsClient;
static PubSubClient mqtt(tlsClient);

// ---- offline ring buffer of JSON strings ----
static String ring[OFFLINE_BUFFER];
static int ringHead = 0, ringCount = 0;
static void ringPush(const String &s) {
  ring[(ringHead + ringCount) % OFFLINE_BUFFER] = s;
  if (ringCount < OFFLINE_BUFFER) ringCount++; else ringHead = (ringHead + 1) % OFFLINE_BUFFER;
}

static bool fanOn = false;
static uint32_t lastAiCmdMs = 0;
static float lastRh[2] = {0, 0}; static char lastId[2][8];

static void setFan(bool on, const char *why) {
  if (on != fanOn) Serial.printf("[fan] %s (%s)\n", on ? "ON" : "OFF", why);
  fanOn = on; digitalWrite(FAN_PIN, on ? HIGH : LOW);
}

// ---- ESP-NOW receive: only enqueue, do the work in loop() ----
void onRecv(const uint8_t *mac, const uint8_t *data, int len) {
  if (len != (int)sizeof(DataPacket) || data[0] != PKT_DATA) return;
  RxItem it; memcpy(it.mac, mac, 6); memcpy(&it.pkt, data, sizeof(DataPacket));
  xQueueSend(rxQueue, &it, 0);
}

static void sendAck(const uint8_t *mac, uint32_t seq) {
  if (!esp_now_is_peer_exist(mac)) {
    esp_now_peer_info_t p = {}; memcpy(p.peer_addr, mac, 6); p.channel = 0; p.encrypt = false;  // 0 = current channel
    esp_now_add_peer(&p);
  }
  AckPacket a = { PKT_ACK, seq };
  esp_now_send(mac, (const uint8_t *)&a, sizeof(a));
}

// ---- MQTT ----
static void onMqtt(char *topic, byte *payload, unsigned int len) {
  // NOTE: the exact NETPIE downlink topic (@msg/...) must be verified in the NETPIE portal.
  String t(topic), body; for (unsigned i = 0; i < len; i++) body += (char)payload[i];
  Serial.printf("[mqtt] %s : %s\n", t.c_str(), body.c_str());
  if (t == "@msg/fan") { lastAiCmdMs = millis(); setFan(body == "on" || body == "1", "cloud/AI"); }
}

static bool mqttConnect() {
  if (mqtt.connected()) return true;
  Serial.print("[mqtt] connecting... ");
  // NETPIE: ClientID / Token (as username) / Secret (as password)
  if (mqtt.connect(NETPIE_CLIENT_ID, NETPIE_TOKEN, NETPIE_SECRET)) {
    mqtt.subscribe("@msg/fan");
    Serial.println("ok");
    return true;
  }
  Serial.printf("fail rc=%d\n", mqtt.state());
  return false;
}

static void publishJson(const String &json) {
  // NETPIE shadow format: {"data": {...}}
  String body = "{\"data\":" + json + "}";
  if (mqtt.connected() && mqtt.publish(NETPIE_TOPIC, body.c_str())) return;
  ringPush(json);
  Serial.printf("[buf] offline, buffered (%d/%d)\n", ringCount, OFFLINE_BUFFER);
}

static void flushRing() {
  while (ringCount > 0 && mqtt.connected()) {
    String body = "{\"data\":" + ring[ringHead] + "}";
    if (!mqtt.publish(NETPIE_TOPIC, body.c_str())) break;
    ringHead = (ringHead + 1) % OFFLINE_BUFFER; ringCount--;
    delay(20);
  }
}

static String toJson(const char *id, uint32_t seq, float t, float rh, int door) {
  JsonDocument d;
  d["node_id"] = id; d["seq"] = seq; d["ts"] = (uint32_t)time(nullptr);
  d["temp"] = serialized(String(t, 1)); d["rh"] = serialized(String(rh, 1)); d["door"] = door;
  String s; serializeJson(d, s); return s;
}

// ---- gateway's own sensors ----
static void publishLocal() {
  static uint32_t seq = 0;
  float tOut, lux, gas;
#if SIMULATE_SENSOR
  tOut = 30.0f + 2.0f * sinf(millis() / 80000.0f); lux = 300 + random(-30, 30); gas = 120 + random(-10, 10);
#else
  ds18.requestTemperatures(); tOut = ds18.getTempCByIndex(0);
  lux = analogRead(LDR_PIN); gas = analogRead(MQ_PIN);          // raw ADC; calibrate later
#endif
  JsonDocument d;
  d["node_id"] = "gateway"; d["seq"] = ++seq; d["ts"] = (uint32_t)time(nullptr);
  d["temp_out"] = serialized(String(tOut, 1)); d["ldr"] = (int)lux; d["gas"] = (int)gas; d["fan"] = fanOn ? 1 : 0;
  String s; serializeJson(d, s); publishJson(s);
}

static void applyFallbackRule() {
  if (millis() - lastAiCmdMs < AI_CMD_TTL_MS) return;           // AI is in charge
  float rh = max(lastRh[0], lastRh[1]);
  if (rh >= RH_FAN_ON) setFan(true, "fallback RH high");
  else if (rh <= RH_FAN_OFF && rh > 0) setFan(false, "fallback RH ok");
}

void setup() {
  Serial.begin(115200); delay(300);
  Serial.println("\nLensGuard gateway");
  pinMode(FAN_PIN, OUTPUT); digitalWrite(FAN_PIN, LOW);
#if !SIMULATE_SENSOR
  ds18.begin();
#endif
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Wi-Fi");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print('.'); }
  Serial.printf("\nMAC: %s\n", WiFi.macAddress().c_str());
  if (WiFi.status() == WL_CONNECTED) {
    int ch = WiFi.channel();
    Serial.printf("Wi-Fi channel = %d  (ESPNOW_CHANNEL in protocol.h = %d)%s\n", ch, ESPNOW_CHANNEL,
                  ch == ESPNOW_CHANNEL ? "" : "  <-- MISMATCH: set ESPNOW_CHANNEL to this value on nodes!");
    configTime(7 * 3600, 0, "pool.ntp.org");
  }
  tlsClient.setInsecure();   // TODO(before final): pin the CA cert instead of skipping verification
  mqtt.setServer(NETPIE_HOST, NETPIE_PORT_TLS);
  mqtt.setCallback(onMqtt);
  mqtt.setBufferSize(512);

  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW init failed"); ESP.restart(); }
  esp_now_register_recv_cb(onRecv);
  rxQueue = xQueueCreate(20, sizeof(RxItem));
}

void loop() {
  static uint32_t lastLocal = 0, lastReconnect = 0;
  if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
  if (!mqtt.connected() && millis() - lastReconnect > 5000) {
    lastReconnect = millis();
    if (WiFi.status() == WL_CONNECTED && mqttConnect()) flushRing();
  }
  mqtt.loop();

  RxItem it;
  while (xQueueReceive(rxQueue, &it, 0) == pdTRUE) {
    sendAck(it.mac, it.pkt.seq);                                 // ACK first, then forward
    char id[9] = {0}; memcpy(id, it.pkt.node_id, 8);
    int slot = (id[strlen(id) - 1] == 'B') ? 1 : 0;              // box_A -> 0, box_B -> 1
    static uint32_t lastSeq[2] = {0, 0};
    bool duplicate = (it.pkt.seq == lastSeq[slot]);              // node retried because our ACK was lost
    lastSeq[slot] = it.pkt.seq;
    if (it.pkt.valid && !duplicate) {
      lastRh[slot] = it.pkt.rh;
      publishJson(toJson(id, it.pkt.seq, it.pkt.temp, it.pkt.rh, it.pkt.door));
    }
    Serial.printf("[rx] %s seq=%lu RH=%.1f door=%u\n", id, (unsigned long)it.pkt.seq, it.pkt.rh, it.pkt.door);
  }

  if (millis() - lastLocal > SAMPLE_PERIOD_MS) { lastLocal = millis(); publishLocal(); }
  applyFallbackRule();
}
