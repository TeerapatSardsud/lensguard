// Shared by node/ and gateway/. Contains NO secrets.
#pragma once

// --- ESP-NOW / Wi-Fi ---------------------------------------------------
// ESP-NOW peers must be on the SAME Wi-Fi channel as the gateway's Wi-Fi
// connection. The gateway prints its real channel at boot and warns if it
// differs from this value. Phone hotspots can change channel: check the log.
#define ESPNOW_CHANNEL   6

// MAC address of the GATEWAY board (printed in the gateway's serial log at
// boot). Replace after you have the real board. Not a secret.
#define GATEWAY_MAC      {0x00, 0x00, 0x00, 0x00, 0x00, 0x00}

// --- reliability (E4) --------------------------------------------------
#define ACK_TIMEOUT_MS   150
#define MAX_RETRIES      3

// --- timing ------------------------------------------------------------
#define SAMPLE_PERIOD_MS 30000   // 30 s between normal readings

// --- NETPIE (public, not secret) ----------------------------------------
#define NETPIE_HOST      "mqtt.netpie.io"
#define NETPIE_PORT_TLS  1884
#define NETPIE_TOPIC     "@shadow/data/update"

// Offline buffer on the gateway (messages kept while the Internet is down)
#define OFFLINE_BUFFER   100
