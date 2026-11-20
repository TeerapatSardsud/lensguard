// LensGuard checkpoint gateway: ESP-NOW from nodes -> Wi-Fi -> NETPIE shadow.
// NOT yet compiled or tested on hardware.
//
// 2026-11-20 @msg patch (AI-loop): on top of the shadow path this now also
//  - publishes full records to @msg/lensguard/data (same protocol as the repo
//    gateway: node_id/seq/ts/temp/rh/door/valid + gateway heartbeat with uptime_s),
//    which NETPIE now delivers since the devices joined a device group;
//  - subscribes @msg/lensguard/fan so the AI service can command the fans.
// Shadow keys/format are unchanged, so the NETPIE console feed stays identical.

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_now.h>
#include <time.h>
#include "secrets.h"

#define NETPIE_HOST "mqtt.netpie.io"
#define NETPIE_PORT 1883
#define TOPIC_DATA "@msg/lensguard/data"
#define TOPIC_FAN  "@msg/lensguard/fan"

struct AckMsg { uint32_t seq; };
struct RxItem { uint8_t mac[6]; char data[200]; };

static QueueHandle_t rxQueue;
static WiFiClient net;
static PubSubClient mqtt(net);
static uint32_t lastSeq[2] = {0, 0};
static const char *NODE_IDS[2] = {"box_A", "box_B"};
static bool fanCmd[2] = {false, false};   // last AI decision (logged + echoed to shadow)

// millis()-based fallback when SNTP hasn't run yet -> DriftCorrector on the Pi
// rebases anyway, and a 1970 stamp would be worse than none.
static uint64_t nowMs() {
  time_t t = time(nullptr);
  return (t > 1600000000) ? (uint64_t)t * 1000ULL : (uint64_t)(millis() / 1000) * 1000ULL;
}

static void onFanCommand(char *topic, uint8_t *payload, unsigned len) {
  StaticJsonDocument<256> d;
  if (deserializeJson(d, payload, len)) { Serial.println("[fan] bad json"); return; }
  const char *id = d["node_id"] | "";
  int v = d["fan"] | -1;
  for (int i = 0; i < 2; i++)
    if (strcmp(id, NODE_IDS[i]) == 0 && v >= 0) {
      fanCmd[i] = (v != 0);
      Serial.printf("[fan] %s <= AI cmd %s (pred=%s)\n", id, fanCmd[i] ? "ON" : "OFF",
                    const char *(d["pred"] | "?"));
      // TODO hardware: drive the fan GPIO for box i here.
    }
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onEspNowRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  const uint8_t *mac = info->src_addr;
#else
void onEspNowRecv(const uint8_t *mac, const uint8_t *data, int len) {
#endif
  if (len <= 0 || len >= (int)sizeof(RxItem::data)) return;
  RxItem it;
  memcpy(it.mac, mac, 6);
  memcpy(it.data, data, len);
  it.data[len] = 0;
  xQueueSend(rxQueue, &it, 0);
}

static void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting Wi-Fi '%s' ...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(250);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("Wi-Fi OK, IP %s, channel %d  <-- set ESPNOW_CHANNEL on the nodes to this\n",
                  WiFi.localIP().toString().c_str(), WiFi.channel());
    configTime(7 * 3600, 0, "pool.ntp.org", "time.google.com");  // Asia/Bangkok; re-sync below
  }
}

static void ensureMqtt() {
  if (mqtt.connected() || WiFi.status() != WL_CONNECTED) return;
  static uint32_t last = 0;
  if (last != 0 && millis() - last < 5000) return;
  last = millis() ? millis() : 1;
  Serial.print("Connecting NETPIE ...");
  if (mqtt.connect(NETPIE_CLIENT_ID, NETPIE_TOKEN, NETPIE_SECRET)) {
    Serial.println(" connected");
    mqtt.subscribe(TOPIC_FAN);            // re-subscribe after every reconnect
  } else {
    Serial.printf(" failed, rc=%d\n", mqtt.state());
  }
}

static void publishShadow(const JsonDocument &doc) {
  char buf[300];
  serializeJson(doc, buf, sizeof(buf));
  bool ok = mqtt.connected() && mqtt.publish("@shadow/data/update", buf);
  Serial.printf("[NETPIE %s] %s\n", ok ? "ok" : "FAILED", buf);
}

static void handleNodeMessage(const RxItem &it) {
  StaticJsonDocument<256> in;
  if (deserializeJson(in, it.data)) { Serial.println("[rx] bad json"); return; }
  const char *id = in["node_id"] | "";
  int idx = -1;
  for (int i = 0; i < 2; i++) if (strcmp(id, NODE_IDS[i]) == 0) idx = i;
  if (idx < 0) { Serial.printf("[rx] unknown node '%s'\n", id); return; }
  uint32_t seq = in["seq"] | 0;

  if (!esp_now_is_peer_exist(it.mac)) {
    esp_now_peer_info_t p = {};
    memcpy(p.peer_addr, it.mac, 6);
    p.channel = 0;
    p.encrypt = false;
    esp_now_add_peer(&p);
  }
  AckMsg ack = {seq};
  esp_now_send(it.mac, (uint8_t *)&ack, sizeof(ack));

  if (seq == lastSeq[idx]) return;
  lastSeq[idx] = seq;

  Serial.printf("[rx] %s seq=%u type=%s\n", id, (unsigned)seq, (const char *)(in["type"] | "?"));

  StaticJsonDocument<384> out;
  JsonObject d = out.createNestedObject("data");
  String prefix = String(id) + "_";
  if (in.containsKey("temp")) d[prefix + "temp"] = in["temp"];
  if (in.containsKey("rh"))   d[prefix + "rh"] = in["rh"];
  d[prefix + "door"] = in["door"] | 0;
  d[prefix + "seq"] = seq;
  publishShadow(out);

  // full record -> @msg/lensguard/data (repo protocol; consumed by ai/service.py)
  StaticJsonDocument<384> rec;
  rec["node_id"] = id;
  rec["seq"] = seq;
  rec["ts"] = nowMs();
  if (in.containsKey("temp")) rec["temp"] = in["temp"];
  if (in.containsKey("rh"))   rec["rh"] = in["rh"];
  rec["door"] = in["door"] | 0;
  rec["valid"] = in["valid"] | 1;
  char rbuf[256];
  size_t n = serializeJson(rec, rbuf, sizeof(rbuf));
  bool okr = mqtt.connected() && mqtt.publish(TOPIC_DATA, rbuf, n);
  Serial.printf("[NETPIE @msg %s] %s\n", okr ? "ok" : "FAILED", rbuf);
}

void setup() {
  Serial.begin(115200);
  rxQueue = xQueueCreate(16, sizeof(RxItem));
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  Serial.print("Gateway MAC (put in the nodes' config.h): ");
  Serial.println(WiFi.macAddress());
  ensureWifi();
  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW init failed"); ESP.restart(); }
  esp_now_register_recv_cb(onEspNowRecv);
  mqtt.setServer(NETPIE_HOST, NETPIE_PORT);
  mqtt.setBufferSize(512);
  mqtt.setCallback(onFanCommand);
}

void loop() {
  ensureWifi();
  ensureMqtt();
  mqtt.loop();

  RxItem it;
  while (xQueueReceive(rxQueue, &it, 0) == pdTRUE) handleNodeMessage(it);

  static uint32_t lastBeat = 0;
  if (millis() - lastBeat > 10000) {
    lastBeat = millis();
    static uint32_t lastNtp = 0;                       // periodic re-sync: a one-shot SNTP at boot
    if (millis() - lastNtp > 15 * 60000UL) {           // free-runs on the ESP32 crystal (~15x drift
      lastNtp = millis();                              // observed on the other board)
      configTime(7 * 3600, 0, "pool.ntp.org", "time.google.com");
    }
    StaticJsonDocument<192> hb;
    JsonObject d = hb.createNestedObject("data");
    d["gw_uptime_s"] = (uint32_t)(millis() / 1000);
    d["box_A_fan"] = fanCmd[0] ? 1 : 0;   // echo of last AI cmd (console/debug only)
    d["box_B_fan"] = fanCmd[1] ? 1 : 0;
    publishShadow(hb);

    StaticJsonDocument<192> g;             // gateway heartbeat as a @msg record
    g["node_id"] = "gateway";
    g["ts"] = nowMs();
    g["uptime_s"] = (uint32_t)(millis() / 1000);
    char gbuf[160];
    size_t gn = serializeJson(g, gbuf, sizeof(gbuf));
    if (mqtt.connected()) mqtt.publish(TOPIC_DATA, gbuf, gn);
  }
  delay(5);
}