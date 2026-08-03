/*
  Термометр на NodeMCU (ESP8266)
  DS18B20 + WebSocket + FastBot (Telegram)
  Часть системы умного дома

  Оригинал: Telegram_bot_temp V1.1
*/

// ========================= НАСТРОЙКИ =========================

#define DS18B20_PIN  2    // D4 = GPIO2, OneWire
#define BTN_PIN      0    // D3 = GPIO0, FLASH button

#define AP_SSID     "TempSensor"
#define AP_PASS     "12345678"

#define DEFAULT_WS_HOST       "0.0.0.0"
#define DEFAULT_WS_PORT       3000
#define DEFAULT_THRESHOLD     5.0

// ----- Telegram -----
#define BOT_TOKEN "5853726725:AAFSNKwhzbUm6l27K4oKqb_uyzFgnO6i8ns"
#define TG_USERS  "1387680086,629395593,5207110017"

// ----- интервалы -----
#define STATE_INTERVAL  60000   // 60 сек — отправка на сервер

// ========================= БИБЛИОТЕКИ =========================

#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <DNSServer.h>
#include <EEPROM.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <FastBot.h>

#include "web_ui.h"

// ========================= СТРУКТУРА НАСТРОЕК =========================

#define SETTINGS_MARKER 0xA2

struct Settings {
  uint8_t marker;
  char wifi_ssid[33];
  char wifi_pass[65];
  char ws_host[65];
  uint16_t ws_port;
  float threshold;
  bool set_limit_mode;
};

Settings cfg;

// ========================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =========================

OneWire oneWire(DS18B20_PIN);
DallasTemperature ds18b20(&oneWire);
FastBot bot(BOT_TOKEN);

ESP8266WebServer httpServer(80);
DNSServer dnsServer;
WebSocketsClient wsClient;

float currentTemp = 0.0;
bool isAPMode = false;
bool wsConnected = false;
bool settings_changed = false;
bool send_msg = true;
String deviceId;
String tgButtons = "\xF0\x9F\x8C\xA1\xD0\xA2\xD0\xB5\xD0\xBC\xD0\xBF\xD0\xB5\xD1\x80\xD0\xB0\xD1\x82\xD1\x83\xD1\x80\xD0\xB0\n\xF0\x9F\x94\x94\xD0\x9F\xD0\xBE\xD1\x80\xD0\xBE\xD0\xB3";

unsigned long state_timer = 0;
unsigned long save_timer = 0;

// кнопка
unsigned long btn_last_press = 0;
int btn_press_count = 0;

void getTemp() {
  ds18b20.requestTemperatures();
  float t = ds18b20.getTempCByIndex(0);
  if (t != DEVICE_DISCONNECTED_C) {
    currentTemp = round(t * 10.0) / 10.0;
  } else {
    Serial.println("DS18B20 read error!");
  }
}

// ========================= TELEGRAM BOT =========================

void onTelegramMsg(FB_msg& msg) {
  String id = msg.chatID;
  Serial.printf("TG [%s] %s: %s\n", msg.chatID.c_str(), msg.username.c_str(), msg.text.c_str());

  String reply = "";

  if (msg.text == "/start") {
    reply = "\xD0\x9F\xD1\x80\xD0\xB8\xD0\xB2\xD0\xB5\xD1\x82! \xD0\xAF \xD0\xB1\xD0\xBE\xD1\x82 \xD1\x82\xD0\xB5\xD1\x80\xD0\xBC\xD0\xBE\xD0\xBC\xD0\xB5\xD1\x82\xD1\x80\xD0\xB0.";

  } else if (msg.text == "/get_temp" || msg.text == "\xF0\x9F\x8C\xA1\xD0\xA2\xD0\xB5\xD0\xBC\xD0\xBF\xD0\xB5\xD1\x80\xD0\xB0\xD1\x82\xD1\x83\xD1\x80\xD0\xB0") {
    getTemp();
    reply = "\xD0\xA2\xD0\xB5\xD0\xBC\xD0\xBF\xD0\xB5\xD1\x80\xD0\xB0\xD1\x82\xD1\x83\xD1\x80\xD0\xB0: " + String(currentTemp, 1) + " \xC2\xB0\x43";
    send_msg = true;

  } else if (msg.text == "/set_a_threshold" || msg.text == "\xF0\x9F\x94\x94\xD0\x9F\xD0\xBE\xD1\x80\xD0\xBE\xD0\xB3") {
    if (!cfg.set_limit_mode) {
      reply = "\xD0\xA1\xD0\xB5\xD0\xB9\xD1\x87\xD0\xB0\xD1\x81 \xD0\xBF\xD0\xBE\xD1\x80\xD0\xBE\xD0\xB3: " + String(cfg.threshold, 1) + " \xC2\xB0\x43. \xD0\x92\xD0\xB2\xD0\xB5\xD0\xB4\xD0\xB8\xD1\x82\xD0\xB5 \xD0\xBD\xD0\xBE\xD0\xB2\xD1\x8B\xD0\xB9 \xD0\xBF\xD0\xBE\xD1\x80\xD0\xBE\xD0\xB3 (\xD1\x80\xD0\xB0\xD0\xB7\xD0\xB4\xD0\xB5\xD0\xBB\xD0\xB8\xD1\x82\xD0\xB5\xD0\xBB\xD1\x8C \xD1\x82\xD0\xBE\xD1\x87\xD0\xBA\xD0\xB0). \xD0\x94\xD0\xBB\xD1\x8F \xD0\xB2\xD1\x8B\xD1\x85\xD0\xBE\xD0\xB4\xD0\xB0 \xD0\xBD\xD0\xB0\xD0\xB6\xD0\xBC\xD0\xB8\xD1\x82\xD0\xB5 \xD0\xBA\xD0\xBE\xD0\xBC\xD0\xB0\xD0\xBD\xD0\xB4\xD1\x83 \xD1\x81\xD0\xBD\xD0\xBE\xD0\xB2\xD0\xB0.";
      cfg.set_limit_mode = true;
    } else {
      reply = "\xD0\x92\xD1\x8B\xD1\x85\xD0\xBE\xD0\xB4 \xD0\xB8\xD0\xB7 \xD1\x80\xD0\xB5\xD0\xB4\xD0\xB0\xD0\xBA\xD1\x82\xD0\xB8\xD1\x80\xD0\xBE\xD0\xB2\xD0\xB0\xD0\xBD\xD0\xB8\xD1\x8F";
      cfg.set_limit_mode = false;
    }
    markChanged();

  } else if (cfg.set_limit_mode) {
    float newLim = strtof(msg.text.c_str(), NULL);
    if (newLim != 0) {
      cfg.threshold = newLim;
      reply = "\xD0\xA3\xD1\x81\xD1\x82\xD0\xB0\xD0\xBD\xD0\xBE\xD0\xB2\xD0\xBB\xD0\xB5\xD0\xBD \xD0\xBF\xD0\xBE\xD1\x80\xD0\xBE\xD0\xB3 " + String(cfg.threshold, 1) + " \xC2\xB0\x43";
      cfg.set_limit_mode = false;
      send_msg = true;
      markChanged();
      if (wsConnected) sendState();
    }
  }

  if (reply.length() > 0) {
    bot.showMenuText(reply, tgButtons, msg.chatID);
  }
}

// ========================= SETUP =========================

void setup() {
  delay(2000);

  Serial.begin(115200);
  Serial.println("\n=== TempSensor ESP8266 ===");

  deviceId = "temp-" + String(ESP.getChipId(), HEX);
  Serial.println("Device ID: " + deviceId);

  pinMode(BTN_PIN, INPUT_PULLUP);

  EEPROM.begin(512);
  loadSettings();

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

    bot.setChatID(TG_USERS);
    bot.attach(onTelegramMsg);
    Serial.println("Telegram bot started");

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

  ds18b20.begin();
  ds18b20.setResolution(12);
  Serial.printf("DS18B20 devices: %d\n", ds18b20.getDeviceCount());

  getTemp();
  Serial.printf("Initial temp: %.1f C\n", currentTemp);
  Serial.println("Ready!");
}

// ========================= LOOP =========================

void loop() {
  if (isAPMode) {
    dnsServer.processNextRequest();
  } else {
    wsClient.loop();
    bot.tick();
    stateTick();
  }
  httpServer.handleClient();
  buttonTick();
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
  StaticJsonDocument<256> doc;
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
}

bool applyParam(const char* key, JsonVariant val) {
  if (strcmp(key, "threshold") == 0) {
    cfg.threshold = val.as<float>();
    send_msg = true;
    Serial.printf("Threshold set: %.1f\n", cfg.threshold);
    return true;
  }
  return false;
}

String buildStateJson() {
  String json = "{";
  json += "\"temp\":" + String(currentTemp, 1);
  json += ",\"threshold\":" + String(cfg.threshold, 1);
  json += "}";
  return json;
}

void sendHello() {
  String msg = "{\"type\":\"hello\",\"deviceId\":\"" + deviceId + "\",\"name\":\"\\u0422\\u0435\\u0440\\u043c\\u043e\\u043c\\u0435\\u0442\\u0440\",\"deviceType\":\"tempsensor\",\"state\":" + buildStateJson() + "}";
  wsClient.sendTXT(msg);
}

void sendState() {
  if (!wsConnected) return;
  String msg = "{\"type\":\"state\",\"state\":" + buildStateJson() + "}";
  wsClient.sendTXT(msg);
}

void stateTick() {
  if (millis() - state_timer < STATE_INTERVAL) return;
  state_timer = millis();

  getTemp();
  Serial.printf("Temp: %.1f C\n", currentTemp);

  if (wsConnected) sendState();

  // проверка порога (как в оригинале — одно уведомление, сброс при запросе температуры)
  if (currentTemp < cfg.threshold && send_msg) {
    bot.sendMessage("\xE2\x9D\x97\xEF\xB8\x8F\xD0\x92\xD0\xBD\xD0\xB8\xD0\xBC\xD0\xB0\xD0\xBD\xD0\xB8\xD0\xB5! \xD0\xA2\xD0\xB5\xD0\xBC\xD0\xBF\xD0\xB5\xD1\x80\xD0\xB0\xD1\x82\xD1\x83\xD1\x80\xD0\xB0 \xD0\xBC\xD0\xB5\xD0\xBD\xD1\x8C\xD1\x88\xD0\xB5 " + String(cfg.threshold, 1) + " \xC2\xB0\x43.\n\xD0\x9F\xD1\x80\xD0\xB8\xD0\xB5\xD0\xB7\xD0\xB6\xD0\xB0\xD0\xB9\xD1\x82\xD0\xB5 \xD0\xB4\xD0\xBE\xD0\xBC\xD0\xBE\xD0\xB9!");
    send_msg = false;
  }
}

// ========================= НАСТРОЙКИ =========================

void setDefaults() {
  cfg.marker = SETTINGS_MARKER;
  strncpy(cfg.wifi_ssid, "Blanik", sizeof(cfg.wifi_ssid));
  strncpy(cfg.wifi_pass, "61746723D", sizeof(cfg.wifi_pass));
  strncpy(cfg.ws_host, DEFAULT_WS_HOST, sizeof(cfg.ws_host));
  cfg.ws_port = DEFAULT_WS_PORT;
  cfg.threshold = DEFAULT_THRESHOLD;
  cfg.set_limit_mode = false;
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

void eepromTick() {
  if (settings_changed && millis() - save_timer > 10000) {
    saveSettings();
  }
}

void markChanged() {
  settings_changed = true;
  save_timer = millis();
}

// ========================= КНОПКА =========================

void buttonTick() {
  static bool btn_prev = HIGH;
  bool btn_now = digitalRead(BTN_PIN);

  if (btn_prev == HIGH && btn_now == LOW) {
    unsigned long now = millis();
    if (now - btn_last_press < 500) btn_press_count++;
    else btn_press_count = 1;
    btn_last_press = now;
  }
  btn_prev = btn_now;

  if (btn_press_count >= 2 && millis() - btn_last_press > 600) {
    Serial.println("Double press -> reset");
    cfg.wifi_ssid[0] = '\0';
    cfg.wifi_pass[0] = '\0';
    cfg.ws_host[0] = '\0';
    saveSettings();
    ESP.restart();
  }

  if (btn_press_count > 0 && btn_press_count < 2 && millis() - btn_last_press > 600) {
    btn_press_count = 0;
  }
}

// ========================= AP-MODE ВЕБ-СЕРВЕР =========================

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
