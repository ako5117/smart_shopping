// Host tests for shelf_node change detection: runs the real firmware logic on a PC with
// simulated load cells, clock and MQTT. Run with: sh run_tests.sh
#include "stubs.h"
#include "../config.example.h"
#include "firmware_under_test.inc"  // generated from shelf_node.ino by run_tests.sh

#include <random>

static std::mt19937 rng(42);
static float noise(float sd) { std::normal_distribution<float> n(0, sd); return n(rng); }

static void runFor(unsigned long ms) {
  unsigned long end = fake_now_ms + ms;
  while (fake_now_ms < end) { fake_now_ms += 10; loop(); }
}

static std::vector<std::string> events() {
  std::vector<std::string> v;
  for (auto& p : PubSubClient::sent) v.push_back(p.payload);
  return v;
}

static double field(const std::string& json, const char* key) {
  auto p = json.find(std::string("\"") + key + "\":");
  return p == std::string::npos ? NAN : atof(json.c_str() + p + strlen(key) + 3);
}

static int failures = 0;
static void check(bool ok, const char* name, const std::string& detail = "") {
  printf("%s  %s %s\n", ok ? "PASS" : "FAIL", name, detail.c_str());
  failures += !ok;
}

static void reset(std::function<float(int, unsigned long)> sig) {
  PubSubClient::sent.clear(); PubSubClient::up = true;
  for (size_t i = 0; i < NUM_ZONES; i++) { zones[i] = Zone(); zones[i].scale.id = (int)i; }
  q_head = q_count = 0;
  fake_signal = sig;
  runFor(3000);  // fill the median window and establish a stable weight
  PubSubClient::sent.clear();
}

int main() {
  setup();

  // 1. Idle with sensor noise: no events over 10 minutes.
  reset([](int z, unsigned long) { return 5000.0f + noise(1.5f); });
  runFor(600000);
  check(events().empty(), "idle noise produces no events", std::to_string(events().size()) + " events");

  // 2. Slow drift (temperature): +3 g over 10 minutes, no events.
  reset([](int z, unsigned long t) { return 5000.0f + 3.0f * t / 600000.0f + noise(1.5f); });
  runFor(600000);
  check(events().empty(), "slow drift produces no events");

  // 3. A pick with a wobbling hand: one event, correct delta.
  static unsigned long t0;
  t0 = fake_now_ms + 3000 + 1000;
  reset([](int z, unsigned long t) {
    if (z != 1) return 5000.0f + noise(1.5f);
    if (t < t0) return 5000.0f + noise(1.5f);
    if (t < t0 + 600) return 4300.0f + 300.0f * sinf(t / 40.0f) + noise(1.5f);  // item lifted, shelf bouncing
    return 3994.0f + noise(1.5f);                                                  // one 1006 g item gone
  });
  runFor(5000);
  auto ev = events();
  check(ev.size() == 1 && fabs(field(ev[0], "delta_g") + 1006) < 4, "pick produces one event with the right delta",
        ev.empty() ? "none" : ev[0]);
  check(!ev.empty() && ev[0].find("\"zone_id\":\"A2\"") != std::string::npos, "event is for the right zone");

  // 4. A hand resting on the shelf (tremor) for 3 s, then removed: no event.
  t0 = fake_now_ms + 3000 + 1000;
  reset([](int z, unsigned long t) {
    if (z == 0 && t > t0 && t < t0 + 3000) return 5400.0f + 25.0f * sinf(t / 90.0f) + noise(1.5f);
    return 5000.0f + noise(1.5f);
  });
  runFor(6000);
  check(events().empty(), "resting hand with tremor produces no events", std::to_string(events().size()) + " events");

  // 5. Offline: three picks while the broker is down, all delivered in order after reconnect.
  t0 = fake_now_ms + 3000 + 1000;
  reset([](int z, unsigned long t) {
    if (z != 1) return 5000.0f + noise(1.5f);  // zone A2 holds 1006 g items
    int picks = t < t0 ? 0 : t < t0 + 3000 ? 1 : t < t0 + 6000 ? 2 : 3;
    return 5000.0f - 1006.0f * picks + noise(1.5f);
  });
  PubSubClient::up = false;
  runFor(10000);
  bool none_while_down = PubSubClient::sent.empty();
  PubSubClient::up = true;
  runFor(500);
  ev = events();
  bool ordered = ev.size() == 3;
  for (size_t i = 1; ordered && i < ev.size(); i++)
    ordered = ev[i - 1].substr(ev[i - 1].find("event_id")) < ev[i].substr(ev[i].find("event_id"));
  check(none_while_down && ordered, "offline events queue and send in order on reconnect",
        std::to_string(ev.size()) + " delivered");

  // 6. Two items picked together: one event of twice the weight.
  t0 = fake_now_ms + 3000 + 1000;
  reset([](int z, unsigned long t) {
    if (z != 1) return 5000.0f + noise(1.5f);
    return (t < t0 ? 5000.0f : 5000.0f - 2012.0f) + noise(1.5f);
  });
  runFor(4000);
  ev = events();
  check(ev.size() == 1 && fabs(field(ev[0], "delta_g") + 2012) < 4, "two-item pick is one event of double weight");

  printf("\n%s\n", failures ? "SOME TESTS FAILED" : "All firmware tests passed");
  return failures ? 1 : 0;
}
