// Copy this file to secrets.h (same folder) and fill in YOUR values.
// secrets.h is in .gitignore — do not commit it, do not paste it in chat/report.
#pragma once

#define WIFI_SSID        "your-mobile-hotspot-name"
#define WIFI_PASSWORD    "your-hotspot-password"

// NETPIE 2020 credentials of the GATEWAY device (portal.netpie.io -> Project -> Device)
#define NETPIE_CLIENT_ID "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
#define NETPIE_TOKEN     "your-device-token"
#define NETPIE_SECRET    "your-device-secret"

// Password for over-the-air updates (E6): pio run -d gateway -e gateway_ota -t upload
// Use the same value in gateway/platformio.ini upload_flags (--auth=...) via the env var, see README.
#define OTA_PASSWORD     "change-me"
