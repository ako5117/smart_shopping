// Copy this file to config.h and fill in your values. config.h is git-ignored.
#pragma once

// --- Network ---
#define WIFI_SSID       "your-wifi"
#define WIFI_PASSWORD   "your-wifi-password"
#define MQTT_HOST       "192.168.1.10"   // machine running the MQTT broker and Shelf Service
#define MQTT_PORT       1883
#define MQTT_USER       ""               // leave empty if the broker has no login
#define MQTT_PASSWORD   ""

// --- Identity ---
#define STORE_ID        "001"
#define SHELF_ID        "A"
#define DEVICE_ID       "shelf-A"

// --- Timing (see docs/sensor-logic.md, tunable parameters) ---
#define SAMPLE_INTERVAL_MS   100    // 10 Hz per zone
#define SETTLE_TIME_MS       700    // weight must hold steady this long
#define ZERO_TRACK_AFTER_MS  60000  // idle this long before slow drift correction

// --- Zones: one load cell + HX711 per zone ---
// noise_g: measure with the "noise" serial command, then set threshold_g = 3 x noise and settle_band_g = 2 x noise.
// min_unit_g: lightest product assigned to the zone; changes below half of it are ignored.
struct ZoneConfig {
  const char* zone_id;
  uint8_t dout_pin;
  uint8_t sck_pin;
  float threshold_g;
  float settle_band_g;
  float min_unit_g;
};

static const ZoneConfig ZONES[] = {
  // zone   DT  SCK  threshold  settle  min unit
  { "A1",   16,  4,     6.0,      4.0,   2012.0 },
  { "A2",   17,  5,     6.0,      4.0,   1006.0 },
};
