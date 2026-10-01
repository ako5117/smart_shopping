// Shelf node: reads one load cell per zone, detects settled weight changes and publishes them over MQTT.
// Logic: docs/sensor-logic.md, Step 1. Message format consumed by services/shelf.
//
// Libraries (Arduino Library Manager): "HX711" by Rob Tillaart, "PubSubClient" by Nick O'Leary.
// Board: ESP32 Dev Module.
//
// Serial commands (115200 baud):
//   tare <zone>            zero the zone with nothing on it
//   cal <zone> <grams>     calibrate with a known weight on the zone
//   noise <zone>           measure idle noise over ~10 s (standard deviation, g)
//   show                   print current readings and settings

#include <WiFi.h>
#include <PubSubClient.h>
#include <Preferences.h>
#include <HX711.h>
#include <time.h>
#include "config.h"

static const size_t NUM_ZONES = sizeof(ZONES) / sizeof(ZONES[0]);
static const int MEDIAN_WINDOW = 5;
static const int QUEUE_SIZE = 50;

enum class ZoneState { Stable, Changing };

struct Zone {
  HX711 scale;
  float window[MEDIAN_WINDOW];
  int filled = 0;
  int next = 0;
  float stable_g = 0;
  float candidate_g = 0;
  unsigned long candidate_since = 0;
  unsigned long stable_since = 0;
  ZoneState state = ZoneState::Stable;
};

static Zone zones[NUM_ZONES];
static WiFiClient net;
static PubSubClient mqtt(net);
static Preferences prefs;

static char pending_topic[QUEUE_SIZE][96];
static char pending_payload[QUEUE_SIZE][320];
static int q_head = 0, q_count = 0;

static uint32_t boot_id = 0;
static uint32_t seq = 0;
static unsigned long last_sample = 0;

// ------------------------------------------------------------------ helpers

static float median(const float* v, int n) {
  float s[MEDIAN_WINDOW];
  memcpy(s, v, n * sizeof(float));
  for (int i = 1; i < n; i++) {
    float x = s[i]; int j = i - 1;
    while (j >= 0 && s[j] > x) { s[j + 1] = s[j]; j--; }
    s[j + 1] = x;
  }
  return s[n / 2];
}

static int zoneIndex(const String& id) {
  for (size_t i = 0; i < NUM_ZONES; i++) if (id == ZONES[i].zone_id) return i;
  return -1;
}

static void isoTimestamp(char* out, size_t len) {
  time_t now = time(nullptr);
  if (now < 1700000000) { out[0] = '\0'; return; }  // clock not set yet; the service uses its own time
  struct tm t;
  localtime_r(&now, &t);
  strftime(out, len, "%Y-%m-%dT%H:%M:%S+03:00", &t);
}

// ---------------------------------------------------------------- publishing

static void enqueue(const char* topic, const char* payload) {
  if (q_count == QUEUE_SIZE) {  // full: drop the oldest so the newest state is kept
    q_head = (q_head + 1) % QUEUE_SIZE;
    q_count--;
    Serial.println("WARN queue full, oldest event dropped");
  }
  int slot = (q_head + q_count) % QUEUE_SIZE;
  strlcpy(pending_topic[slot], topic, sizeof(pending_topic[slot]));
  strlcpy(pending_payload[slot], payload, sizeof(pending_payload[slot]));
  q_count++;
}

static void flushQueue() {
  while (q_count > 0 && mqtt.connected()) {
    if (!mqtt.publish(pending_topic[q_head], pending_payload[q_head])) return;  // retry next loop
    q_head = (q_head + 1) % QUEUE_SIZE;
    q_count--;
  }
}

static void publishWeightEvent(size_t i, float before_g, float after_g, unsigned long settled_ms) {
  char topic[96], payload[320], ts[32], event_id[48];
  snprintf(topic, sizeof(topic), "store/%s/shelf/%s/zone/%s/weight", STORE_ID, SHELF_ID, ZONES[i].zone_id);
  snprintf(event_id, sizeof(event_id), "%s-b%04lu-%06lu", DEVICE_ID, (unsigned long)boot_id, (unsigned long)++seq);
  isoTimestamp(ts, sizeof(ts));
  snprintf(payload, sizeof(payload),
           "{\"device_id\":\"%s\",\"zone_id\":\"%s\",\"event_id\":\"%s\","
           "\"weight_before_g\":%.1f,\"weight_after_g\":%.1f,\"delta_g\":%.1f,"
           "\"settled_ms\":%lu,\"ts\":\"%s\"}",
           DEVICE_ID, ZONES[i].zone_id, event_id, before_g, after_g, after_g - before_g, settled_ms, ts);
  Serial.printf("EVENT %s\n", payload);
  enqueue(topic, payload);
}

// ------------------------------------------------------------- change detection

static void processZone(size_t i, float reading_g) {
  Zone& z = zones[i];
  const ZoneConfig& c = ZONES[i];
  z.window[z.next] = reading_g;
  z.next = (z.next + 1) % MEDIAN_WINDOW;
  if (z.filled < MEDIAN_WINDOW) { z.filled++; if (z.filled < MEDIAN_WINDOW) return; }
  float w = median(z.window, MEDIAN_WINDOW);
  unsigned long now = millis();

  if (z.stable_since == 0) { z.stable_g = w; z.stable_since = now; return; }  // first full window

  float threshold = max(c.threshold_g, 0.5f * c.min_unit_g);

  if (z.state == ZoneState::Stable) {
    if (fabsf(w - z.stable_g) > threshold) {
      z.state = ZoneState::Changing;
      z.candidate_g = w;
      z.candidate_since = now;
    } else if (now - z.stable_since > ZERO_TRACK_AFTER_MS && fabsf(w - z.stable_g) < c.settle_band_g) {
      z.stable_g += 0.02f * (w - z.stable_g);  // very slow drift correction while idle
    }
    return;
  }

  // Changing: wait for the reading to hold within the settle band.
  if (fabsf(w - z.candidate_g) > c.settle_band_g) {
    z.candidate_g = w;
    z.candidate_since = now;
    return;
  }
  if (now - z.candidate_since < SETTLE_TIME_MS) return;

  unsigned long settled_ms = now - z.candidate_since;
  if (fabsf(z.candidate_g - z.stable_g) > threshold) {
    publishWeightEvent(i, z.stable_g, z.candidate_g, settled_ms);
    z.stable_g = z.candidate_g;
  }
  // Otherwise it came back to where it started (a lean or a hand resting): no event.
  z.state = ZoneState::Stable;
  z.stable_since = now;
}

// ------------------------------------------------------------- serial commands

static void measureNoise(size_t i) {
  const int n = 100;
  float sum = 0, sq = 0;
  for (int k = 0; k < n; k++) {
    float v = zones[i].scale.get_units(1);
    sum += v; sq += v * v;
  }
  float mean = sum / n, sd = sqrtf(max(0.0f, sq / n - mean * mean));
  Serial.printf("NOISE %s mean=%.1f g sd=%.2f g -> suggest threshold_g=%.1f settle_band_g=%.1f\n",
                ZONES[i].zone_id, mean, sd, 3 * sd, 2 * sd);
}

static void handleSerial() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  int sp1 = line.indexOf(' ');
  String cmd = sp1 < 0 ? line : line.substring(0, sp1);
  String rest = sp1 < 0 ? "" : line.substring(sp1 + 1);
  int sp2 = rest.indexOf(' ');
  String zid = sp2 < 0 ? rest : rest.substring(0, sp2);
  int i = zoneIndex(zid);

  if (cmd == "show") {
    for (size_t k = 0; k < NUM_ZONES; k++)
      Serial.printf("%s stable=%.1f g factor=%.2f queue=%d\n", ZONES[k].zone_id, zones[k].stable_g,
                    zones[k].scale.get_scale(), q_count);
    return;
  }
  if (i < 0) { Serial.println("ERR unknown zone. Commands: tare <zone>, cal <zone> <grams>, noise <zone>, show"); return; }

  if (cmd == "tare") {
    zones[i].scale.tare(20);
    prefs.putLong((String("off_") + zid).c_str(), zones[i].scale.get_offset());
    zones[i].filled = 0; zones[i].stable_since = 0;
    Serial.printf("OK %s tared\n", zid.c_str());
  } else if (cmd == "cal" && sp2 > 0) {
    float grams = rest.substring(sp2 + 1).toFloat();
    if (grams <= 0) { Serial.println("ERR give the known weight in grams"); return; }
    zones[i].scale.set_scale(1.0f);
    float raw = zones[i].scale.get_units(20);
    float factor = raw / grams;
    zones[i].scale.set_scale(factor);
    prefs.putFloat((String("cal_") + zid).c_str(), factor);
    zones[i].filled = 0; zones[i].stable_since = 0;
    Serial.printf("OK %s factor=%.3f\n", zid.c_str(), factor);
  } else if (cmd == "noise") {
    measureNoise(i);
  } else {
    Serial.println("ERR unknown command");
  }
}

// ------------------------------------------------------------------ network

static void ensureConnected() {
  static unsigned long last_try = 0;
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - last_try > 5000) { last_try = millis(); WiFi.reconnect(); }
    return;
  }
  if (!mqtt.connected() && millis() - last_try > 3000) {
    last_try = millis();
    bool ok = strlen(MQTT_USER) ? mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD) : mqtt.connect(DEVICE_ID);
    Serial.println(ok ? "MQTT connected" : "MQTT connect failed, retrying");
  }
}

// ------------------------------------------------------------------ setup/loop

void setup() {
  Serial.begin(115200);
  prefs.begin("shelf", false);
  boot_id = prefs.getUInt("boot", 0) + 1;  // makes event ids unique across restarts
  prefs.putUInt("boot", boot_id);

  for (size_t i = 0; i < NUM_ZONES; i++) {
    zones[i].scale.begin(ZONES[i].dout_pin, ZONES[i].sck_pin);
    zones[i].scale.set_scale(prefs.getFloat((String("cal_") + ZONES[i].zone_id).c_str(), 1.0f));
    zones[i].scale.set_offset(prefs.getLong((String("off_") + ZONES[i].zone_id).c_str(), 0));
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(3 * 3600, 0, "pool.ntp.org", "time.google.com");  // East Africa Time, no DST
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  Serial.printf("Shelf node %s boot %lu, %u zones\n", DEVICE_ID, (unsigned long)boot_id, (unsigned)NUM_ZONES);
}

void loop() {
  handleSerial();
  ensureConnected();
  mqtt.loop();

  if (millis() - last_sample >= SAMPLE_INTERVAL_MS) {
    last_sample = millis();
    for (size_t i = 0; i < NUM_ZONES; i++)
      if (zones[i].scale.is_ready()) processZone(i, zones[i].scale.get_units(1));
  }
  flushQueue();
}
