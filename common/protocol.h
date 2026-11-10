// Shared by node/ and gateway/. Contains NO secrets.
#pragma once

// --- ESP-NOW / Wi-Fi ---------------------------------------------------
// The node must transmit on the SAME channel the gateway's Wi-Fi uses. Phone hotspots pick their
// own channel, so the node starts on ESPNOW_CHANNEL and, if it gets no ACK, scans channels
// 1..13 until the gateway answers (see findGatewayChannel() in node/src/main.cpp).
#define ESPNOW_CHANNEL   6

// MAC address of the GATEWAY board (printed in the gateway's serial log at boot). Not a secret.
// All zeros = send to the broadcast address; the gateway still answers with a unicast ACK, so this
// works out of the box. Put the real MAC here once you know it (less airtime for other devices).
#define GATEWAY_MAC      {0x00, 0x00, 0x00, 0x00, 0x00, 0x00}

// --- reliability (E4) --------------------------------------------------
#define ACK_TIMEOUT_MS   150
#define MAX_RETRIES      3
#define FAILS_BEFORE_SCAN 2      // consecutive failed packets before the node re-scans channels

// --- timing ------------------------------------------------------------
#define SAMPLE_PERIOD_MS 30000   // 30 s between normal readings (node and gateway sensors)
#define DOOR_DEBOUNCE_MS 50

// --- fan control (proposal section 1: act above 55 %, run until below 45 %) --------------------
#define RH_FAN_ON        55.0f   // fallback rule (no fresh AI command): measured RH >= this -> ON
#define RH_FAN_OFF       45.0f   //                                       measured RH <= this -> OFF
#define AI_CMD_TTL_MS    120000  // AI command older than this -> gateway uses the fallback rule
#define FAN_MAX_ON_MS    (30UL * 60 * 1000)  // safety (M7): fan never runs longer than this...
#define FAN_COOLDOWN_MS  (5UL * 60 * 1000)   // ...then is forced OFF for this long
#define SILICA_MIN_DROP  3.0f    // RH must fall at least this much over a full FAN_MAX_ON run,
                                 // otherwise the silica gel is probably saturated -> event

// --- NETPIE (public, not secret) ----------------------------------------
#define NETPIE_HOST      "mqtt.netpie.io"
#define NETPIE_PORT_TLS  1884
#define NETPIE_TOPIC_SHADOW "@shadow/data/update"   // flat keys -> dashboard + feed (storage)
#define NETPIE_TOPIC_DATA   "@msg/lensguard/data"   // full records -> AI service / logger
#define NETPIE_TOPIC_FAN    "@msg/lensguard/fan"    // AI service -> gateway fan commands
// NETPIE's port-1884 certificate is signed by NETPIE's private "NEXPIE Device Broker" CA, which the
// server does not send, so normal CA verification is impossible. We pin SHA-256 fingerprints of the
// server certificates instead. mqtt.netpie.io is load-balanced: on 2026-11-10 two backends answered,
// each with its OWN certificate and key (both valid until 2036-09-12), so BOTH are listed.
// Run this a few times to see what is served now; add any new value the gateway log reports:
//   openssl s_client -connect mqtt.netpie.io:1884 </dev/null | openssl x509 -noout -fingerprint -sha256
#define NETPIE_CERT_SHA256_LIST { \
  "91:D7:1A:00:54:40:C3:DC:95:2D:7D:5D:BB:B2:B6:94:35:F9:3F:7D:47:4E:83:AA:2C:81:B1:64:75:59:C7:46", \
  "2D:56:F9:F3:AA:B9:63:9E:A9:A4:B6:4B:B1:DC:20:99:32:13:BC:0A:8A:7D:A9:9F:44:FC:FE:86:37:F0:9E:A8" }
// Emergency switch: build with -DNETPIE_TLS_PIN=0 to keep encryption but skip the pin check.
#ifndef NETPIE_TLS_PIN
#define NETPIE_TLS_PIN 1
#endif

// Offline buffer on the gateway (records kept while the Internet is down, then re-sent)
#define OFFLINE_BUFFER   120     // x JSON_MAX bytes of RAM (~38 KB)
#define JSON_MAX         320     // gateway heartbeat record is the longest (~260 bytes)

// --- self-recovery (rubric III Robustness: "recovers by itself after a power or Wi-Fi loss") ----
#define WDT_TIMEOUT_S      60                      // task watchdog: a hung task -> reboot
#define WIFI_REBEGIN_MS    (5UL * 60 * 1000)       // Wi-Fi down this long -> full WiFi.begin() again
#define MQTT_RESTART_MS    (15UL * 60 * 1000)      // Wi-Fi up but MQTT failing this long -> reboot
                                                   // (stuck TLS/network stack; buffered records are lost)

// Sensor nodes the gateway tracks (fan + last RH per box). Index = position in this list.
#define NODE_IDS         { "box_A", "box_B" }
#define NUM_BOXES        2
