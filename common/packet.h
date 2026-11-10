// ESP-NOW packet layout shared by node/ and gateway/. No secrets here.
#pragma once
#include <stdint.h>

enum PacketType : uint8_t { PKT_DATA = 1, PKT_ACK = 2 };

struct __attribute__((packed)) DataPacket {
  uint8_t  type;        // PKT_DATA
  char     node_id[8];  // "box_A" / "box_B"
  uint32_t seq;         // increases by 1 per reading (used for delivery rate)
  float    temp;        // degC   (NAN-safe: sent as 0 with valid=0 if sensor failed)
  float    rh;          // %RH
  uint8_t  door;        // 1 = open, 0 = closed
  uint8_t  valid;       // 1 = reading OK, 0 = sensor read failed
};

struct __attribute__((packed)) AckPacket {
  uint8_t  type;        // PKT_ACK
  uint32_t seq;         // echo of the seq being acknowledged
};
