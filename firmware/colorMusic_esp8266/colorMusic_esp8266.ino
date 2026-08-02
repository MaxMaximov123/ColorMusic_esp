/*
  Светомузыка на NodeMCU (ESP8266)
  Порт проекта AlexGyver ColorMusic v2.10
  Управление через удалённый сервер (WebSocket)

  Оригинал: https://github.com/AlexGyver/ColorMusic
*/

// ========================= НАСТРОЙКИ =========================

// ----- пины подключения (NodeMCU) -----
#define SOUND_PIN   A0    // аналоговый вход аудио (единственный на ESP8266)
#define LED_PIN     2     // D4 = GPIO2, UART1 TX — аппаратный вывод через NeoPixelBus
#define BTN_PIN     13    // D7 = GPIO13, кнопка (--- GND)

// ----- реле -----
#define RELAY1_PIN  5     // D1 = GPIO5
#define RELAY2_PIN  4     // D2 = GPIO4
#define RELAY3_PIN  14    // D5 = GPIO14
#define RELAY4_PIN  12    // D6 = GPIO12
#define RELAY_ON    LOW   // большинство модулей реле — active LOW
#define RELAY_OFF   HIGH

// ----- настройки ленты -----
#define NUM_LEDS        100   // количество светодиодов
#define PSU_CURRENT_MA  500   // ток блока питания в мА (менять под свой БП)

// ----- WiFi точка доступа (режим настройки) -----
#define AP_SSID     "ColorMusic"
#define AP_PASS     "12345678"

// ----- настройки по умолчанию -----
#define DEFAULT_BRIGHTNESS    200
#define DEFAULT_EMPTY_BRIGHT  30
#define DEFAULT_EMPTY_COLOR   192
#define DEFAULT_SMOOTH        0.5
#define DEFAULT_SMOOTH_FREQ   0.8
#define DEFAULT_RAINBOW_STEP  5.0
#define DEFAULT_MAX_COEF_FREQ 1.5
#define DEFAULT_STROBE_PERIOD 140
#define DEFAULT_STROBE_SMOOTH 200
#define DEFAULT_STROBE_DUTY   20
#define DEFAULT_LIGHT_COLOR   0
#define DEFAULT_LIGHT_SAT     255
#define DEFAULT_COLOR_SPEED   100
#define DEFAULT_RAINBOW_PERIOD 1
#define DEFAULT_RAINBOW_STEP2 0.5
#define DEFAULT_RUNNING_SPEED 11
#define DEFAULT_HUE_START     0
#define DEFAULT_HUE_STEP      5
#define DEFAULT_LOW_PASS      100
#define DEFAULT_SPEKTR_LOW_PASS 40
#define DEFAULT_EXP           1.4
#define DEFAULT_MAX_COEF      1.8
#define DEFAULT_WS_HOST       "0.0.0.0"
#define DEFAULT_WS_PORT       3000

// ----- отрисовка -----
#define MAIN_LOOP     16
#define SMOOTH_STEP   20
#define LIGHT_SMOOTH  2
#define LOW_PASS_ADD  13
#define LOW_PASS_FREQ_ADD 3
#define LOW_COLOR     0
#define MID_COLOR     96
#define HIGH_COLOR    42
#define STROBE_COLOR  42
#define MODE_AMOUNT   9

// ========================= БИБЛИОТЕКИ =========================

#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <DNSServer.h>
#include <EEPROM.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>

#define FASTLED_ESP8266_RAW_PIN_ORDER
#include "FastLED.h"
#include <NeoPixelBus.h>

#include "GyverButton.h"
#include "FFT_C.h"
#include "web_ui.h"

// ========================= СТРУКТУРА НАСТРОЕК =========================

#define SETTINGS_MARKER 0xD2

struct Settings {
  uint8_t marker;
  bool on_state;
  uint8_t this_mode;
  int8_t freq_strobe_mode;
  int8_t light_mode;
  uint8_t brightness;
  uint8_t empty_bright;
  uint8_t empty_color;
  float smooth;
  float smooth_freq;
  float rainbow_step;
  float max_coef_freq;
  float max_coef;
  float exp_val;
  float rainbow_step_2;
  uint16_t strobe_period;
  uint8_t strobe_smooth;
  uint8_t light_color;
  uint8_t light_sat;
  uint8_t color_speed;
  int8_t rainbow_period;
  uint8_t running_speed;
  uint8_t hue_start;
  uint8_t hue_step;
  uint16_t low_pass;
  uint16_t spektr_low_pass;
  char wifi_ssid[33];
  char wifi_pass[65];
  bool relay[4];
  char ws_host[65];
  uint16_t ws_port;
};

Settings cfg;

// ========================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =========================

CRGB leds[NUM_LEDS];
NeoPixelBus<NeoGrbFeature, NeoEsp8266AsyncUart1Ws2812xMethod> strip(NUM_LEDS);

ESP8266WebServer httpServer(80);
DNSServer dnsServer;
WebSocketsClient wsClient;
GButton butt1(BTN_PIN);

int fft_input[FFT_SIZE];
int fft_output[FFT_SIZE];

DEFINE_GRADIENT_PALETTE(soundlevel_gp) {
  0,    0,   255,  0,
  100,  255, 255,  0,
  150,  255, 100,  0,
  200,  255,  50,  0,
  255,  255,   0,  0
};
CRGBPalette32 myPal = soundlevel_gp;

#define STRIPE (NUM_LEDS / 5)
float freq_to_stripe = NUM_LEDS / 40.0;

int Rlenght, Llenght;
float RsoundLevel, RsoundLevel_f;
float LsoundLevel, LsoundLevel_f;

float averageLevel = 50;
int maxLevel = 100;
int MAX_CH = NUM_LEDS / 2;
int hue;
unsigned long main_timer, hue_timer, strobe_timer, running_timer, color_timer, rainbow_timer, eeprom_timer, save_timer, state_timer;
float averK = 0.006;
byte count;
float index_coef;
boolean lowFlag;
int RcurrentLevel, LcurrentLevel;
int colorMusic[3];
float colorMusic_f[3], colorMusic_aver[3];
boolean colorMusicFlash[3], strobeUp_flag, strobeDwn_flag;
byte this_mode;
int thisBright[3], strobe_bright = 0;
unsigned int light_time;
boolean ONstate = true;
int8_t freq_strobe_mode, light_mode;
int freq_max;
float freq_max_f, rainbow_steps;
int freq_f[32];
int this_color;
boolean running_flag[3];
bool settings_changed = false;
bool isAPMode = false;
bool wsConnected = false;
String deviceId;

void showStrip() {
  uint8_t bri = calculate_max_brightness_for_power_vmA(leds, NUM_LEDS, FastLED.getBrightness(), 5, PSU_CURRENT_MA);
  for (int i = 0; i < NUM_LEDS; i++) {
    strip.SetPixelColor(i, RgbColor(
      scale8_video(leds[i].r, bri),
      scale8_video(leds[i].g, bri),
      scale8_video(leds[i].b, bri)
    ));
  }
  strip.Show();
}

// ========================= SETUP =========================

void setup() {
  delay(3000);

  Serial.begin(115200);
  Serial.println("\n=== ColorMusic ESP8266 ===");

  deviceId = "colormusic-" + String(ESP.getChipId(), HEX);
  Serial.println("Device ID: " + deviceId);

  const uint8_t rPins[] = {RELAY1_PIN, RELAY2_PIN, RELAY3_PIN, RELAY4_PIN};
  for (int i = 0; i < 4; i++) {
    digitalWrite(rPins[i], RELAY_OFF);
    pinMode(rPins[i], OUTPUT);
  }

  butt1.setTimeout(900);

  EEPROM.begin(512);
  loadSettings();
  applySettings();
  for (int i = 0; i < 4; i++) applyRelay(i);

  WiFi.forceSleepWake();
  delay(200);
  WiFi.mode(WIFI_OFF);
  delay(200);

  if (cfg.wifi_ssid[0] != '\0') {
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(cfg.wifi_ssid, cfg.wifi_pass);
    Serial.printf("Connecting to %s", cfg.wifi_ssid);
    unsigned long startAttempt = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 15000) {
      delay(500);
      Serial.print(".");
    }
    Serial.println();
  }

  if (WiFi.status() == WL_CONNECTED) {
    isAPMode = false;
    WiFi.setSleepMode(WIFI_MODEM_SLEEP);
    Serial.printf("Connected! IP: %s\n", WiFi.localIP().toString().c_str());

    if (cfg.ws_host[0] != '\0' && strcmp(cfg.ws_host, "0.0.0.0") != 0) {
      wsClient.begin(cfg.ws_host, cfg.ws_port, "/ws/device");
      wsClient.onEvent(wsEvent);
      wsClient.setReconnectInterval(5000);
      Serial.printf("WS connecting to %s:%d\n", cfg.ws_host, cfg.ws_port);
    }
  } else {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASS);
    isAPMode = true;
    dnsServer.start(53, "*", WiFi.softAPIP());
    Serial.printf("AP mode: %s / %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());
  }

  setupAPServer();

  strip.Begin();
  strip.Show();
  FastLED.addLeds<WS2812B, 16, GRB>(leds, NUM_LEDS);
  FastLED.setMaxPowerInVoltsAndMilliamps(5, PSU_CURRENT_MA);
  FastLED.setBrightness(cfg.brightness);

  Serial.println("Ready!");
}

// ========================= LOOP =========================

void loop() {
  if (isAPMode) {
    dnsServer.processNextRequest();
  } else {
    wsClient.loop();
    stateTick();
  }
  httpServer.handleClient();
  buttonTick();
  mainLoop();
  eepromTick();
  yield();
}

// ========================= WEBSOCKET =========================

void wsEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      wsConnected = true;
      Serial.println("WS connected");
      sendHello();
      break;
    case WStype_DISCONNECTED:
      wsConnected = false;
      Serial.println("WS disconnected");
      break;
    case WStype_TEXT:
      handleWsMessage(payload, length);
      break;
    case WStype_PING:
    case WStype_PONG:
      break;
  }
}

void handleWsMessage(uint8_t * payload, size_t length) {
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, payload, length)) return;

  const char* type = doc["type"];
  if (!type) return;

  if (strcmp(type, "set") == 0) {
    JsonObject params = doc["params"];
    if (params.isNull()) return;
    bool changed = false;
    for (JsonPair p : params) {
      changed |= applyParam(p.key().c_str(), p.value());
    }
    if (changed) markChanged();
    sendState();
  }

  if (strcmp(type, "get") == 0) {
    sendState();
  }

  if (strcmp(type, "calibrate") == 0) {
    fullLowPass();
    String msg = "{\"type\":\"calibrate\",\"result\":{\"lp\":" + String(cfg.low_pass) + ",\"slp\":" + String(cfg.spektr_low_pass) + "}}";
    wsClient.sendTXT(msg);
  }
}

bool applyParam(const char* key, JsonVariant val) {
  if (strcmp(key, "on") == 0) { ONstate = val.as<int>(); cfg.on_state = ONstate; if (!ONstate) { fill_solid(leds, NUM_LEDS, CRGB::Black); showStrip(); } return true; }
  if (strcmp(key, "mode") == 0) { this_mode = val.as<int>(); cfg.this_mode = this_mode; return true; }
  if (strcmp(key, "br") == 0) { cfg.brightness = val.as<int>(); FastLED.setBrightness(cfg.brightness); return true; }
  if (strcmp(key, "ebr") == 0) { cfg.empty_bright = val.as<int>(); return true; }
  if (strcmp(key, "ecol") == 0) { cfg.empty_color = val.as<int>(); return true; }
  if (strcmp(key, "sm") == 0) { cfg.smooth = val.as<float>(); return true; }
  if (strcmp(key, "smf") == 0) { cfg.smooth_freq = val.as<float>(); return true; }
  if (strcmp(key, "rs") == 0) { cfg.rainbow_step = val.as<float>(); return true; }
  if (strcmp(key, "mcf") == 0) { cfg.max_coef_freq = val.as<float>(); return true; }
  if (strcmp(key, "mc") == 0) { cfg.max_coef = val.as<float>(); return true; }
  if (strcmp(key, "exp") == 0) { cfg.exp_val = val.as<float>(); return true; }
  if (strcmp(key, "rs2") == 0) { cfg.rainbow_step_2 = val.as<float>(); return true; }
  if (strcmp(key, "sp") == 0) { cfg.strobe_period = val.as<int>(); light_time = cfg.strobe_period * DEFAULT_STROBE_DUTY / 100; return true; }
  if (strcmp(key, "ss") == 0) { cfg.strobe_smooth = val.as<int>(); return true; }
  if (strcmp(key, "lc") == 0) { cfg.light_color = val.as<int>(); return true; }
  if (strcmp(key, "ls") == 0) { cfg.light_sat = val.as<int>(); return true; }
  if (strcmp(key, "cs") == 0) { cfg.color_speed = val.as<int>(); return true; }
  if (strcmp(key, "rp") == 0) { cfg.rainbow_period = val.as<int>(); return true; }
  if (strcmp(key, "rns") == 0) { cfg.running_speed = val.as<int>(); return true; }
  if (strcmp(key, "hs") == 0) { cfg.hue_start = val.as<int>(); return true; }
  if (strcmp(key, "hst") == 0) { cfg.hue_step = val.as<int>(); return true; }
  if (strcmp(key, "fsm") == 0) { freq_strobe_mode = val.as<int>(); cfg.freq_strobe_mode = freq_strobe_mode; return true; }
  if (strcmp(key, "lm") == 0) { light_mode = val.as<int>(); cfg.light_mode = light_mode; return true; }
  if (strcmp(key, "lp") == 0) { cfg.low_pass = val.as<int>(); return true; }
  if (strcmp(key, "slp") == 0) { cfg.spektr_low_pass = val.as<int>(); return true; }

  for (int i = 0; i < 4; i++) {
    char rkey[3];
    snprintf(rkey, sizeof(rkey), "r%d", i + 1);
    if (strcmp(key, rkey) == 0) { cfg.relay[i] = val.as<int>(); applyRelay(i); return true; }
  }
  return false;
}

String buildStateJson() {
  String json = "{";
  json += "\"on\":" + String(ONstate ? 1 : 0);
  json += ",\"mode\":" + String(this_mode);
  json += ",\"br\":" + String(cfg.brightness);
  json += ",\"ebr\":" + String(cfg.empty_bright);
  json += ",\"ecol\":" + String(cfg.empty_color);
  json += ",\"sm\":" + String(cfg.smooth, 2);
  json += ",\"smf\":" + String(cfg.smooth_freq, 2);
  json += ",\"rs\":" + String(cfg.rainbow_step, 1);
  json += ",\"mcf\":" + String(cfg.max_coef_freq, 1);
  json += ",\"mc\":" + String(cfg.max_coef, 1);
  json += ",\"exp\":" + String(cfg.exp_val, 1);
  json += ",\"rs2\":" + String(cfg.rainbow_step_2, 1);
  json += ",\"sp\":" + String(cfg.strobe_period);
  json += ",\"ss\":" + String(cfg.strobe_smooth);
  json += ",\"lc\":" + String(cfg.light_color);
  json += ",\"ls\":" + String(cfg.light_sat);
  json += ",\"cs\":" + String(cfg.color_speed);
  json += ",\"rp\":" + String(cfg.rainbow_period);
  json += ",\"rns\":" + String(cfg.running_speed);
  json += ",\"hs\":" + String(cfg.hue_start);
  json += ",\"hst\":" + String(cfg.hue_step);
  json += ",\"fsm\":" + String(freq_strobe_mode);
  json += ",\"lm\":" + String(light_mode);
  json += ",\"lp\":" + String(cfg.low_pass);
  json += ",\"slp\":" + String(cfg.spektr_low_pass);
  for (int i = 0; i < 4; i++) {
    json += ",\"r" + String(i + 1) + "\":" + String(cfg.relay[i] ? 1 : 0);
  }
  json += "}";
  return json;
}

void sendHello() {
  String msg = "{\"type\":\"hello\",\"deviceId\":\"" + deviceId + "\",\"name\":\"ColorMusic\",\"state\":" + buildStateJson() + "}";
  wsClient.sendTXT(msg);
}

void sendState() {
  if (!wsConnected) return;
  String msg = "{\"type\":\"state\",\"state\":" + buildStateJson() + "}";
  wsClient.sendTXT(msg);
}

void stateTick() {
  if (!wsConnected) return;
  if (millis() - state_timer > 5000) {
    state_timer = millis();
    sendState();
  }
}

// ========================= НАСТРОЙКИ: ЗАГРУЗКА/СОХРАНЕНИЕ =========================

void setDefaults() {
  cfg.marker = SETTINGS_MARKER;
  cfg.on_state = true;
  cfg.this_mode = 0;
  cfg.freq_strobe_mode = 0;
  cfg.light_mode = 0;
  cfg.brightness = DEFAULT_BRIGHTNESS;
  cfg.empty_bright = DEFAULT_EMPTY_BRIGHT;
  cfg.empty_color = DEFAULT_EMPTY_COLOR;
  cfg.smooth = DEFAULT_SMOOTH;
  cfg.smooth_freq = DEFAULT_SMOOTH_FREQ;
  cfg.rainbow_step = DEFAULT_RAINBOW_STEP;
  cfg.max_coef_freq = DEFAULT_MAX_COEF_FREQ;
  cfg.max_coef = DEFAULT_MAX_COEF;
  cfg.exp_val = DEFAULT_EXP;
  cfg.rainbow_step_2 = DEFAULT_RAINBOW_STEP2;
  cfg.strobe_period = DEFAULT_STROBE_PERIOD;
  cfg.strobe_smooth = DEFAULT_STROBE_SMOOTH;
  cfg.light_color = DEFAULT_LIGHT_COLOR;
  cfg.light_sat = DEFAULT_LIGHT_SAT;
  cfg.color_speed = DEFAULT_COLOR_SPEED;
  cfg.rainbow_period = DEFAULT_RAINBOW_PERIOD;
  cfg.running_speed = DEFAULT_RUNNING_SPEED;
  cfg.hue_start = DEFAULT_HUE_START;
  cfg.hue_step = DEFAULT_HUE_STEP;
  cfg.low_pass = DEFAULT_LOW_PASS;
  cfg.spektr_low_pass = DEFAULT_SPEKTR_LOW_PASS;
  strncpy(cfg.wifi_ssid, "Blanik", sizeof(cfg.wifi_ssid));
  strncpy(cfg.wifi_pass, "61746723D", sizeof(cfg.wifi_pass));
  for (int i = 0; i < 4; i++) cfg.relay[i] = false;
  strncpy(cfg.ws_host, DEFAULT_WS_HOST, sizeof(cfg.ws_host));
  cfg.ws_port = DEFAULT_WS_PORT;
}

void applyRelay(int i) {
  const uint8_t rPins[] = {RELAY1_PIN, RELAY2_PIN, RELAY3_PIN, RELAY4_PIN};
  digitalWrite(rPins[i], cfg.relay[i] ? RELAY_ON : RELAY_OFF);
}

void loadSettings() {
  EEPROM.get(0, cfg);
  if (cfg.marker != SETTINGS_MARKER) {
    Serial.println("First boot, loading defaults");
    setDefaults();
    saveSettings();
  } else {
    Serial.println("Settings loaded from EEPROM");
  }
}

void saveSettings() {
  cfg.marker = SETTINGS_MARKER;
  EEPROM.put(0, cfg);
  EEPROM.commit();
  settings_changed = false;
  Serial.println("Settings saved");
}

void applySettings() {
  ONstate = cfg.on_state;
  this_mode = cfg.this_mode;
  freq_strobe_mode = cfg.freq_strobe_mode;
  light_mode = cfg.light_mode;
  FastLED.setBrightness(cfg.brightness);
  light_time = cfg.strobe_period * DEFAULT_STROBE_DUTY / 100;
  index_coef = (float)255 / MAX_CH;
}

// ========================= EEPROM TICK =========================

void eepromTick() {
  if (settings_changed) {
    if (millis() - save_timer > 10000) {
      saveSettings();
    }
  }
}

void markChanged() {
  settings_changed = true;
  save_timer = millis();
}

// ========================= КНОПКА =========================

void buttonTick() {
  butt1.tick();
  if (butt1.isSingle()) {
    if (++cfg.this_mode >= MODE_AMOUNT) cfg.this_mode = 0;
    this_mode = cfg.this_mode;
    markChanged();
    sendState();
  }
  if (butt1.isDouble()) {
    cfg.wifi_ssid[0] = '\0';
    cfg.wifi_pass[0] = '\0';
    cfg.ws_host[0] = '\0';
    saveSettings();
    ESP.restart();
  }
  if (butt1.isHolded()) {
    fullLowPass();
    String msg = "{\"type\":\"calibrate\",\"result\":{\"lp\":" + String(cfg.low_pass) + ",\"slp\":" + String(cfg.spektr_low_pass) + "}}";
    if (wsConnected) wsClient.sendTXT(msg);
  }
}

void fullLowPass() {
  FastLED.setBrightness(0);
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  showStrip();
  delay(500);
  autoLowPass();
  delay(500);
  FastLED.setBrightness(cfg.brightness);
}

// ========================= АНАЛИЗ АУДИО =========================

void analyzeAudio() {
  for (int i = 0; i < FFT_SIZE; i++) {
    fft_input[i] = analogRead(SOUND_PIN);
  }
  FFT(fft_input, fft_output);
}

void autoLowPass() {
  delay(10);
  int thisMax = 0;
  int thisLevel;
  for (int i = 0; i < 200; i++) {
    thisLevel = analogRead(SOUND_PIN);
    if (thisLevel > thisMax) thisMax = thisLevel;
    delay(4);
  }
  cfg.low_pass = thisMax + LOW_PASS_ADD;

  thisMax = 0;
  for (int i = 0; i < 100; i++) {
    analyzeAudio();
    for (int j = 2; j < 32; j++) {
      thisLevel = fft_output[j];
      if (thisLevel > thisMax) thisMax = thisLevel;
    }
    delay(4);
  }
  cfg.spektr_low_pass = thisMax + LOW_PASS_FREQ_ADD;

  markChanged();
  Serial.printf("Calibrated: VU=%d, Spektr=%d\n", cfg.low_pass, cfg.spektr_low_pass);
}

// ========================= ГЛАВНЫЙ ЦИКЛ =========================

void mainLoop() {
  if (!ONstate) return;

  if (millis() - main_timer < MAIN_LOOP) return;
  main_timer = millis();

  RsoundLevel = 0;
  LsoundLevel = 0;

  if (this_mode == 0 || this_mode == 1) {
    for (byte i = 0; i < 100; i++) {
      RcurrentLevel = analogRead(SOUND_PIN);
      if (RsoundLevel < RcurrentLevel) RsoundLevel = RcurrentLevel;
    }

    RsoundLevel = map(RsoundLevel, cfg.low_pass, 1023, 0, 500);
    RsoundLevel = constrain(RsoundLevel, 0, 500);
    RsoundLevel = pow(RsoundLevel, cfg.exp_val);

    RsoundLevel_f = RsoundLevel * cfg.smooth + RsoundLevel_f * (1 - cfg.smooth);
    LsoundLevel_f = RsoundLevel_f;

    if (cfg.empty_bright > 5) {
      for (int i = 0; i < NUM_LEDS; i++)
        leds[i] = CHSV(cfg.empty_color, 255, cfg.empty_bright);
    }

    if (RsoundLevel_f > 15 && LsoundLevel_f > 15) {
      averageLevel = (float)(RsoundLevel_f + LsoundLevel_f) / 2 * averK + averageLevel * (1 - averK);
      maxLevel = (float)averageLevel * cfg.max_coef;
      Rlenght = map(RsoundLevel_f, 0, maxLevel, 0, MAX_CH);
      Llenght = Rlenght;
      Rlenght = constrain(Rlenght, 0, MAX_CH);
      Llenght = constrain(Llenght, 0, MAX_CH);
      animation();
    }
  }

  if (this_mode == 2 || this_mode == 3 || this_mode == 4 || this_mode == 7 || this_mode == 8) {
    analyzeAudio();
    colorMusic[0] = 0;
    colorMusic[1] = 0;
    colorMusic[2] = 0;

    for (int i = 0; i < 32; i++) {
      if (fft_output[i] < cfg.spektr_low_pass) fft_output[i] = 0;
    }

    for (byte i = 2; i < 6; i++) {
      if (fft_output[i] > colorMusic[0]) colorMusic[0] = fft_output[i];
    }
    for (byte i = 6; i < 11; i++) {
      if (fft_output[i] > colorMusic[1]) colorMusic[1] = fft_output[i];
    }
    for (byte i = 11; i < 32; i++) {
      if (fft_output[i] > colorMusic[2]) colorMusic[2] = fft_output[i];
    }

    freq_max = 0;
    for (byte i = 0; i < 30; i++) {
      if (fft_output[i + 2] > freq_max) freq_max = fft_output[i + 2];
      if (freq_max < 5) freq_max = 5;
      if (freq_f[i] < fft_output[i + 2]) freq_f[i] = fft_output[i + 2];
      if (freq_f[i] > 0) freq_f[i] -= LIGHT_SMOOTH;
      else freq_f[i] = 0;
    }
    freq_max_f = freq_max * averK + freq_max_f * (1 - averK);

    for (byte i = 0; i < 3; i++) {
      colorMusic_aver[i] = colorMusic[i] * averK + colorMusic_aver[i] * (1 - averK);
      colorMusic_f[i] = colorMusic[i] * cfg.smooth_freq + colorMusic_f[i] * (1 - cfg.smooth_freq);
      if (colorMusic_f[i] > ((float)colorMusic_aver[i] * cfg.max_coef_freq)) {
        thisBright[i] = 255;
        colorMusicFlash[i] = true;
        running_flag[i] = true;
      } else {
        colorMusicFlash[i] = false;
      }
      if (thisBright[i] >= 0) thisBright[i] -= SMOOTH_STEP;
      if (thisBright[i] < cfg.empty_bright) {
        thisBright[i] = cfg.empty_bright;
        running_flag[i] = false;
      }
    }
    animation();
  }

  if (this_mode == 5) {
    if ((long)millis() - strobe_timer > cfg.strobe_period) {
      strobe_timer = millis();
      strobeUp_flag = true;
      strobeDwn_flag = false;
    }
    if ((long)millis() - strobe_timer > light_time) {
      strobeDwn_flag = true;
    }
    if (strobeUp_flag) {
      if (strobe_bright < 255) strobe_bright += cfg.strobe_smooth;
      if (strobe_bright > 255) {
        strobe_bright = 255;
        strobeUp_flag = false;
      }
    }
    if (strobeDwn_flag) {
      if (strobe_bright > 0) strobe_bright -= cfg.strobe_smooth;
      if (strobe_bright < 0) {
        strobeDwn_flag = false;
        strobe_bright = 0;
      }
    }
    animation();
  }

  if (this_mode == 6) animation();

  showStrip();
  if (this_mode != 7) fill_solid(leds, NUM_LEDS, CRGB::Black);
}

// ========================= АНИМАЦИЯ =========================

void animation() {
  switch (this_mode) {

    case 0:
      count = 0;
      for (int i = (MAX_CH - 1); i > ((MAX_CH - 1) - Rlenght); i--) {
        leds[i] = ColorFromPalette(myPal, (count * index_coef));
        count++;
      }
      count = 0;
      for (int i = MAX_CH; i < (MAX_CH + Llenght); i++) {
        leds[i] = ColorFromPalette(myPal, (count * index_coef));
        count++;
      }
      if (cfg.empty_bright > 0) {
        CHSV dark = CHSV(cfg.empty_color, 255, cfg.empty_bright);
        for (int i = ((MAX_CH - 1) - Rlenght); i > 0; i--) leds[i] = dark;
        for (int i = MAX_CH + Llenght; i < NUM_LEDS; i++) leds[i] = dark;
      }
      break;

    case 1:
      if (millis() - rainbow_timer > 30) {
        rainbow_timer = millis();
        hue = floor((float)hue + cfg.rainbow_step);
      }
      count = 0;
      for (int i = (MAX_CH - 1); i > ((MAX_CH - 1) - Rlenght); i--) {
        leds[i] = ColorFromPalette(RainbowColors_p, (count * index_coef) / 2 - hue);
        count++;
      }
      count = 0;
      for (int i = MAX_CH; i < (MAX_CH + Llenght); i++) {
        leds[i] = ColorFromPalette(RainbowColors_p, (count * index_coef) / 2 - hue);
        count++;
      }
      if (cfg.empty_bright > 0) {
        CHSV dark = CHSV(cfg.empty_color, 255, cfg.empty_bright);
        for (int i = ((MAX_CH - 1) - Rlenght); i > 0; i--) leds[i] = dark;
        for (int i = MAX_CH + Llenght; i < NUM_LEDS; i++) leds[i] = dark;
      }
      break;

    case 2:
      for (int i = 0; i < NUM_LEDS; i++) {
        if (i < STRIPE)          leds[i] = CHSV(HIGH_COLOR, 255, thisBright[2]);
        else if (i < STRIPE * 2) leds[i] = CHSV(MID_COLOR, 255, thisBright[1]);
        else if (i < STRIPE * 3) leds[i] = CHSV(LOW_COLOR, 255, thisBright[0]);
        else if (i < STRIPE * 4) leds[i] = CHSV(MID_COLOR, 255, thisBright[1]);
        else if (i < STRIPE * 5) leds[i] = CHSV(HIGH_COLOR, 255, thisBright[2]);
      }
      break;

    case 3:
      for (int i = 0; i < NUM_LEDS; i++) {
        if (i < NUM_LEDS / 3)          leds[i] = CHSV(HIGH_COLOR, 255, thisBright[2]);
        else if (i < NUM_LEDS * 2 / 3) leds[i] = CHSV(MID_COLOR, 255, thisBright[1]);
        else                            leds[i] = CHSV(LOW_COLOR, 255, thisBright[0]);
      }
      break;

    case 4:
      switch (freq_strobe_mode) {
        case 0:
          if (colorMusicFlash[2]) HIGHS();
          else if (colorMusicFlash[1]) MIDS();
          else if (colorMusicFlash[0]) LOWS();
          else SILENCE();
          break;
        case 1: colorMusicFlash[2] ? HIGHS() : SILENCE(); break;
        case 2: colorMusicFlash[1] ? MIDS()  : SILENCE(); break;
        case 3: colorMusicFlash[0] ? LOWS()  : SILENCE(); break;
      }
      break;

    case 5:
      if (strobe_bright > 0)
        for (int i = 0; i < NUM_LEDS; i++) leds[i] = CHSV(STROBE_COLOR, 0, strobe_bright);
      else
        for (int i = 0; i < NUM_LEDS; i++) leds[i] = CHSV(cfg.empty_color, 255, cfg.empty_bright);
      break;

    case 6:
      switch (light_mode) {
        case 0:
          for (int i = 0; i < NUM_LEDS; i++) leds[i] = CHSV(cfg.light_color, cfg.light_sat, 255);
          break;
        case 1:
          if (millis() - color_timer > cfg.color_speed) {
            color_timer = millis();
            if (++this_color > 255) this_color = 0;
          }
          for (int i = 0; i < NUM_LEDS; i++) leds[i] = CHSV(this_color, cfg.light_sat, 255);
          break;
        case 2:
          if (millis() - rainbow_timer > 30) {
            rainbow_timer = millis();
            this_color += cfg.rainbow_period;
            if (this_color > 255) this_color = 0;
            if (this_color < 0) this_color = 255;
          }
          rainbow_steps = this_color;
          for (int i = 0; i < NUM_LEDS; i++) {
            leds[i] = CHSV((int)floor(rainbow_steps), 255, 255);
            rainbow_steps += cfg.rainbow_step_2;
            if (rainbow_steps > 255) rainbow_steps = 0;
            if (rainbow_steps < 0) rainbow_steps = 255;
          }
          break;
      }
      break;

    case 7:
      switch (freq_strobe_mode) {
        case 0:
          if (running_flag[2])      leds[NUM_LEDS / 2] = CHSV(HIGH_COLOR, 255, thisBright[2]);
          else if (running_flag[1]) leds[NUM_LEDS / 2] = CHSV(MID_COLOR, 255, thisBright[1]);
          else if (running_flag[0]) leds[NUM_LEDS / 2] = CHSV(LOW_COLOR, 255, thisBright[0]);
          else                      leds[NUM_LEDS / 2] = CHSV(cfg.empty_color, 255, cfg.empty_bright);
          break;
        case 1:
          leds[NUM_LEDS / 2] = running_flag[2] ? CHSV(HIGH_COLOR, 255, thisBright[2]) : CHSV(cfg.empty_color, 255, cfg.empty_bright);
          break;
        case 2:
          leds[NUM_LEDS / 2] = running_flag[1] ? CHSV(MID_COLOR, 255, thisBright[1]) : CHSV(cfg.empty_color, 255, cfg.empty_bright);
          break;
        case 3:
          leds[NUM_LEDS / 2] = running_flag[0] ? CHSV(LOW_COLOR, 255, thisBright[0]) : CHSV(cfg.empty_color, 255, cfg.empty_bright);
          break;
      }
      leds[(NUM_LEDS / 2) - 1] = leds[NUM_LEDS / 2];
      if (millis() - running_timer > cfg.running_speed) {
        running_timer = millis();
        for (int i = 0; i < NUM_LEDS / 2 - 1; i++) {
          leds[i] = leds[i + 1];
          leds[NUM_LEDS - i - 1] = leds[i];
        }
      }
      break;

    case 8: {
      byte HUEindex = cfg.hue_start;
      for (int i = 0; i < NUM_LEDS / 2; i++) {
        byte this_bright = map(freq_f[(int)floor((NUM_LEDS / 2 - i) / freq_to_stripe)], 0, freq_max_f, 0, 255);
        this_bright = constrain(this_bright, 0, 255);
        leds[i] = CHSV(HUEindex, 255, this_bright);
        leds[NUM_LEDS - i - 1] = leds[i];
        HUEindex += cfg.hue_step;
        if (HUEindex > 255) HUEindex = 0;
      }
      break;
    }
  }
}

void HIGHS()   { for (int i = 0; i < NUM_LEDS; i++) leds[i] = CHSV(HIGH_COLOR, 255, thisBright[2]); }
void MIDS()    { for (int i = 0; i < NUM_LEDS; i++) leds[i] = CHSV(MID_COLOR, 255, thisBright[1]); }
void LOWS()    { for (int i = 0; i < NUM_LEDS; i++) leds[i] = CHSV(LOW_COLOR, 255, thisBright[0]); }
void SILENCE() { for (int i = 0; i < NUM_LEDS; i++) leds[i] = CHSV(cfg.empty_color, 255, cfg.empty_bright); }

// ========================= AP-MODE ВЕБ-СЕРВЕР (только настройка WiFi/сервера) =========================

void setupAPServer() {
  httpServer.on("/", HTTP_GET, []() { httpServer.send_P(200, "text/html", WIFI_PAGE); });
  httpServer.on("/scan", HTTP_GET, handleWifiScan);
  httpServer.on("/wifisave", HTTP_GET, handleWifiSave);
  httpServer.on("/wifireset", HTTP_GET, handleWifiReset);
  httpServer.onNotFound([]() { httpServer.send_P(200, "text/html", WIFI_PAGE); });
  httpServer.begin();
  Serial.println("Config server started");
}

void handleWifiScan() {
  int n = WiFi.scanNetworks();
  String json = "[";
  for (int i = 0; i < n; i++) {
    if (i > 0) json += ",";
    String ssid = WiFi.SSID(i);
    ssid.replace("\\", "\\\\");
    ssid.replace("\"", "\\\"");
    json += "{\"s\":\"" + ssid + "\",\"r\":" + String(WiFi.RSSI(i)) + ",\"e\":" + String(WiFi.encryptionType(i) != ENC_TYPE_NONE ? 1 : 0) + "}";
  }
  json += "]";
  WiFi.scanDelete();
  httpServer.send(200, "application/json", json);
}

void handleWifiSave() {
  if (httpServer.hasArg("ssid")) {
    httpServer.arg("ssid").toCharArray(cfg.wifi_ssid, 33);
    if (httpServer.hasArg("pass"))
      httpServer.arg("pass").toCharArray(cfg.wifi_pass, 65);
    else
      cfg.wifi_pass[0] = '\0';
    if (httpServer.hasArg("host"))
      httpServer.arg("host").toCharArray(cfg.ws_host, 65);
    if (httpServer.hasArg("port"))
      cfg.ws_port = httpServer.arg("port").toInt();
    saveSettings();
    httpServer.send(200, "application/json", "{\"ok\":1}");
    delay(1000);
    ESP.restart();
  } else {
    httpServer.send(400, "application/json", "{\"err\":\"no ssid\"}");
  }
}

void handleWifiReset() {
  cfg.wifi_ssid[0] = '\0';
  cfg.wifi_pass[0] = '\0';
  cfg.ws_host[0] = '\0';
  saveSettings();
  httpServer.send(200, "application/json", "{\"ok\":1}");
  delay(1000);
  ESP.restart();
}
