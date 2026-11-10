# Pin map & wiring (ESP32 DevKit, 30/38-pin) — matches the Wokwi diagram (`docs/wokwi/diagram.json`)

Wokwi project (full system view, LEDs stand in for the fans): https://wokwi.com/projects/480315294985826305
Pins changed on 2026-11-10 to follow that diagram: reed 27→33, DS18B20 18→16, fans 25/26→26/27.

The fans are driven by the gateway, so each box needs a 2-wire cable from the gateway to its fan (see the 3D model in `material/`).
BOM lists 1–2 MOSFETs: buy **2**. With only one, both fans would share one on/off state and the firmware
(which controls each box separately) needs a code change — tell the gateway owner first.

## Node (box_A / box_B) — ESP32 + DHT22 + reed switch
| Signal | ESP32 pin | Notes |
|---|---|---|
| DHT22 DATA | GPIO4 | 10 kΩ pull-up DATA→3V3 (skip if the DHT22 is a 3-pin module with built-in resistor) |
| DHT22 VCC / GND | 3V3 / GND | |
| Reed switch | GPIO33 ↔ GND | `INPUT_PULLUP`; magnet on the lid = closed (LOW), lid open = HIGH |
| Power | USB 5 V (adapter or power bank) | |

## Gateway — ESP32 + DS18B20 + LDR + MQ-135 + 2 fans (one per box)
| Signal | ESP32 pin | Notes |
|---|---|---|
| DS18B20 DATA | GPIO16 (RX2) | 4.7 kΩ pull-up DATA→3V3; measures **outside** temperature. OK on ESP32-WROOM DevKits; GPIO16 is not usable on WROVER (PSRAM) boards |
| LDR divider | GPIO34 (ADC1) | 3V3 – LDR – node – 10 kΩ – GND; node → GPIO34 |
| MQ-135 AO | GPIO35 (ADC1) | MQ-135 runs on 5 V; **AO can reach ~5 V** → divider (e.g. 10 kΩ top / 20 kΩ bottom) before the pin |
| MQ-135 VCC / GND | 5 V (VIN) / GND | heater draws ~150 mA; needs warm-up before readings are meaningful |
| Fan box_A MOSFET gate | GPIO26 | via 100 Ω; 10 kΩ gate→GND pull-down so the fan is OFF at boot |
| Fan box_B MOSFET gate | GPIO27 | same circuit, second MOSFET (`FAN_PINS` in `gateway/src/main.cpp`) |
| Status LED (optional) | GPIO2 | |

Use ADC1 pins (GPIO32–39) for analog: ADC2 does not work while Wi-Fi is on.
GPIO34/35 are input-only (fine for sensors).

## Fan driver (low-side switch) — build this twice (box_A on GPIO26, box_B on GPIO27)
```
Fan supply (+) ───────────────┬───────── Fan (+)
                              │  1N4007 flyback diode across the fan (stripe toward +)
Fan (−) ──────── Drain  [N-MOSFET, logic-level, e.g. IRLZ44N / IRLZ34N]
GPIO26/27 ─ 100Ω ─ Gate ─┬─ 10kΩ ─ GND
                Source ──────── GND  (common with ESP32 GND!)
```
- Use the voltage of the fan you actually bought (check the label). If the fan is 5 V, USB 5 V is fine; if 12 V, use a separate 12 V adapter and **join the grounds**.
- Logic-level MOSFET is required (3.3 V gate drive). A plain IRF540 may not switch fully.

## Box layout (sealed plastic box)
- Air must circulate **inside** the box: fan + silica gel container in one corner, sensor on the opposite side (not directly in the fan's airflow).
- Leave **no** opening to the outside: pass wires through a small hole sealed with hot glue/silicone.
- Reed switch on the lid edge, magnet on the box body; confirm it reads CLOSED when the lid is shut.
- Keep silica gel in a perforated container so dust does not enter the fan.

## Before powering anything
1. Check polarity with a multimeter. 2. Fan off by default (pull-down). 3. Never power the fan from an ESP32 pin.
