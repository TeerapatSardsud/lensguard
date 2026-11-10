// ESP-NOW packet layout shared by node/ and gateway/. No secrets here.
#pragma once
#include <stdint.h>

enum PacketType : uint8_t { PKT_DATA = 1, PKT_ACK = 2 };

// reason the node sent this packet
enum PacketReason : uint8_t { REASON_PERIODIC = 0, REASON_DOOR = 1, REASON_BOOT = 2 };

struct __attribute__((packed)) DataPacket {
  uint8_t  type;        // PKT_DATA
  char     node_id[8];  // "box_A" / "box_B" (NUL-terminated)
  uint32_t seq;         // +1 per packet; restarts at 1 after a node reboot (boot_id changes)
  uint16_t boot_id;     // random per boot, so the gateway can tell a reboot from a duplicate
  float    temp;        // degC   (0 with valid=0 if the sensor read failed)
  float    rh;          // %RH
  uint8_t  door;        // 1 = open, 0 = closed
  uint8_t  valid;       // 1 = reading OK, 0 = sensor read failed
  uint8_t  reason;      // PacketReason
  uint8_t  tries;       // send attempt that produced this frame (1 = first try) -> retry metric
};

struct __attribute__((packed)) AckPacket {
  uint8_t  type;        // PKT_ACK
  uint32_t seq;         // echo of the seq being acknowledged
};

// Note: packed fields cannot be bound to references (float&). Read into locals, then assign.
