// LensGuard gateway — compiles (PlatformIO, Arduino-ESP32 2.0.17); NOT yet tested on real hardware.
//
// FreeRTOS tasks (E1)                                            priority  why
//   espnowTask  ESP-NOW rx -> ACK -> de-duplicate -> records        4      node waits only 150 ms for the ACK
//   fanTask     per-box fan: AI cmd / fallback / timeout (M7)       3      control must not wait for the network
//   sensorTask  DS18B20, LDR, MQ-135 every SAMPLE_PERIOD_MS         2      periodic, not time critical
//   cloudTask   Wi-Fi, MQTT/TLS (E5), offline buffer (E4), OTA (E6) 1      blocking network I/O, lowest
// Queues: onRecv -> rxQueue -> espnowTask;  everyone -> pubQueue -> cloudTask;
//         MQTT callback (inside cloudTask) -> fanCmdQueue -> fanTask.  Box state shared under stateMutex.
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ArduinoOTA.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <sys/time.h>
#include "protocol.h"
#include "packet.h"
#include "secrets.h"           // copy gateway/include/secrets.example.h -> secrets.h (git-ignored)

#if !SIMULATE_SENSOR
  #include <OneWire.h>
  #include <DallasTemperature.h>
  OneWire oneWire(18);
  DallasTemperature ds18(&oneWire);
#endif

#define LDR_PIN 34             // ADC1 only (ADC2 is unusable while Wi-Fi is on)
#define MQ_PIN  35
static const uint8_t FAN_PINS[NUM_BOXES] = {25, 26};   // box_A, box_B MOSFET gates
static const char   *BOX_IDS[NUM_BOXES]  = NODE_IDS;
#define BOX_STALE_MS (3UL * SAMPLE_PERIOD_MS)           // no data for this long -> treat RH as unknown

// ---------------------------------------------------------------- shared state
struct RxItem  { uint8_t mac[6]; DataPacket pkt; };
struct PubItem { char json[JSON_MAX]; };
struct FanCmd  { int8_t box; int8_t on; };

struct BoxState {
  float    rh = 0;            uint32_t lastSeenMs = 0;   bool seen = false;
  uint16_t bootId = 0;        uint32_t lastSeq = 0;
  bool     fanOn = false;     uint32_t fanOnSinceMs = 0; float rhAtFanStart = 0;
  uint32_t cooldownUntilMs = 0;
  int8_t   aiCmd = -1;        uint32_t aiCmdMs = 0;      // -1 = no AI command yet
};
static BoxState boxes[NUM_BOXES];
static SemaphoreHandle_t stateMutex;
static QueueHandle_t rxQueue, pubQueue, fanCmdQueue;
static volatile bool otaInProgress = false;

static WiFiClientSecure tlsClient;
static PubSubClient mqtt(tlsClient);

// offline ring buffer (owned by cloudTask only, fixed-size: no heap fragmentation)
static char ring[OFFLINE_BUFFER][JSON_MAX];
static int ringHead = 0, ringCount = 0;
static uint32_t statBuffered = 0, statBufferDropped = 0;

// ---------------------------------------------------------------- helpers
static int boxIndex(const char *id) {
  for (int i = 0; i < NUM_BOXES; i++) if (strcmp(id, BOX_IDS[i]) == 0) return i;
  return -1;                     // unknown/simulated node: still forwarded, no fan
}

static uint64_t nowEpochMs() {   // 0 until NTP has synced
  struct timeval tv; gettimeofday(&tv, nullptr);
  if (tv.tv_sec < 1700000000) return 0;
  return (uint64_t)tv.tv_sec * 1000ULL + tv.tv_usec / 1000;
}

static void enqueue(JsonDocument &d) {
  PubItem it;
  size_t n = serializeJson(d, it.json, sizeof(it.json));
  if (n == 0 || n >= sizeof(it.json) - 1) { Serial.println("[pub] record too long, dropped"); return; }
  if (xQueueSend(pubQueue, &it, pdMS_TO_TICKS(50)) != pdTRUE) Serial.println("[pub] queue full, dropped");
}

static void emitEvent(const char *event, int box, const char *detail, float value) {
  JsonDocument d;
  d["node_id"] = "gateway"; d["ts"] = nowEpochMs(); d["event"] = event;
  if (box >= 0) d["box"] = BOX_IDS[box];
  if (detail) d["detail"] = detail;
  d["value"] = serialized(String(value, 1));
  enqueue(d);
}

// ---------------------------------------------------------------- ESP-NOW
void onRecv(const uint8_t *mac, const uint8_t *data, int len) {     // Wi-Fi task context
  if (len != (int)sizeof(DataPacket) || data[0] != PKT_DATA) return;
  RxItem it; memcpy(it.mac, mac, 6); memcpy(&it.pkt, data, sizeof(DataPacket));
  xQueueSend(rxQueue, &it, 0);
}

static void sendAck(const uint8_t *mac, uint32_t seq) {
  if (!esp_now_is_peer_exist(mac)) {
    esp_now_peer_info_t p = {};
    memcpy(p.peer_addr, mac, 6); p.channel = 0; p.ifidx = WIFI_IF_STA; p.encrypt = false;
    esp_now_add_peer(&p);
  }
  AckPacket a = { PKT_ACK, seq };
  esp_now_send(mac, (const uint8_t *)&a, sizeof(a));
}

static void espnowTask(void *) {
  RxItem it;
  for (;;) {
    xQueueReceive(rxQueue, &it, portMAX_DELAY);
    const DataPacket &p = it.pkt;
    sendAck(it.mac, p.seq);                          // ACK first: the node is waiting
    char id[9] = {0}; memcpy(id, p.node_id, 8);
    int b = boxIndex(id);

    bool duplicate = false;
    if (b >= 0) {
      xSemaphoreTake(stateMutex, portMAX_DELAY);
      BoxState &s = boxes[b];
      if (s.seen && s.bootId == p.boot_id && p.seq <= s.lastSeq) duplicate = true;  // our ACK was lost
      else {
        if (s.seen && s.bootId != p.boot_id) Serial.printf("[rx] %s rebooted\n", id);
        s.seen = true; s.bootId = p.boot_id; s.lastSeq = p.seq; s.lastSeenMs = millis();
        if (p.valid) s.rh = p.rh;
      }
      xSemaphoreGive(stateMutex);
    }
    Serial.printf("[rx] %s seq=%lu RH=%.1f door=%u tries=%u%s\n", id, (unsigned long)p.seq, p.rh, p.door,
                  p.tries, duplicate ? " (duplicate, not forwarded)" : "");
    if (duplicate) continue;

    JsonDocument d;
    d["node_id"] = id; d["seq"] = p.seq; d["boot"] = p.boot_id; d["ts"] = nowEpochMs();
    if (p.valid) { d["temp"] = serialized(String(p.temp, 1)); d["rh"] = serialized(String(p.rh, 1)); }
    d["valid"] = p.valid; d["door"] = p.door; d["reason"] = p.reason; d["tries"] = p.tries;
    enqueue(d);
  }
}

// ---------------------------------------------------------------- fan control
static void setFan(int b, bool on, const char *why) {          // caller holds stateMutex
  BoxState &s = boxes[b];
  if (on == s.fanOn) return;
  s.fanOn = on;
  digitalWrite(FAN_PINS[b], on ? HIGH : LOW);
  if (on) { s.fanOnSinceMs = millis(); s.rhAtFanStart = s.rh; }
  Serial.printf("[fan] %s %s (%s)\n", BOX_IDS[b], on ? "ON" : "OFF", why);
  emitEvent("fan", b, why, on ? 1 : 0);
}

static void fanTask(void *) {
  FanCmd cmd;
  for (;;) {
    // wake every second, or immediately when the AI sends a command
    if (xQueueReceive(fanCmdQueue, &cmd, pdMS_TO_TICKS(1000)) == pdTRUE && cmd.box >= 0) {
      xSemaphoreTake(stateMutex, portMAX_DELAY);
      boxes[cmd.box].aiCmd = cmd.on; boxes[cmd.box].aiCmdMs = millis();
      xSemaphoreGive(stateMutex);
    }
    uint32_t now = millis();
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    for (int b = 0; b < NUM_BOXES; b++) {
      BoxState &s = boxes[b];
      if (otaInProgress) { setFan(b, false, "ota"); continue; }
      // safety timeout (M7) + silica-gel saturation check
      if (s.fanOn && now - s.fanOnSinceMs >= FAN_MAX_ON_MS) {
        float drop = s.rhAtFanStart - s.rh;
        setFan(b, false, "timeout");
        s.cooldownUntilMs = now + FAN_COOLDOWN_MS;
        if (drop < SILICA_MIN_DROP) emitEvent("silica", b, "rh_drop_too_small", drop);
        continue;
      }
      if ((int32_t)(s.cooldownUntilMs - now) > 0) continue;              // cooling down, stay off
      bool fresh = s.seen && now - s.lastSeenMs < BOX_STALE_MS;
      if (s.aiCmd >= 0 && now - s.aiCmdMs < AI_CMD_TTL_MS) setFan(b, s.aiCmd == 1, "ai");
      else if (!fresh) setFan(b, false, "no_data");
      else if (s.rh >= RH_FAN_ON) setFan(b, true, "fallback");
      else if (s.rh <= RH_FAN_OFF) setFan(b, false, "fallback");
    }
    xSemaphoreGive(stateMutex);
  }
}

// ---------------------------------------------------------------- gateway sensors
static void sensorTask(void *) {
  uint32_t seq = 0;
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    float tOut; int ldr, gas; bool tOk = true;
#if SIMULATE_SENSOR
    tOut = 30.0f + 2.0f * sinf(millis() / 80000.0f); ldr = 300 + random(-30, 30); gas = 120 + random(-10, 10);
#else
    ds18.requestTemperatures(); tOut = ds18.getTempCByIndex(0);
    tOk = tOut != DEVICE_DISCONNECTED_C;
    ldr = analogRead(LDR_PIN); gas = analogRead(MQ_PIN);            // raw 12-bit ADC; calibrate later
#endif
    JsonDocument d;
    d["node_id"] = "gateway"; d["seq"] = ++seq; d["ts"] = nowEpochMs();
    if (tOk) d["temp_out"] = serialized(String(tOut, 1));
    d["ldr"] = ldr; d["gas"] = gas;
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    for (int b = 0; b < NUM_BOXES; b++) d[String("fan_") + BOX_IDS[b]] = boxes[b].fanOn ? 1 : 0;
    xSemaphoreGive(stateMutex);
    d["buffered"] = ringCount;   // read without lock: diagnostic only
    enqueue(d);
    vTaskDelayUntil(&last, pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
  }
}

// ---------------------------------------------------------------- cloud
static void onMqtt(char *topic, byte *payload, unsigned int len) {   // runs inside cloudTask
  // payload: {"node_id":"box_A","fan":1}
  JsonDocument d;
  if (strcmp(topic, NETPIE_TOPIC_FAN) != 0 || deserializeJson(d, payload, len)) return;
  FanCmd c = { (int8_t)boxIndex(d["node_id"] | ""), (int8_t)((d["fan"] | 0) ? 1 : 0) };
  Serial.printf("[mqtt] fan cmd %s -> %d\n", (const char *)(d["node_id"] | "?"), c.on);
  if (c.box >= 0) xQueueSend(fanCmdQueue, &c, 0);
}

static bool mqttConnect() {
  Serial.print("[mqtt] connecting... ");
  tlsClient.stop();
  tlsClient.setInsecure();                          // chain can't be verified (private CA) ->
  if (!tlsClient.connect(NETPIE_HOST, NETPIE_PORT_TLS)) { Serial.println("TLS connect failed"); return false; }
#if NETPIE_TLS_PIN
  static const char *pins[] = NETPIE_CERT_SHA256_LIST;       // ...so pin the certificates instead (E5)
  bool pinned = false;
  for (const char *fp : pins) if (tlsClient.verify(fp, nullptr)) { pinned = true; break; }
  if (!pinned) {
    Serial.println("certificate fingerprint not in NETPIE_CERT_SHA256_LIST - not sending credentials.\n"
                   "  Check it with openssl (see protocol.h) and add it if it is really NETPIE's.");
    tlsClient.stop(); return false;
  }
#else
  Serial.print("(TLS pin disabled) ");
#endif
  // PubSubClient reuses the already-open TLS socket. NETPIE: ClientID / Token / Secret
  if (!mqtt.connect(NETPIE_CLIENT_ID, NETPIE_TOKEN, NETPIE_SECRET)) {
    Serial.printf("MQTT fail rc=%d\n", mqtt.state()); return false;
  }
  mqtt.subscribe(NETPIE_TOPIC_FAN);
  Serial.println("ok");
  return true;
}

// raw record -> @msg/lensguard/data ; flat keys -> @shadow/data/update (dashboard/feed)
static bool publishRecord(const char *json) {
  if (!mqtt.connected() || !mqtt.publish(NETPIE_TOPIC_DATA, json)) return false;
  JsonDocument in, out;
  if (deserializeJson(in, json) || in["event"].is<const char *>()) return true;   // events: msg only
  String prefix = String((const char *)(in["node_id"] | "unknown")) + "_";
  JsonObject data = out["data"].to<JsonObject>();
  for (JsonPair kv : in.as<JsonObject>()) {
    if (strcmp(kv.key().c_str(), "node_id") == 0) continue;
    data[prefix + kv.key().c_str()] = kv.value();
  }
  String body; serializeJson(out, body);
  return mqtt.publish(NETPIE_TOPIC_SHADOW, body.c_str());
}

static void ringPush(const char *json) {
  if (ringCount == OFFLINE_BUFFER) { ringHead = (ringHead + 1) % OFFLINE_BUFFER; ringCount--; statBufferDropped++; }
  strlcpy(ring[(ringHead + ringCount) % OFFLINE_BUFFER], json, JSON_MAX);
  ringCount++; statBuffered++;
}

static void flushRing() {
  int sent = 0;
  while (ringCount > 0 && mqtt.connected() && sent < 10) {         // a few per pass, keep mqtt.loop() alive
    if (!publishRecord(ring[ringHead])) break;
    ringHead = (ringHead + 1) % OFFLINE_BUFFER; ringCount--; sent++;
    vTaskDelay(pdMS_TO_TICKS(30));
  }
  if (sent) Serial.printf("[buf] re-sent %d buffered records, %d left\n", sent, ringCount);
}

static void setupOta() {
  ArduinoOTA.setHostname("lensguard-gw");
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.onStart([]() { otaInProgress = true; Serial.println("[ota] start (fans off)"); });
  ArduinoOTA.onEnd([]()   { Serial.println("[ota] done, rebooting"); });
  ArduinoOTA.onError([](ota_error_t e) { otaInProgress = false; Serial.printf("[ota] error %u\n", e); });
  ArduinoOTA.begin();
}

static void cloudTask(void *) {
  uint32_t lastWifiTry = 0, lastMqttTry = 0;
  bool otaReady = false, timeConfigured = false;
  PubItem it;
  for (;;) {
    bool wifiUp = WiFi.status() == WL_CONNECTED;
    if (!wifiUp && millis() - lastWifiTry > 10000) {
      lastWifiTry = millis(); Serial.println("[wifi] reconnecting"); WiFi.reconnect();
    }
    if (wifiUp && !timeConfigured) {
      configTime(7 * 3600, 0, "pool.ntp.org", "time.navy.mi.th"); timeConfigured = true;
      int ch = WiFi.channel();
      Serial.printf("[wifi] %s channel=%d (nodes find it by scanning)\n", WiFi.localIP().toString().c_str(), ch);
    }
    if (wifiUp && !otaReady) { setupOta(); otaReady = true; }
    if (otaReady) ArduinoOTA.handle();

    if (wifiUp && !mqtt.connected() && millis() - lastMqttTry > 5000) { lastMqttTry = millis(); mqttConnect(); }
    mqtt.loop();

    // drain records produced by other tasks: publish now, or buffer while offline (E4)
    while (xQueueReceive(pubQueue, &it, 0) == pdTRUE) {
      if (ringCount > 0 || !publishRecord(it.json)) {               // keep order: buffer behind older data
        ringPush(it.json);
        Serial.printf("[buf] offline, buffered %d/%d (lost to overflow: %lu)\n", ringCount, OFFLINE_BUFFER,
                      (unsigned long)statBufferDropped);
      }
    }
    if (ringCount > 0 && mqtt.connected()) flushRing();
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

// ---------------------------------------------------------------- setup
void setup() {
  for (int b = 0; b < NUM_BOXES; b++) { pinMode(FAN_PINS[b], OUTPUT); digitalWrite(FAN_PINS[b], LOW); }
  Serial.begin(115200); delay(300);
  Serial.printf("\nLensGuard gateway (simulate=%d)\n", SIMULATE_SENSOR);
#if !SIMULATE_SENSOR
  ds18.begin();
  analogSetPinAttenuation(LDR_PIN, ADC_11db); analogSetPinAttenuation(MQ_PIN, ADC_11db);
#endif
  stateMutex  = xSemaphoreCreateMutex();
  rxQueue     = xQueueCreate(20, sizeof(RxItem));
  pubQueue    = xQueueCreate(16, sizeof(PubItem));
  fanCmdQueue = xQueueCreate(8, sizeof(FanCmd));

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                 // keep the radio awake so ESP-NOW frames are not missed
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("MAC: %s  (optional: put it in GATEWAY_MAC)\n", WiFi.macAddress().c_str());
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);   // ESP-NOW needs the final channel

  mqtt.setServer(NETPIE_HOST, NETPIE_PORT_TLS);
  mqtt.setCallback(onMqtt);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(30);

  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW init failed"); ESP.restart(); }
  esp_now_register_recv_cb(onRecv);

  xTaskCreate(espnowTask, "espnow", 4096, nullptr, 4, nullptr);
  xTaskCreate(fanTask,    "fan",    4096, nullptr, 3, nullptr);
  xTaskCreate(sensorTask, "sensor", 4096, nullptr, 2, nullptr);
  xTaskCreate(cloudTask,  "cloud",  12288, nullptr, 1, nullptr);
}

void loop() { vTaskDelete(nullptr); }
