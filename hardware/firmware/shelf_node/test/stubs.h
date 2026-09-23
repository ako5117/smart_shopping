// Minimal stand-ins for the Arduino/ESP32 APIs the firmware uses, so its logic can run on a PC.
#pragma once
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <functional>
#include <string>
#include <vector>
#include <algorithm>
using std::max;

inline unsigned long fake_now_ms = 0;
inline unsigned long millis() { return fake_now_ms; }

inline size_t strlcpy(char* d, const char* s, size_t n) {
  size_t l = strlen(s); if (n) { size_t c = l < n - 1 ? l : n - 1; memcpy(d, s, c); d[c] = 0; } return l;
}

class String {
 public:
  std::string s;
  String() {}
  String(const char* c) : s(c) {}
  String(const std::string& x) : s(x) {}
  int indexOf(char c) const { auto p = s.find(c); return p == std::string::npos ? -1 : (int)p; }
  String substring(int a) const { return String(s.substr(a)); }
  String substring(int a, int b) const { return String(s.substr(a, b - a)); }
  void trim() { s.erase(0, s.find_first_not_of(" \r\n\t")); s.erase(s.find_last_not_of(" \r\n\t") + 1); }
  float toFloat() const { return std::stof(s); }
  const char* c_str() const { return s.c_str(); }
  bool operator==(const char* o) const { return s == o; }
  bool operator==(const String& o) const { return s == o.s; }
  String operator+(const String& o) const { return String(s + o.s); }
  String operator+(const char* o) const { return String(s + o); }
};

struct FakeSerial {
  std::vector<std::string> input;
  std::vector<std::string> lines;
  void begin(int) {}
  int available() { return !input.empty(); }
  String readStringUntil(char) { auto l = input.front(); input.erase(input.begin()); return String(l); }
  void println(const char* t) { lines.push_back(t); }
  void printf(const char* f, ...) { char b[512]; va_list a; va_start(a, f); vsnprintf(b, sizeof b, f, a); va_end(a); lines.push_back(b); }
};
inline FakeSerial Serial;

#define WL_CONNECTED 3
#define WIFI_STA 1
struct FakeWiFi {
  int status_ = WL_CONNECTED;
  int status() { return status_; }
  void mode(int) {} void begin(const char*, const char*) {} void reconnect() {}
};
inline FakeWiFi WiFi;
struct WiFiClient {};

struct Published { std::string topic, payload; };
struct PubSubClient {
  static inline bool up = true;
  static inline std::vector<Published> sent;
  PubSubClient(WiFiClient&) {}
  void setServer(const char*, int) {} void setBufferSize(int) {}
  bool connected() { return up; }
  bool connect(const char*) { return up; } bool connect(const char*, const char*, const char*) { return up; }
  void loop() {}
  bool publish(const char* t, const char* p) { if (!up) return false; sent.push_back({t, p}); return true; }
};

struct Preferences {
  void begin(const char*, bool) {}
  uint32_t getUInt(const char*, uint32_t d) { return d; } void putUInt(const char*, uint32_t) {}
  float getFloat(const char*, float d) { return d; } void putFloat(const char*, float) {}
  long getLong(const char*, long d) { return d; } void putLong(const char*, long) {}
};

// Each simulated load cell reads grams from a signal function of time.
inline std::function<float(int zone, unsigned long t)> fake_signal = [](int, unsigned long) { return 0.0f; };
struct HX711 {
  static inline int next_id = 0;
  int id = next_id++;
  float scale = 1, offset = 0;
  void begin(uint8_t, uint8_t) {}
  bool is_ready() { return true; }
  float get_units(int) { return fake_signal(id, fake_now_ms); }
  void set_scale(float f) { scale = f; } float get_scale() { return scale; }
  void set_offset(long o) { offset = o; } long get_offset() { return (long)offset; }
  void tare(int) {}
};

inline void configTime(long, int, const char*, const char*) {}
