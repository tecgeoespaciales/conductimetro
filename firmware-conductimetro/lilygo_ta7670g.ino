// --- LIBRERIAS BASE DEL SISTEMA ---
#include <Arduino.h>
#include <secrets.h>
#include <Wire.h>
#include <math.h>
#include <stdlib.h>

// --- LIBRERIAS PARA PORTAL WI-FI ---
#include <WiFi.h>
#include <WebServer.h>

// --- SISTEMA DE ARCHIVOS Y SD ---
#include <Preferences.h>
#include <FS.h>
#include <SD.h>
#include <SPI.h>

// --- DEEP SLEEP Y RTC GPIO ---
#include <esp_sleep.h>
#include <driver/rtc_io.h>
#include <driver/gpio.h>

// --- MODEM LTE Y MQTT ---
#define TINY_GSM_MODEM_SIM7600
#include <TinyGsmClient.h>
#include <PubSubClient.h>
#define HAS_GSM_MQTT 1


namespace {
// =========================================================================
//                1. CONFIGURACION DE HARDWARE Y SERVICIOS
// =========================================================================
constexpr uint8_t I2C_SDA_PIN = 21;
constexpr uint8_t I2C_SCL_PIN = 22;
constexpr uint8_t ONEWIRE_PIN = 33;
constexpr bool ONEWIRE_AUTODETECT_PIN = false;
constexpr bool DS18X20_PARASITE_POWER = false;
constexpr bool DS18X20_TRY_BOTH_POWER_MODES = false;
constexpr uint8_t ADS1115_ADDR = 0x48;
constexpr uint8_t ADS1115_CHANNEL = 1;

constexpr uint8_t ADS1115_GAIN = 1;
constexpr uint8_t ADS1115_AVG_SAMPLES = 5;
constexpr uint8_t SD_MISO_PIN = 2;
constexpr uint8_t SD_MOSI_PIN = 15;
constexpr uint8_t SD_SCLK_PIN = 14;
constexpr uint8_t SD_CS_PIN = 13;
constexpr int MODEM_TX_PIN = 26;
constexpr int MODEM_RX_PIN = 27;
constexpr uint8_t PIN_MODEM_POWER = 12;
constexpr uint8_t PIN_MODEM_PWRKEY = 4;
constexpr bool MODEM_POWER_PIN_ALWAYS_HIGH = true;
constexpr bool MODEM_USE_PWRKEY_BOOT = true;
constexpr uint32_t MODEM_PWRKEY_PULSE_MS = 1100UL;
constexpr uint32_t MODEM_PWRKEY_SETTLE_MS = 2500UL;
constexpr uint32_t MODEM_PREPARE_SEND_MS = 8000UL;
constexpr uint32_t MODEM_ACTIVE_WINDOW_MS = 60000UL;
constexpr uint8_t PIN_BATTERY = 35;
constexpr float BATTERY_DIVIDER_RATIO = 2.0f;
constexpr float BATTERY_CALIBRATION_FACTOR = 1.097f;
constexpr float BATTERY_EMPTY_V = 3.20f;
constexpr float BATTERY_FULL_V = 4.20f;
constexpr float NOISE_THRESHOLD_V = 0.0f;  
constexpr float EC_NOISE_FLOOR_US = 5.0f;    
constexpr uint32_t SERIAL_BAUD = 115200;
constexpr uint32_t DEFAULT_SAMPLE_INTERVAL_MS = 60000;  // Intervalo entre ciclos de 30 muestras: 60 segundos
constexpr uint8_t DEFAULT_SAMPLES_PER_WAKE = 30;        // 30 muestras por ciclo (rápidas, sin descanso)
constexpr uint16_t DEFAULT_SD_SAVE_WINDOW_MIN = 1;      // Guardar cada minuto en SD
constexpr uint16_t DEFAULT_LTE_SEND_WINDOW_MIN = 60;    // Enviar promedio cada 60 minutos    
constexpr uint32_t IDLE_SLEEP_SLICE_MS = 5000;  
constexpr uint8_t DS3231_ADDR = 0x68;
constexpr char AP_SSID[] = "CONDUCTIMETRO_CONFIG";
constexpr char AP_PASSWORD[] = REAL_AP_PASSWORD;
constexpr uint32_t AP_AUTO_EXIT_MS = 60000UL;  // 60 segundos: timeout para AP sin conexion serial
constexpr uint8_t AP_EXIT_BUTTON_PRIMARY_PIN = 0;
constexpr uint8_t AP_EXIT_BUTTON_FALLBACK_PIN = 34;
constexpr uint32_t AP_BUTTON_DEBOUNCE_MS = 35;
constexpr char SD_LOG_PATH[] = "/mediciones.txt";
constexpr char MODEM_APN[] = REAL_MODEM_APN;
constexpr char MODEM_GPRS_USER[] = "";
constexpr char MODEM_GPRS_PASS[] = "";
constexpr char MQTT_SERVER[] = REAL_MQTT_SERVER;
constexpr uint16_t MQTT_PORT = 1883;
constexpr char MQTT_USER[] = REAL_MQTT_USER;
constexpr char MQTT_PASS[] = REAL_MQTT_PASS;
constexpr char MQTT_TOPIC[] = REAL_MQTT_TOPIC;
constexpr char MQTT_CLIENT_ID[] = REAL_MQTT_CLIENT_ID;
constexpr uint16_t VALID_TIME_MIN_YEAR = 2024;
constexpr uint16_t VALID_TIME_MAX_YEAR = 2069;


constexpr bool RTC_FORCE_SET_ON_BOOT = false;

// =========================================================================
//             2. ESTRUCTURAS DE DATOS Y CURVAS DE CALIBRACION
// =========================================================================
struct RtcDateTime {
  uint16_t year;
  uint8_t month;
  uint8_t day;
  uint8_t hour;
  uint8_t minute;
  uint8_t second;
};

struct KRange {
  float minV;
  float maxV;
  float k;
};

struct KnownCalPoint {
  float v;
  float k;
};

constexpr float POLY_A = 133.42f;
constexpr float POLY_B = 255.86f;
constexpr float POLY_C = 857.39f;
constexpr float TEMP_REF_C = 25.0f;
constexpr float COEF_TEMP = 0.02f;
constexpr float MAX_EC_US = 3000.0f;
constexpr float KNOWN_CAL_MIN_V = 0.05f;
constexpr float KNOWN_CAL_MAX_V = 3.20f;
constexpr float KNOWN_K_MIN = 0.20f;
constexpr float KNOWN_K_MAX = 6.00f;


const KRange LAB_K_RANGES[] = {
    {0.000000f, 0.105848f, 0.858724f},
    {0.105848f, 0.186937f, 0.681144f},
    {0.186937f, 0.245598f, 0.537636f},
    {0.245598f, 0.322972f, 0.976365f},
    {0.322972f, 0.431948f, 1.024897f},
    {0.431948f, 0.517394f, 1.150721f},
    {0.517394f, 0.598582f, 1.234628f},
    {0.598582f, 0.678322f, 1.271184f},
    {0.678322f, 0.762783f, 1.338425f},
    {0.762783f, 0.856402f, 1.360273f},
    {0.856402f, 0.958736f, 1.322626f},
    {0.958736f, 1.204168f, 1.307752f},
    {1.204168f, 1.662699f, 1.263621f},
    {1.662699f, 2.112308f, 1.220740f},
    {2.112308f, 2.372536f, 1.364694f},
    {2.372536f, 2.473318f, 1.705371f},
    {2.473318f, 2.507401f, 1.936834f},
    {2.507401f, 2.527843f, 2.279828f},
    {2.527843f, 10.000000f, 2.627152f},
};

struct MinuteSample {
  RtcDateTime now;
  float tempC;
  bool tempOk;
  float ecUs;
  float voltageV;
  bool valid;
};
// =========================================================================
//              3. ESTADO GLOBAL (RAM, RTC_DATA_ATTR Y OBJETOS)
// =========================================================================
constexpr RtcDateTime RTC_BOOT_DT = {2026, 4, 9, 9, 39, 0};
uint8_t lastScratchpad[9] = {0};
bool lastScratchpadValid = false;
uint8_t lastScratchpadCrcCalc = 0;
bool lastTempReadUsedParasite = false;

enum CalibrationMode : uint8_t { MODE_LABORATORY = 0, MODE_KNOWN = 1 };
CalibrationMode calibrationMode = MODE_LABORATORY;

uint32_t sampleIntervalMs = DEFAULT_SAMPLE_INTERVAL_MS;
uint8_t samplesPerWake = DEFAULT_SAMPLES_PER_WAKE;
constexpr uint16_t sdSaveWindowMin = DEFAULT_SD_SAVE_WINDOW_MIN;
uint16_t lteSendWindowMin = DEFAULT_LTE_SEND_WINDOW_MIN;

RTC_DATA_ATTR uint16_t sdBatchCount = 0;
RTC_DATA_ATTR uint16_t sdBatchTempValidCount = 0;
RTC_DATA_ATTR float sdBatchTempSum = 0.0f;
RTC_DATA_ATTR float sdBatchEcSum = 0.0f;

RTC_DATA_ATTR uint16_t lteBatchCount = 0;
RTC_DATA_ATTR uint16_t lteBatchTempValidCount = 0;
RTC_DATA_ATTR float lteBatchTempSum = 0.0f;
RTC_DATA_ATTR float lteBatchEcSum = 0.0f;
RTC_DATA_ATTR bool forceLteSendRequested = false;
RTC_DATA_ATTR uint64_t lastProcessTimeMs = 0;  // Timestamp del inicio del último procesamiento

bool pendingModeChangeNotification = false;
CalibrationMode pendingModeValue = MODE_LABORATORY;

KRange *knownRanges = nullptr;
uint16_t knownRangesCount = 0;
KnownCalPoint *knownPoints = nullptr;
uint16_t knownPointsCount = 0;

Preferences prefs;

WebServer webServer(80);
volatile bool apModeActive = false;
volatile uint32_t apModeStartMs = 0;
bool apRoutesRegistered = false;
uint8_t oneWireDataPin = ONEWIRE_PIN;
bool sdReady = false;
SPIClass sdSpi(VSPI);
HardwareSerial SerialAT(1);
TinyGsm modem(SerialAT);
TinyGsmClient gsmClient(modem);
PubSubClient mqtt(gsmClient);
bool modemSerialReady = false;
bool modemPowerActive = false;
uint32_t modemActiveUntilMs = 0;
uint8_t modemPwrKeyIdleLevel = LOW;
// =========================================================================
//                 4. ZONA DEL MODEM (ENERGIA, UART Y RED)
// =========================================================================
void modemKeepAliveWindow() {
  modemActiveUntilMs = millis() + MODEM_ACTIVE_WINDOW_MS;
}

void modemPowerOn() {
  pinMode(PIN_MODEM_POWER, OUTPUT);

  if (MODEM_POWER_PIN_ALWAYS_HIGH) {
    digitalWrite(PIN_MODEM_POWER, HIGH);
    if (!modemPowerActive) {
      modemPowerActive = true;
      modemSerialReady = false;
      Serial.println("LTE_INFO pin modem power fijado en HIGH (siempre energizado)");
    }
    modemKeepAliveWindow();
    return;
  }

  if (modemPowerActive) {
    modemKeepAliveWindow();
    return;
  }

  Serial.println("LTE_INFO encendiendo modem para envio");
  digitalWrite(PIN_MODEM_POWER, HIGH);
  delay(1200);
  Serial.printf("LTE_INFO espera de arranque modem (%lu ms)\n", static_cast<unsigned long>(MODEM_PREPARE_SEND_MS));
  delay(MODEM_PREPARE_SEND_MS);
  modemPowerActive = true;
  modemSerialReady = false;
  modemKeepAliveWindow();
}

void modemPowerOff() {
#if HAS_GSM_MQTT
  if (!modemPowerActive && !MODEM_POWER_PIN_ALWAYS_HIGH) {
    return;
  }

  if (mqtt.connected()) {
    mqtt.disconnect();
  }
  if (modemSerialReady && modem.isGprsConnected()) {
    modem.gprsDisconnect();
  }

  if (MODEM_POWER_PIN_ALWAYS_HIGH) {
    if (modemSerialReady) {
      SerialAT.println("AT+CGATT=0");
      delay(150);
      SerialAT.flush();
      SerialAT.end();
    }

    pinMode(PIN_MODEM_POWER, OUTPUT);
    digitalWrite(PIN_MODEM_POWER, HIGH);

    modemPowerActive = true;
    modemSerialReady = false;
    modemActiveUntilMs = 0;
    Serial.println("LTE_INFO sesion LTE cerrada (pin power se mantiene en HIGH)");
    return;
  }

  if (modemSerialReady) {
    SerialAT.println("AT+CGATT=0");
    delay(150);
    SerialAT.println("AT+CPOWD=1");
    delay(300);
    SerialAT.flush();
    SerialAT.end();
  }

  pinMode(PIN_MODEM_POWER, OUTPUT);
  digitalWrite(PIN_MODEM_POWER, LOW);
  delay(120);

  modemPowerActive = false;
  modemSerialReady = false;
  modemActiveUntilMs = 0;
  Serial.println("LTE_INFO modem apagado fisicamente");
#endif
}

void modemCloseDataSession() {
#if HAS_GSM_MQTT
  if (!modemPowerActive || !modemSerialReady) {
    return;
  }

  if (mqtt.connected()) {
    mqtt.disconnect();
  }
  if (modem.isGprsConnected()) {
    modem.gprsDisconnect();
  }
  SerialAT.println("AT+CGATT=0");
  delay(150);
#endif
}

void modemHandlePowerWindow() {
  if (!modemPowerActive || modemActiveUntilMs == 0) {
    return;
  }

  if (static_cast<int32_t>(millis() - modemActiveUntilMs) >= 0) {
    if (MODEM_POWER_PIN_ALWAYS_HIGH) {
      Serial.println("LTE_INFO ventana de envio agotada, cerrando sesion (pin power HIGH)");
    } else {
      Serial.println("LTE_INFO ventana de envio agotada, apagando modem");
    }
    modemPowerOff();
  }
}

uint8_t bcdToDec(uint8_t bcd) {
  return static_cast<uint8_t>((bcd >> 4) * 10 + (bcd & 0x0F));
}
// =========================================================================
//                     5. LECTURA Y CALCULO DE BATERIA
// =========================================================================
String readBatteryPercent3Digits() {
  pinMode(PIN_BATTERY, INPUT);

#if defined(ADC_11db)
  analogSetPinAttenuation(PIN_BATTERY, ADC_11db);
#elif defined(ADC_ATTEN_DB_11)
  analogSetPinAttenuation(PIN_BATTERY, ADC_ATTEN_DB_11);
#endif

  analogReadResolution(12);

  long sumMv = 0;
  uint8_t mvCount = 0;
  for (uint8_t i = 0; i < 10; i++) {
    const int sampleMv = analogReadMilliVolts(PIN_BATTERY);
    if (sampleMv > 0) {
      sumMv += sampleMv;
      mvCount++;
    }
    delay(8);
  }

  long sumAdc = 0;
  for (uint8_t i = 0; i < 10; i++) {
    sumAdc += analogRead(PIN_BATTERY);
    delay(2);
  }

  const float adcAvg = static_cast<float>(sumAdc) / 10.0f;
  float voltagePin = (adcAvg / 4095.0f) * 3.3f;
  if (mvCount > 0) {
    voltagePin = (static_cast<float>(sumMv) / static_cast<float>(mvCount)) / 1000.0f;
  }

  const float batteryV = voltagePin * BATTERY_DIVIDER_RATIO * BATTERY_CALIBRATION_FACTOR;

  float soc = (batteryV - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V);
  if (soc < 0.0f) {
    soc = 0.0f;
  }
  if (soc > 1.0f) {
    soc = 1.0f;
  }

  int percent = static_cast<int>(lroundf(soc * 100.0f));

  Serial.printf("BAT_INFO fuente=adc raw=%.0f vpin=%.3f vbat=%.3f pct=%d\n",
                adcAvg,
                voltagePin,
                batteryV,
                percent);

  if (percent > 100) {
    percent = 100;
  }
  if (percent < 0) {
    percent = 0;
  }

  char out[4] = {0};
  snprintf(out, sizeof(out), "%03d", percent);
  return String(out);
}

bool modemProbeAtResponse(uint32_t timeoutMs, uint8_t attempts) {
#if HAS_GSM_MQTT
  while (SerialAT.available()) {
    SerialAT.read();
  }

  for (uint8_t attempt = 0; attempt < attempts; attempt++) {
    SerialAT.println("AT");
    String line;
    const uint32_t startMs = millis();

    while (millis() - startMs < timeoutMs) {
      while (SerialAT.available()) {
        const char c = static_cast<char>(SerialAT.read());
        if (c == '\r') {
          continue;
        }
        if (c == '\n') {
          line.trim();
          if (line == "OK") {
            return true;
          }
          if (line == "ERROR") {
            break;
          }
          line = "";
        } else if (line.length() < 64) {
          line += c;
        }
      }
      delay(5);
    }
  }
#else
  (void)timeoutMs;
  (void)attempts;
#endif
  return false;
}

void modemPulsePwrKey(bool activeHighPulse) {
  if (!MODEM_USE_PWRKEY_BOOT) {
    return;
  }

  pinMode(PIN_MODEM_PWRKEY, OUTPUT);
  const uint8_t idleLevel = activeHighPulse ? LOW : HIGH;
  const uint8_t pulseLevel = activeHighPulse ? HIGH : LOW;

  digitalWrite(PIN_MODEM_PWRKEY, idleLevel);
  delay(80);
  digitalWrite(PIN_MODEM_PWRKEY, pulseLevel);
  delay(MODEM_PWRKEY_PULSE_MS);
  digitalWrite(PIN_MODEM_PWRKEY, idleLevel);
  delay(MODEM_PWRKEY_SETTLE_MS);
}

void modemTryBootWithPwrKey() {
  if (!MODEM_USE_PWRKEY_BOOT) {
    return;
  }

  if (modemProbeAtResponse(500, 2)) {
    Serial.println("LTE_INFO modem responde a AT sin pulso PWRKEY");
    return;
  }

  Serial.println("LTE_INFO modem sin AT, intento de arranque con pulso PWRKEY HIGH");
  modemPulsePwrKey(true);
  if (modemProbeAtResponse(700, 4)) {
    modemPwrKeyIdleLevel = LOW;
    pinMode(PIN_MODEM_PWRKEY, OUTPUT);
    digitalWrite(PIN_MODEM_PWRKEY, modemPwrKeyIdleLevel);
    Serial.println("LTE_INFO modem encendido con pulso PWRKEY HIGH");
    return;
  }

  Serial.println("LTE_INFO sin respuesta, intento de arranque con pulso PWRKEY LOW");
  modemPulsePwrKey(false);
  if (modemProbeAtResponse(700, 4)) {
    modemPwrKeyIdleLevel = HIGH;
    pinMode(PIN_MODEM_PWRKEY, OUTPUT);
    digitalWrite(PIN_MODEM_PWRKEY, modemPwrKeyIdleLevel);
    Serial.println("LTE_INFO modem encendido con pulso PWRKEY LOW");
    return;
  }

  modemPwrKeyIdleLevel = LOW;
  pinMode(PIN_MODEM_PWRKEY, OUTPUT);
  digitalWrite(PIN_MODEM_PWRKEY, modemPwrKeyIdleLevel);
  Serial.println("LTE_WARN sin respuesta AT tras secuencias PWRKEY");
}

void modemEnsureSerial() {
  modemKeepAliveWindow();

  if (modemSerialReady) {
    return;
  }

  modemPowerOn();

  SerialAT.begin(115200, SERIAL_8N1, MODEM_RX_PIN, MODEM_TX_PIN);
  delay(300);

  modemTryBootWithPwrKey();
  if (!modemProbeAtResponse(700, 3)) {
    Serial.println("LTE_WARN UART lista pero el modem no responde a AT");
  }

  
  SerialAT.println("AT+CTZU=1");
  delay(150);

  modemSerialReady = true;
  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
}

void modemRunAtAndLog(const char *cmd, const char *label, uint32_t timeoutMs = 2500) {
#if HAS_GSM_MQTT
  modemEnsureSerial();

  while (SerialAT.available()) {
    SerialAT.read();
  }

  SerialAT.println(cmd);
  String line;
  const uint32_t startMs = millis();
  bool printedAny = false;

  while (millis() - startMs < timeoutMs) {
    while (SerialAT.available()) {
      const char c = static_cast<char>(SerialAT.read());
      if (c == '\r') {
        continue;
      }
      if (c == '\n') {
        line.trim();
        if (line.length() > 0) {
          Serial.printf("4G_DIAG %s: %s\n", label, line.c_str());
          printedAny = true;
          if (line == "OK" || line == "ERROR") {
            return;
          }
        }
        line = "";
      } else if (line.length() < 160) {
        line += c;
      }
    }
    delay(5);
  }

  if (!printedAny) {
    Serial.printf("4G_DIAG %s: sin respuesta\n", label);
  }
#else
  (void)cmd;
  (void)label;
  (void)timeoutMs;
#endif
}

void modemPrintDiagnostics() {
#if HAS_GSM_MQTT
  modemRunAtAndLog("AT", "AT", 1200);
  modemRunAtAndLog("AT+CPIN?", "CPIN", 1500);
  modemRunAtAndLog("AT+CSQ", "CSQ", 1500);
  modemRunAtAndLog("AT+CEREG?", "CEREG", 1500);
  modemRunAtAndLog("AT+CGATT?", "CGATT", 1500);
  modemRunAtAndLog("AT+CGPADDR", "CGPADDR", 1800);
#endif
}

bool modemEnsureDataConnection() {
#if HAS_GSM_MQTT
  // 1) Asegura UART y estado base del modem.
  modemEnsureSerial();

  // 2) Verifica registro en red celular.
  if (!modem.isNetworkConnected()) {
    if (!modem.waitForNetwork(60000L)) {
      Serial.println("4G_WARN sin red celular");
      modemPrintDiagnostics();
      return false;
    }
  }

  // 3) Verifica sesion de datos (APN/GPRS).
  if (!modem.isGprsConnected()) {
    if (!modem.gprsConnect(MODEM_APN, MODEM_GPRS_USER, MODEM_GPRS_PASS)) {
      Serial.println("4G_WARN sin GPRS/APN");
      modemPrintDiagnostics();
      return false;
    }
  }

  return true;
#else
  modemEnsureSerial();
  return false;
#endif
}

void formatDateTimeDDMMYY(const RtcDateTime &dt, char *dateOut, size_t dateLen, char *timeOut, size_t timeLen) {
  snprintf(dateOut, dateLen, "%02u/%02u/%02u", dt.day, dt.month, static_cast<unsigned>(dt.year % 100));
  snprintf(timeOut, timeLen, "%02u:%02u:%02u", dt.hour, dt.minute, dt.second);
}

bool syncRtcWithNetworkTimeIfNeeded(RtcDateTime &nowOut);
bool isValidDateTime(const RtcDateTime &dt);
bool ds3231ReadTime(RtcDateTime &out);
bool ds3231WriteTime(const RtcDateTime &dt);
// =========================================================================
//                6. PUBLICACION MQTT (PROMEDIOS Y EVENTOS)
// =========================================================================
bool publishMqttAverage(const RtcDateTime &now, float avgTemp, bool avgTempOk, float avgEc) {
#if HAS_GSM_MQTT
  if (!modemEnsureDataConnection()) {
    modemCloseDataSession();
    modemKeepAliveWindow();
    return false;
  }

  RtcDateTime publishNow = now;
  RtcDateTime syncedNow = {};
  if (syncRtcWithNetworkTimeIfNeeded(syncedNow)) {
    publishNow = syncedNow;
  } else {
    Serial.println("RTC_SYNC_WARN se mantiene hora local para payload MQTT");
  }

  if (!mqtt.connected()) {
    if (!mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)) {
      Serial.printf("MQTT_WARN conexion fallida state=%d\n", mqtt.state());
      modemPrintDiagnostics();
      modemCloseDataSession();
      modemKeepAliveWindow();
      return false;
    }
  }

  char fecha[12] = {0};
  char hora[12] = {0};
  formatDateTimeDDMMYY(publishNow, fecha, sizeof(fecha), hora, sizeof(hora));
  const String bat = readBatteryPercent3Digits();

  char payload[200] = {0};
  if (avgTempOk) {
    snprintf(payload, sizeof(payload),
             "{\"f\":\"%s\",\"h\":\"%s\",\"d\":\"%.1f\",\"t\":\"%.2f\",\"b\":\"%s\"}",
             fecha,
             hora,
             avgEc,
             avgTemp,
             bat.c_str());
  } else {
    snprintf(payload, sizeof(payload),
             "{\"f\":\"%s\",\"h\":\"%s\",\"d\":\"%.1f\",\"t\":null,\"b\":\"%s\"}",
             fecha,
             hora,
             avgEc,
             bat.c_str());
  }

  const bool ok = mqtt.publish(MQTT_TOPIC, payload);
  if (!ok) {
    Serial.printf("MQTT_WARN publish fallido state=%d\n", mqtt.state());
    modemCloseDataSession();
    modemKeepAliveWindow();
    return false;
  }

  Serial.printf("MQTT_OK topic=%s payload=%s\n", MQTT_TOPIC, payload);
  mqtt.loop();
  modemCloseDataSession();
  modemKeepAliveWindow();
  return true;
#else
  (void)now;
  (void)avgTemp;
  (void)avgTempOk;
  (void)avgEc;
  modemEnsureSerial();
  return false;
#endif
}

const char *calibrationModeToString(CalibrationMode mode) {
  return (mode == MODE_KNOWN) ? "conocidos" : "laboratorio";
}

bool publishMqttModeChange(const RtcDateTime &now, CalibrationMode mode) {
#if HAS_GSM_MQTT
  if (!modemEnsureDataConnection()) {
    modemCloseDataSession();
    modemKeepAliveWindow();
    return false;
  }

  RtcDateTime publishNow = now;
  RtcDateTime syncedNow = {};
  if (syncRtcWithNetworkTimeIfNeeded(syncedNow)) {
    publishNow = syncedNow;
  } else {
    Serial.println("RTC_SYNC_WARN se mantiene hora local para payload mode_change");
  }

  if (!mqtt.connected()) {
    if (!mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)) {
      Serial.printf("MQTT_WARN conexion fallida (mode_change) state=%d\n", mqtt.state());
      modemPrintDiagnostics();
      modemCloseDataSession();
      modemKeepAliveWindow();
      return false;
    }
  }

  char fecha[12] = {0};
  char hora[12] = {0};
  formatDateTimeDDMMYY(publishNow, fecha, sizeof(fecha), hora, sizeof(hora));

  char payload[220] = {0};
  snprintf(payload, sizeof(payload),
           "{\"f\":\"%s\",\"h\":\"%s\",\"mode\":\"%s\"}",
           fecha,
           hora,
           calibrationModeToString(mode));

  const bool ok = mqtt.publish(MQTT_TOPIC, payload);
  if (!ok) {
    Serial.printf("MQTT_WARN publish fallido (mode_change) state=%d\n", mqtt.state());
    modemCloseDataSession();
    modemKeepAliveWindow();
    return false;
  }

  Serial.printf("MQTT_OK mode_change topic=%s payload=%s\n", MQTT_TOPIC, payload);
  mqtt.loop();
  modemCloseDataSession();
  modemKeepAliveWindow();
  return true;
#else
  (void)now;
  (void)mode;
  modemEnsureSerial();
  return false;
#endif
}

void queueModeChangeNotification(CalibrationMode mode) {
  pendingModeChangeNotification = true;
  pendingModeValue = mode;
}

void tryFlushPendingModeChange(const RtcDateTime &now) {
  if (!pendingModeChangeNotification) {
    return;
  }

  if (publishMqttModeChange(now, pendingModeValue)) {
    pendingModeChangeNotification = false;
    Serial.printf("MQTT_OK cambio de modo confirmado (%s)\n", calibrationModeToString(pendingModeValue));
  }
}
// =========================================================================
//            7. SD, PARSING DE FECHAS Y SINCRONIZACION RTC/RED
// =========================================================================
bool initSdCard() {
  if (sdReady) {
    return true;
  }

  pinMode(SD_CS_PIN, OUTPUT);
  digitalWrite(SD_CS_PIN, HIGH);

  sdSpi.begin(SD_SCLK_PIN, SD_MISO_PIN, SD_MOSI_PIN, SD_CS_PIN);
  if (!SD.begin(SD_CS_PIN, sdSpi, 4000000U)) {
    sdReady = false;
    Serial.println("SD_FAIL begin() error");
    return false;
  }

  sdReady = true;
  return true;
}

bool ensureSdLogFileReady() {
  if (!initSdCard()) {
    return false;
  }

  if (SD.exists(SD_LOG_PATH)) {
    return true;
  }

  File file = SD.open(SD_LOG_PATH, FILE_WRITE);
  if (!file) {
    Serial.println("SD_FAIL no se pudo crear /mediciones.txt");
    return false;
  }

  file.println("# fecha hora temperatura conductividad");
  file.close();
  Serial.println("SD_OK archivo /mediciones.txt creado");
  return true;
}

uint8_t daysInMonthCalc(uint16_t year, uint8_t month) {
  static const uint8_t days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if (month < 1 || month > 12) {
    return 0;
  }
  if (month == 2) {
    const bool leap = ((year % 4 == 0 && year % 100 != 0) || (year % 400 == 0));
    if (leap) {
      return 29;
    }
  }
  return days[month - 1];
}

bool parseDateTimeFromStrings(const String &dateStr, const String &timeStr, RtcDateTime &out) {
  if (dateStr.length() != 10 || timeStr.length() != 5) {
    return false;
  }

  if (dateStr[4] != '-' || dateStr[7] != '-' || timeStr[2] != ':') {
    return false;
  }

  out.year = static_cast<uint16_t>(dateStr.substring(0, 4).toInt());
  out.month = static_cast<uint8_t>(dateStr.substring(5, 7).toInt());
  out.day = static_cast<uint8_t>(dateStr.substring(8, 10).toInt());
  out.hour = static_cast<uint8_t>(timeStr.substring(0, 2).toInt());
  out.minute = static_cast<uint8_t>(timeStr.substring(3, 5).toInt());
  out.second = 0;

  if (out.year < 2000 || out.year > 2099) {
    return false;
  }
  if (out.month < 1 || out.month > 12) {
    return false;
  }
  const uint8_t maxDay = daysInMonthCalc(out.year, out.month);
  if (out.day < 1 || out.day > maxDay) {
    return false;
  }
  if (out.hour > 23 || out.minute > 59) {
    return false;
  }

  return true;
}

uint32_t dateTimeToComparable(const RtcDateTime &dt) {
  uint32_t days = 0;
  for (uint16_t y = 2000; y < dt.year; y++) {
    const bool leap = ((y % 4 == 0 && y % 100 != 0) || (y % 400 == 0));
    days += leap ? 366U : 365U;
  }

  for (uint8_t m = 1; m < dt.month; m++) {
    days += static_cast<uint32_t>(daysInMonthCalc(dt.year, m));
  }

  days += static_cast<uint32_t>(dt.day - 1);
  return days * 86400UL + static_cast<uint32_t>(dt.hour) * 3600UL + static_cast<uint32_t>(dt.minute) * 60UL + dt.second;
}

bool parseNetworkDateTimeCclk(const String &raw, RtcDateTime &out) {
  
  if (raw.length() < 17) {
    return false;
  }
  if (raw[2] != '/' || raw[5] != '/' || raw[8] != ',' || raw[11] != ':' || raw[14] != ':') {
    return false;
  }

  const int yy = raw.substring(0, 2).toInt();
  const int mm = raw.substring(3, 5).toInt();
  const int dd = raw.substring(6, 8).toInt();
  const int hh = raw.substring(9, 11).toInt();
  const int mi = raw.substring(12, 14).toInt();
  const int ss = raw.substring(15, 17).toInt();

  
  if (yy < 24 || yy > 69) {
    return false;
  }

  out.year = static_cast<uint16_t>(2000 + yy);
  out.month = static_cast<uint8_t>(mm);
  out.day = static_cast<uint8_t>(dd);
  out.hour = static_cast<uint8_t>(hh);
  out.minute = static_cast<uint8_t>(mi);
  out.second = static_cast<uint8_t>(ss);
  return isValidDateTime(out);
}

bool modemReadNetworkDateTime(RtcDateTime &out) {
#if HAS_GSM_MQTT
  modemEnsureSerial();

  const String dt = modem.getGSMDateTime(DATE_FULL);
  if (dt.length() >= 17 && parseNetworkDateTimeCclk(dt, out)) {
    Serial.printf("RTC_SYNC_NET getGSMDateTime=%s\n", dt.c_str());
    return true;
  }

  while (SerialAT.available()) {
    SerialAT.read();
  }

  SerialAT.println("AT+CCLK?");

  String line;
  bool got = false;
  uint32_t startMs = millis();
  while (millis() - startMs < 3000) {
    while (SerialAT.available()) {
      char c = static_cast<char>(SerialAT.read());
      if (c == '\r') {
        continue;
      }
      if (c == '\n') {
        line.trim();
        if (line.startsWith("+CCLK:")) {
          const int q1 = line.indexOf('"');
          const int q2 = line.lastIndexOf('"');
          if (q1 >= 0 && q2 > q1) {
            const String ts = line.substring(q1 + 1, q2);
            if (parseNetworkDateTimeCclk(ts, out)) {
              got = true;
            }
          }
        }

        if (line == "OK") {
          return got;
        }
        line = "";
      } else {
        if (line.length() < 120) {
          line += c;
        }
      }
    }
    delay(5);
  }

  return got;
#else
  (void)out;
  return false;
#endif
}

bool syncRtcWithNetworkTimeIfNeeded(RtcDateTime &nowOut) {
#if HAS_GSM_MQTT
  RtcDateTime netTime = {};
  if (!modemReadNetworkDateTime(netTime)) {
    Serial.println("RTC_SYNC_WARN no se pudo leer hora desde red 4G");
    if (ds3231ReadTime(nowOut) && nowOut.year >= VALID_TIME_MIN_YEAR && nowOut.year <= VALID_TIME_MAX_YEAR) {
      return true;
    }
    Serial.println("RTC_SYNC_WARN RTC local tambien invalido");
    return false;
  }

  RtcDateTime rtcNow = {};
  if (!ds3231ReadTime(rtcNow)) {
    if (ds3231WriteTime(netTime)) {
      nowOut = netTime;
      Serial.println("RTC_SYNC_OK RTC recuperado desde hora de red 4G");
      return true;
    }
    Serial.println("RTC_SYNC_WARN fallo escritura RTC con hora de red");
    nowOut = netTime;
    return false;
  }

  if (rtcNow.year < VALID_TIME_MIN_YEAR || rtcNow.year > VALID_TIME_MAX_YEAR) {
    if (ds3231WriteTime(netTime)) {
      nowOut = netTime;
      Serial.println("RTC_SYNC_OK RTC corregido por rango de anio invalido");
      return true;
    }
    Serial.println("RTC_SYNC_WARN no se pudo corregir RTC invalido");
    nowOut = netTime;
    return false;
  }

  const uint32_t rtcTs = dateTimeToComparable(rtcNow);
  const uint32_t netTs = dateTimeToComparable(netTime);
  const uint32_t diff = (rtcTs > netTs) ? (rtcTs - netTs) : (netTs - rtcTs);

  if (diff > 1U) {
    if (ds3231WriteTime(netTime)) {
      nowOut = netTime;
      Serial.printf("RTC_SYNC_OK ajustado desde 4G (delta=%lu s)\n", static_cast<unsigned long>(diff));
      return true;
    }
    Serial.println("RTC_SYNC_WARN no se pudo ajustar RTC");
    nowOut = rtcNow;
    return false;
  }

  nowOut = rtcNow;
  return true;
#else
  (void)nowOut;
  return false;
#endif
}

bool parseTimestampFromLogLine(const String &line, RtcDateTime &dt) {
  if (line.length() < 19) {
    return false;
  }

  if (line[4] != '-' || line[7] != '-' || line[10] != ' ' || line[13] != ':' || line[16] != ':') {
    return false;
  }

  dt.year = static_cast<uint16_t>(line.substring(0, 4).toInt());
  dt.month = static_cast<uint8_t>(line.substring(5, 7).toInt());
  dt.day = static_cast<uint8_t>(line.substring(8, 10).toInt());
  dt.hour = static_cast<uint8_t>(line.substring(11, 13).toInt());
  dt.minute = static_cast<uint8_t>(line.substring(14, 16).toInt());
  dt.second = static_cast<uint8_t>(line.substring(17, 19).toInt());

  if (dt.year < 2000 || dt.year > 2099) {
    return false;
  }
  if (dt.month < 1 || dt.month > 12) {
    return false;
  }
  const uint8_t maxDay = daysInMonthCalc(dt.year, dt.month);
  if (dt.day < 1 || dt.day > maxDay) {
    return false;
  }
  if (dt.hour > 23 || dt.minute > 59 || dt.second > 59) {
    return false;
  }

  return true;
}

bool appendMeasurementToSd(const RtcDateTime &now, float tempC, bool tempOk, float ecUs) {
  if (!ensureSdLogFileReady()) {
    return false;
  }

  File file = SD.open(SD_LOG_PATH, FILE_APPEND);
  if (!file) {
    sdReady = false;
    return false;
  }

  if (tempOk) {
    file.printf("%04u-%02u-%02u %02u:%02u:%02u %.2f %.1f\n",
                now.year, now.month, now.day,
                now.hour, now.minute, now.second,
                tempC,
                ecUs);
  } else {
    file.printf("%04u-%02u-%02u %02u:%02u:%02u NaN %.1f\n",
                now.year, now.month, now.day,
                now.hour, now.minute, now.second,
                ecUs);
  }
  file.close();
  Serial.printf("SD_OK guardado %04u-%02u-%02u %02u:%02u:%02u\n",
                now.year, now.month, now.day,
                now.hour, now.minute, now.second);
  return true;
}

uint8_t decToBcd(uint8_t dec) {
  return static_cast<uint8_t>(((dec / 10) << 4) | (dec % 10));
}

bool ds3231IsPresent() {
  Wire.beginTransmission(DS3231_ADDR);
  return Wire.endTransmission() == 0;
}





// =========================================================================
//                   8. DRIVER DE BAJO NIVEL RTC DS3231
// =========================================================================
bool ds3231ReadTime(RtcDateTime &out) {
  Wire.beginTransmission(DS3231_ADDR);
  Wire.write(0x00);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  if (Wire.requestFrom(static_cast<int>(DS3231_ADDR), 7) != 7) {
    return false;
  }

  const uint8_t secReg = Wire.read();
  const uint8_t minReg = Wire.read();
  const uint8_t hourReg = Wire.read();
  Wire.read();  
  const uint8_t dayReg = Wire.read();
  const uint8_t monthReg = Wire.read();
  const uint8_t yearReg = Wire.read();

  out.second = bcdToDec(secReg & 0x7F);
  out.minute = bcdToDec(minReg & 0x7F);

  
  if (hourReg & 0x40) {
    const uint8_t h12 = bcdToDec(hourReg & 0x1F);
    const bool pm = hourReg & 0x20;
    if (h12 == 12) {
      out.hour = pm ? 12 : 0;
    } else {
      out.hour = pm ? static_cast<uint8_t>(h12 + 12) : h12;
    }
  } else {
    out.hour = bcdToDec(hourReg & 0x3F);
  }

  out.day = bcdToDec(dayReg & 0x3F);
  out.month = bcdToDec(monthReg & 0x1F);
  out.year = static_cast<uint16_t>(2000 + bcdToDec(yearReg));

  return true;
}

bool isLeapYear(uint16_t year) {
  return (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
}

uint8_t daysInMonth(uint16_t year, uint8_t month) {
  static const uint8_t days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if (month < 1 || month > 12) {
    return 0;
  }
  if (month == 2 && isLeapYear(year)) {
    return 29;
  }
  return days[month - 1];
}

bool isValidDateTime(const RtcDateTime &dt) {
  if (dt.year < 2000 || dt.year > 2099) {
    return false;
  }
  if (dt.month < 1 || dt.month > 12) {
    return false;
  }
  const uint8_t maxDay = daysInMonth(dt.year, dt.month);
  if (dt.day < 1 || dt.day > maxDay) {
    return false;
  }
  if (dt.hour > 23 || dt.minute > 59 || dt.second > 59) {
    return false;
  }
  return true;
}

bool ds3231WriteTime(const RtcDateTime &dt) {
  if (!isValidDateTime(dt)) {
    return false;
  }

  Wire.beginTransmission(DS3231_ADDR);
  Wire.write(0x00);
  Wire.write(decToBcd(dt.second));
  Wire.write(decToBcd(dt.minute));
  Wire.write(decToBcd(dt.hour));  
  Wire.write(decToBcd(1));        
  Wire.write(decToBcd(dt.day));
  Wire.write(decToBcd(dt.month));
  Wire.write(decToBcd(static_cast<uint8_t>(dt.year - 2000)));
  return Wire.endTransmission() == 0;
}





// =========================================================================
//                9. ADS1115 Y CONVERSIONES DE VOLTAJE/RAW
// =========================================================================
bool ads1115WriteRegister(uint8_t reg, uint16_t value) {
  Wire.beginTransmission(ADS1115_ADDR);
  Wire.write(reg);
  Wire.write(static_cast<uint8_t>((value >> 8) & 0xFF));
  Wire.write(static_cast<uint8_t>(value & 0xFF));
  return Wire.endTransmission() == 0;
}

bool ads1115ReadRegister(uint8_t reg, uint16_t &value) {
  Wire.beginTransmission(ADS1115_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  if (Wire.requestFrom(static_cast<int>(ADS1115_ADDR), 2) != 2) {
    return false;
  }

  const uint8_t msb = Wire.read();
  const uint8_t lsb = Wire.read();
  value = static_cast<uint16_t>((msb << 8) | lsb);
  return true;
}

bool ads1115ReadRawSingleEnded(uint8_t channel, int16_t &raw) {
  if (channel > 3) {
    return false;
  }

  
  
  uint16_t pgaBits = 0x0200;  
  switch (ADS1115_GAIN) {
    case 0:
      pgaBits = 0x0000;
      break;
    case 1:
      pgaBits = 0x0200;
      break;
    case 2:
      pgaBits = 0x0400;
      break;
    case 3:
      pgaBits = 0x0600;
      break;
    case 4:
      pgaBits = 0x0800;
      break;
    case 5:
      pgaBits = 0x0A00;
      break;
    default:
      pgaBits = 0x0200;
      break;
  }

  
  const uint16_t muxBits = static_cast<uint16_t>((0x04 + channel) << 12);
  const uint16_t config = static_cast<uint16_t>(0x8000 | muxBits | pgaBits | 0x0100 | 0x0080 | 0x0003);
  if (!ads1115WriteRegister(0x01, config)) {
    return false;
  }

  delay(10);

  uint16_t conv = 0;
  if (!ads1115ReadRegister(0x00, conv)) {
    return false;
  }

  raw = static_cast<int16_t>(conv);
  return true;
}

float ads1115LsbVolts() {
  switch (ADS1115_GAIN) {
    case 0:
      return 0.0001875f;
    case 1:
      return 0.000125f;
    case 2:
      return 0.0000625f;
    case 3:
      return 0.00003125f;
    case 4:
      return 0.000015625f;
    case 5:
      return 0.0000078125f;
    default:
      return 0.000125f;
  }
}

bool readAdsVoltage(float &voltageV) {
  int32_t sum = 0;
  uint8_t okCount = 0;

  for (uint8_t i = 0; i < ADS1115_AVG_SAMPLES; i++) {
    int16_t raw = 0;
    if (ads1115ReadRawSingleEnded(ADS1115_CHANNEL, raw)) {
      sum += raw;
      okCount++;
    }
    delay(1);
  }

  if (okCount == 0) {
    voltageV = 0.0f;
    return false;
  }

  const float avgRaw = static_cast<float>(sum) / static_cast<float>(okCount);
  float v = avgRaw * ads1115LsbVolts();
  if (v < 0.0f) {
    v = 0.0f;
  }
  if (v > 4.5f) {
    v = 0.0f;
  }
  if (fabsf(v) < NOISE_THRESHOLD_V) {
    v = 0.0f;
  }

  voltageV = v;
  return true;
}

float normalizeVoltageToRefTemp(float voltage, float currentTemp, float refTemp = TEMP_REF_C, float coef = COEF_TEMP) {
  if (currentTemp < -40.0f || currentTemp > 125.0f) {
    currentTemp = refTemp;
  }

  if (fabsf(currentTemp - refTemp) < 0.1f) {
    return voltage;
  }

  const float denominator = 1.0f + coef * (currentTemp - refTemp);
  if (denominator > 0.0f) {
    return voltage / denominator;
  }

  return voltage;
}
// =========================================================================
//             10. CALIBRACION K (LABORATORIO Y VALORES CONOCIDOS)
// =========================================================================
float kFromLabRanges(float voltageAt25C) {
  const size_t n = sizeof(LAB_K_RANGES) / sizeof(LAB_K_RANGES[0]);
  if (n == 0) {
    return 1.0f;
  }

  for (size_t i = 0; i < n; i++) {
    if (voltageAt25C >= LAB_K_RANGES[i].minV && voltageAt25C <= LAB_K_RANGES[i].maxV) {
      return LAB_K_RANGES[i].k;
    }
  }

  if (voltageAt25C < LAB_K_RANGES[0].minV) {
    return LAB_K_RANGES[0].k;
  }
  return LAB_K_RANGES[n - 1].k;
}

float kFromKnownRanges(float voltageAt25C) {
  if (knownRangesCount == 0) {
    return 1.0f;
  }

  const auto clampK = [](float k) {
    if (k < KNOWN_K_MIN) {
      return KNOWN_K_MIN;
    }
    if (k > KNOWN_K_MAX) {
      return KNOWN_K_MAX;
    }
    return k;
  };

  for (uint16_t i = 0; i < knownRangesCount; i++) {
    if (voltageAt25C >= knownRanges[i].minV && voltageAt25C <= knownRanges[i].maxV) {
      return clampK(knownRanges[i].k);
    }
  }

  if (voltageAt25C < knownRanges[0].minV) {
    return clampK(knownRanges[0].k);
  }
  return clampK(knownRanges[knownRangesCount - 1].k);
}

void clearKnownRanges() {
  if (knownRanges != nullptr) {
    free(knownRanges);
    knownRanges = nullptr;
  }
  knownRangesCount = 0;
}

void clearKnownPoints() {
  if (knownPoints != nullptr) {
    free(knownPoints);
    knownPoints = nullptr;
  }
  knownPointsCount = 0;
}

void clearKnownCalibrationData() {
  clearKnownRanges();
  clearKnownPoints();
}

bool isKnownVoltageValid(float voltageAt25C) {
  return voltageAt25C >= KNOWN_CAL_MIN_V && voltageAt25C <= KNOWN_CAL_MAX_V;
}

float clampKnownK(float k) {
  if (k < KNOWN_K_MIN) {
    return KNOWN_K_MIN;
  }
  if (k > KNOWN_K_MAX) {
    return KNOWN_K_MAX;
  }
  return k;
}

void sortKnownPointsByVoltage(KnownCalPoint *points, uint16_t count) {
  if (points == nullptr || count < 2) {
    return;
  }

  for (uint16_t i = 0; i < count; i++) {
    for (uint16_t j = i + 1; j < count; j++) {
      if (points[j].v < points[i].v) {
        KnownCalPoint tmp = points[i];
        points[i] = points[j];
        points[j] = tmp;
      }
    }
  }
}

uint16_t compactKnownPointsByVoltage(KnownCalPoint *points, uint16_t count) {
  if (points == nullptr || count == 0) {
    return 0;
  }

  constexpr float MERGE_EPSILON_V = 0.0005f;
  uint16_t writeIdx = 1;

  for (uint16_t i = 1; i < count; i++) {
    if (fabsf(points[i].v - points[writeIdx - 1].v) <= MERGE_EPSILON_V) {
      points[writeIdx - 1] = points[i];
    } else {
      points[writeIdx] = points[i];
      writeIdx++;
    }
  }

  return writeIdx;
}

KRange *createRangesFromKnownPoints(const KnownCalPoint *points, uint16_t count) {
  if (points == nullptr || count == 0) {
    return nullptr;
  }

  KRange *newRanges = static_cast<KRange *>(malloc(sizeof(KRange) * count));
  if (newRanges == nullptr) {
    return nullptr;
  }

  // Crear rangos escalonados: cada punto conocido define el límite superior de su rango
  // El límite inferior es el voltaje del punto anterior (o 0 si es el primero)
  // El K permanece constante para todo el rango hasta el siguiente punto
  for (uint16_t i = 0; i < count; i++) {
    // minV: el voltaje del punto anterior (o 0 si es el primer rango)
    const float minV = (i == 0) ? 0.0f : points[i - 1].v;
    
    // maxV: el voltaje del punto actual (o 10.0 si es el último punto)
    const float maxV = points[i].v;
    
    // K: el factor de celda del punto actual, válido para todo este rango
    newRanges[i].minV = minV;
    newRanges[i].maxV = maxV;
    newRanges[i].k = clampKnownK(points[i].k);
  }

  return newRanges;
}

float representativeVoltageForRange(const KRange &range) {
  float lo = range.minV;
  float hi = range.maxV;
  if (lo > hi) {
    const float tmp = lo;
    lo = hi;
    hi = tmp;
  }

  const float clippedLo = fmaxf(lo, KNOWN_CAL_MIN_V);
  const float clippedHi = fminf(hi, KNOWN_CAL_MAX_V);
  if (clippedLo <= clippedHi) {
    return (clippedLo + clippedHi) * 0.5f;
  }

  float mid = (lo + hi) * 0.5f;
  if (mid < KNOWN_CAL_MIN_V) {
    mid = KNOWN_CAL_MIN_V;
  } else if (mid > KNOWN_CAL_MAX_V) {
    mid = KNOWN_CAL_MAX_V;
  }
  return mid;
}

bool rebuildKnownPointsFromRanges() {
  clearKnownPoints();
  if (knownRangesCount == 0 || knownRanges == nullptr) {
    return true;
  }

  KnownCalPoint *tmp = static_cast<KnownCalPoint *>(malloc(sizeof(KnownCalPoint) * knownRangesCount));
  if (tmp == nullptr) {
    return false;
  }

  for (uint16_t i = 0; i < knownRangesCount; i++) {
    tmp[i].v = representativeVoltageForRange(knownRanges[i]);
    tmp[i].k = clampKnownK(knownRanges[i].k);
  }

  sortKnownPointsByVoltage(tmp, knownRangesCount);
  uint16_t compactCount = compactKnownPointsByVoltage(tmp, knownRangesCount);
  if (compactCount == 0) {
    free(tmp);
    return true;
  }

  KnownCalPoint *shrunk = static_cast<KnownCalPoint *>(realloc(tmp, sizeof(KnownCalPoint) * compactCount));
  if (shrunk != nullptr) {
    tmp = shrunk;
  }

  knownPoints = tmp;
  knownPointsCount = compactCount;
  return true;
}
// =========================================================================
//            11. PERSISTENCIA DE CONFIGURACION EN NVS (PREFERENCES)
// =========================================================================
bool saveRuntimeConfigToNvs() {
  if (!prefs.begin("condcfg", false)) {
    return false;
  }

  bool ok = true;
  ok = prefs.putUChar("mpwake", samplesPerWake) > 0 && ok;
  ok = prefs.putUShort("ltewin", lteSendWindowMin) > 0 && ok;
  ok = prefs.putUChar("mode", static_cast<uint8_t>(calibrationMode)) > 0 && ok;
  ok = prefs.putUShort("kcnt", knownRangesCount) > 0 && ok;
  ok = prefs.putUShort("pcnt", knownPointsCount) > 0 && ok;

  if (knownRangesCount > 0 && knownRanges != nullptr) {
    const size_t blobBytes = static_cast<size_t>(knownRangesCount) * sizeof(KRange);
    ok = prefs.putBytes("kblob", knownRanges, blobBytes) == blobBytes && ok;
  } else {
    prefs.remove("kblob");
  }

  if (knownPointsCount > 0 && knownPoints != nullptr) {
    const size_t blobBytes = static_cast<size_t>(knownPointsCount) * sizeof(KnownCalPoint);
    ok = prefs.putBytes("pblob", knownPoints, blobBytes) == blobBytes && ok;
  } else {
    prefs.remove("pblob");
  }

  prefs.end();
  return ok;
}

bool loadRuntimeConfigFromNvs() {
  
  if (!prefs.begin("condcfg", false)) {
    return false;
  }

  const uint8_t savedSamplesPerWake = prefs.getUChar("mpwake", DEFAULT_SAMPLES_PER_WAKE);
  if (savedSamplesPerWake >= 1 && savedSamplesPerWake <= 120) {
    samplesPerWake = savedSamplesPerWake;
  }

  const uint16_t savedLteWindow = prefs.getUShort("ltewin", DEFAULT_LTE_SEND_WINDOW_MIN);
  if (savedLteWindow >= 1 && savedLteWindow <= 1440) {
    lteSendWindowMin = savedLteWindow;
  }

  const uint8_t savedMode = prefs.getUChar("mode", static_cast<uint8_t>(MODE_LABORATORY));

  const uint16_t savedPointsCount = prefs.getUShort("pcnt", 0);
  const size_t pointsBlobLen = prefs.getBytesLength("pblob");
  const uint16_t savedCount = prefs.getUShort("kcnt", 0);
  const size_t blobLen = prefs.getBytesLength("kblob");

  clearKnownCalibrationData();
  bool knownLoaded = false;

  if (savedPointsCount > 0 && pointsBlobLen >= static_cast<size_t>(savedPointsCount) * sizeof(KnownCalPoint)) {
    KnownCalPoint *tmpPoints = static_cast<KnownCalPoint *>(malloc(static_cast<size_t>(savedPointsCount) * sizeof(KnownCalPoint)));
    if (tmpPoints != nullptr) {
      const size_t expectedBytes = static_cast<size_t>(savedPointsCount) * sizeof(KnownCalPoint);
      const size_t readBytes = prefs.getBytes("pblob", tmpPoints, expectedBytes);
      if (readBytes == expectedBytes) {
        uint16_t validCount = 0;
        for (uint16_t i = 0; i < savedPointsCount; i++) {
          if (!isKnownVoltageValid(tmpPoints[i].v)) {
            continue;
          }
          tmpPoints[validCount].v = tmpPoints[i].v;
          tmpPoints[validCount].k = clampKnownK(tmpPoints[i].k);
          validCount++;
        }

        if (validCount > 0) {
          sortKnownPointsByVoltage(tmpPoints, validCount);
          validCount = compactKnownPointsByVoltage(tmpPoints, validCount);
          KRange *tmpRanges = createRangesFromKnownPoints(tmpPoints, validCount);
          if (tmpRanges != nullptr) {
            KnownCalPoint *shrunk = static_cast<KnownCalPoint *>(realloc(tmpPoints, sizeof(KnownCalPoint) * validCount));
            if (shrunk != nullptr) {
              tmpPoints = shrunk;
            }

            knownPoints = tmpPoints;
            knownPointsCount = validCount;
            knownRanges = tmpRanges;
            knownRangesCount = validCount;
            knownLoaded = true;
          }
        }
      }

      if (!knownLoaded) {
        free(tmpPoints);
      }
    }
  }

  if (!knownLoaded && savedCount > 0 && blobLen >= static_cast<size_t>(savedCount) * sizeof(KRange)) {
    KRange *tmp = static_cast<KRange *>(malloc(static_cast<size_t>(savedCount) * sizeof(KRange)));
    if (tmp != nullptr) {
      const size_t readBytes = prefs.getBytes("kblob", tmp, static_cast<size_t>(savedCount) * sizeof(KRange));
      if (readBytes == static_cast<size_t>(savedCount) * sizeof(KRange)) {
        knownRanges = tmp;
        knownRangesCount = savedCount;
        for (uint16_t i = 0; i < knownRangesCount; i++) {
          knownRanges[i].k = clampKnownK(knownRanges[i].k);
        }
        rebuildKnownPointsFromRanges();
      } else {
        free(tmp);
      }
    }
  }

  if (savedMode == static_cast<uint8_t>(MODE_KNOWN)) {
    calibrationMode = MODE_KNOWN;
  } else {
    calibrationMode = MODE_LABORATORY;
  }

  prefs.end();
  return true;
}

void resetBatchAccumulator() {
  sdBatchCount = 0;
  sdBatchTempValidCount = 0;
  sdBatchTempSum = 0.0f;
  sdBatchEcSum = 0.0f;

  lteBatchCount = 0;
  lteBatchTempValidCount = 0;
  lteBatchTempSum = 0.0f;
  lteBatchEcSum = 0.0f;
}





// =========================================================================
//          12. FUSION DE PUNTOS CONOCIDOS Y CALCULO FINAL DE EC
// =========================================================================
uint16_t appendKnownRangesFromPoints(const float *knownUs,
                                     const float *voltageAt25,
                                     uint16_t inputCount,
                                     uint16_t &acceptedRows,
                                     bool &memoryError) {
  acceptedRows = 0;
  memoryError = false;

  if (inputCount == 0 || knownUs == nullptr || voltageAt25 == nullptr) {
    return knownRangesCount;
  }

  if (knownPointsCount == 0 && knownRangesCount > 0 && !rebuildKnownPointsFromRanges()) {
    memoryError = true;
    return knownRangesCount;
  }

  KnownCalPoint *incoming = static_cast<KnownCalPoint *>(malloc(sizeof(KnownCalPoint) * inputCount));
  if (incoming == nullptr) {
    memoryError = true;
    return knownRangesCount;
  }

  for (uint16_t i = 0; i < inputCount; i++) {
    const float known = knownUs[i];
    const float v = voltageAt25[i];
    if (!(known > 0.0f) || !isKnownVoltageValid(v)) {
      continue;
    }

    const float ecPoly = POLY_A * v * v * v - POLY_B * v * v + POLY_C * v;
    if (ecPoly <= 0.0f) {
      continue;
    }

    const float k = known / ecPoly;
    if (!(k >= KNOWN_K_MIN && k <= KNOWN_K_MAX)) {
      continue;
    }

    incoming[acceptedRows].v = v;
    incoming[acceptedRows].k = k;
    acceptedRows++;
  }

  if (acceptedRows == 0) {
    free(incoming);
    return knownRangesCount;
  }

  const uint16_t mergedCapacity = knownPointsCount + acceptedRows;
  KnownCalPoint *merged = static_cast<KnownCalPoint *>(malloc(sizeof(KnownCalPoint) * mergedCapacity));
  if (merged == nullptr) {
    free(incoming);
    memoryError = true;
    return knownRangesCount;
  }

  uint16_t mergedCount = 0;
  for (uint16_t i = 0; i < knownPointsCount; i++) {
    if (!isKnownVoltageValid(knownPoints[i].v)) {
      continue;
    }
    merged[mergedCount].v = knownPoints[i].v;
    merged[mergedCount].k = clampKnownK(knownPoints[i].k);
    mergedCount++;
  }

  for (uint16_t i = 0; i < acceptedRows; i++) {
    merged[mergedCount++] = incoming[i];
  }
  free(incoming);

  if (mergedCount == 0) {
    free(merged);
    return knownRangesCount;
  }

  sortKnownPointsByVoltage(merged, mergedCount);
  mergedCount = compactKnownPointsByVoltage(merged, mergedCount);
  if (mergedCount == 0) {
    free(merged);
    return knownRangesCount;
  }

  KRange *newRanges = createRangesFromKnownPoints(merged, mergedCount);
  if (newRanges == nullptr) {
    free(merged);
    memoryError = true;
    return knownRangesCount;
  }

  KnownCalPoint *shrunk = static_cast<KnownCalPoint *>(realloc(merged, sizeof(KnownCalPoint) * mergedCount));
  if (shrunk != nullptr) {
    merged = shrunk;
  }

  clearKnownRanges();
  clearKnownPoints();
  knownPoints = merged;
  knownPointsCount = mergedCount;
  knownRanges = newRanges;
  knownRangesCount = mergedCount;
  return knownRangesCount;
}

float ecFromVoltageAndTemp(float voltage, float tempC) {
  if (voltage <= 0.0f) {
    return 0.0f;
  }

  const float voltageAt25C = normalizeVoltageToRefTemp(voltage, tempC, TEMP_REF_C, COEF_TEMP);
  float ecPoly = POLY_A * voltageAt25C * voltageAt25C * voltageAt25C -
                 POLY_B * voltageAt25C * voltageAt25C +
                 POLY_C * voltageAt25C;
  if (ecPoly < 0.0f) {
    ecPoly = 0.0f;
  }

  float k = (calibrationMode == MODE_KNOWN) ? kFromKnownRanges(voltageAt25C) : kFromLabRanges(voltageAt25C);
  float ecFinal = ecPoly * k;
  if (ecFinal > MAX_EC_US) {
    ecFinal = MAX_EC_US;
  }
  if (ecFinal < EC_NOISE_FLOOR_US) {
    return 0.0f;
  }
  return ecFinal;
}
// Declaracion adelantada para usar lectura de temperatura en handlers HTTP.
bool ds18x20ReadTemperatureC(float &tempC);

// =========================================================================
//             13. CONSTRUCCION DE PAGINAS HTML DEL PORTAL AP
// =========================================================================
String buildApPageHtml() {
  return R"HTML(
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Conductimetro</title>
  <style>
    body { margin: 0; font-family: "Segoe UI", Tahoma, sans-serif; background: radial-gradient(circle at 20% 20%, #dbeafe 0, #eff6ff 35%, #f8fafc 100%); color: #0f172a; }
    .wrap { max-width: 760px; margin: 5vh auto; padding: 20px; }
    .card { background: #ffffff; border-radius: 18px; padding: 24px; box-shadow: 0 14px 34px rgba(15, 23, 42, 0.12); border: 1px solid #e2e8f0; }
    h1 { margin: 0 0 8px; font-size: 1.7rem; letter-spacing: 0.4px; }
    p { margin: 0 0 18px; color: #334155; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .btn { display: block; width: 100%; border: 0; border-radius: 12px; padding: 16px; font-size: 1.03rem; font-weight: 600; cursor: pointer; transition: transform .08s ease, filter .15s ease; }
    .btn:hover { filter: brightness(1.03); }
    .btn:active { transform: translateY(1px); }
    .btn-conf { background: linear-gradient(135deg, #0284c7, #0369a1); color: #fff; }
    .btn-ext { background: linear-gradient(135deg, #f59e0b, #d97706); color: #111827; }
    .foot { margin-top: 16px; color: #64748b; font-size: .92rem; }
    @media (max-width: 640px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Panel Conductimetro</h1>
      <p>Seleccione el modulo que desea usar en modo punto de acceso.</p>
      <div class="grid">
        <form action="/configuracion" method="get">
          <button class="btn btn-conf" type="submit">Configuracion</button>
        </form>
        <form action="/extraccion" method="get">
          <button class="btn btn-ext" type="submit">Extraccion</button>
        </form>
      </div>
      <div class="foot">Para salir de modo AP, presione nuevamente el boton fisico (GPIO0).</div>
    </div>
  </div>
</body>
</html>
)HTML";
}

String buildExtractionPageHtml(const String &message) {
  String html;
  html.reserve(7000);
  html += "<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
  html += "<title>Extraccion</title><style>";
  html += "body{margin:0;font-family:Segoe UI,Tahoma,sans-serif;background:#f8fafc;color:#0f172a;}";
  html += ".wrap{max-width:900px;margin:24px auto;padding:16px;}";
  html += ".card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:20px;box-shadow:0 10px 26px rgba(15,23,42,.08);}";
  html += ".top{display:flex;align-items:center;gap:12px;margin-bottom:12px;}";
  html += ".back{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:10px;background:#e2e8f0;color:#0f172a;text-decoration:none;font-weight:700;}";
  html += "h1{margin:0;font-size:1.35rem;} p{margin:6px 0 12px;color:#475569;}";
  html += ".grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}";
  html += ".field{display:flex;flex-direction:column;gap:6px;}";
  html += "input{padding:10px;border:1px solid #cbd5e1;border-radius:10px;font-size:1rem;}";
  html += ".btn{margin-top:16px;background:#0ea5e9;color:#fff;border:0;padding:12px 16px;border-radius:10px;font-weight:600;cursor:pointer;}";
  html += ".state{display:inline-flex;align-items:center;gap:8px;margin:4px 0 12px;padding:8px 12px;border-radius:999px;background:#dcfce7;color:#166534;font-weight:600;}";
  html += ".msg{margin-top:10px;padding:10px;border-radius:10px;background:#eff6ff;color:#1e3a8a;}";
  html += "@media(max-width:760px){.grid{grid-template-columns:1fr;}}";
  html += "</style></head><body><div class='wrap'><div class='card'>";
  html += "<div class='top'><a class='back' href='/'>â†</a><h1>Extraccion de datos</h1></div>";
  html += "<p>Filtre por fecha y hora para descargar los datos de SD en formato TXT.</p>";
  html += "<div class='state'>SD: ";
  html += sdReady ? "detectada" : "no detectada";
  html += "</div>";
  html += "<form action='/extraccion/descargar' method='get'>";
  html += "<div class='grid'>";
  html += "<div class='field'><label>Fecha inicial</label><input type='date' name='fecha_ini' required></div>";
  html += "<div class='field'><label>Fecha final</label><input type='date' name='fecha_fin' required></div>";
  html += "<div class='field'><label>Hora inicial</label><input type='time' name='hora_ini' required></div>";
  html += "<div class='field'><label>Hora final</label><input type='time' name='hora_fin' required></div>";
  html += "</div>";
  html += "<button class='btn' type='submit'>Descargar datos filtrados</button>";
  html += "</form>";
  if (message.length() > 0) {
    html += "<div class='msg'>" + message + "</div>";
  }
  html += "</div></div></body></html>";
  return html;
}

String buildConfigPageHtml(const String &message) {
  const char *modeLabSel = (calibrationMode == MODE_LABORATORY) ? "selected" : "";
  const char *modeKnownSel = (calibrationMode == MODE_KNOWN) ? "selected" : "";
  const char *activeModeLabel = (calibrationMode == MODE_KNOWN) ? "Valores conocidos" : "Laboratorio";

  String html;
  html.reserve(20000);
  html += "<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
  html += "<title>Configuracion</title><style>";
  html += "body{margin:0;font-family:Segoe UI,Tahoma,sans-serif;background:#f8fafc;color:#0f172a;}";
  html += ".wrap{max-width:920px;margin:24px auto;padding:16px;}";
  html += ".card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:20px;box-shadow:0 10px 26px rgba(15,23,42,.08);}";
  html += ".top{display:flex;align-items:center;gap:12px;margin-bottom:12px;}";
  html += ".back{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:10px;background:#e2e8f0;color:#0f172a;text-decoration:none;font-weight:700;}";
  html += "h1{margin:0;font-size:1.35rem;} p{margin:6px 0 12px;color:#475569;}";
  html += ".grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}";
  html += ".field{display:flex;flex-direction:column;gap:6px;}";
  html += "input,select{padding:10px;border:1px solid #cbd5e1;border-radius:10px;font-size:1rem;}";
  html += "table{width:100%;border-collapse:collapse;margin-top:10px;}th,td{border-bottom:1px solid #e2e8f0;padding:8px;text-align:left;}";
  html += ".btn{margin-top:16px;background:#0ea5e9;color:#fff;border:0;padding:12px 16px;border-radius:10px;font-weight:600;cursor:pointer;}";
  html += ".btn.secondary{background:#e2e8f0;color:#0f172a;}";
  html += ".btn.warn{background:#f59e0b;color:#111827;}";
  html += ".btn.success{background:#22c55e;color:#052e16;}";
  html += ".rowtop{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-top:14px;}";
  html += ".known-section{display:none;margin-top:8px;padding:12px;border:1px dashed #cbd5e1;border-radius:12px;background:#f8fafc;}";
  html += ".measure-btn{background:#0284c7;color:#fff;border:0;border-radius:8px;padding:8px 10px;cursor:pointer;}";
  html += ".measure-btn:disabled{opacity:.55;cursor:not-allowed;}";
  html += ".inline{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}";
  html += ".state{display:inline-flex;align-items:center;gap:8px;margin:4px 0 12px;padding:8px 12px;border-radius:999px;background:#dbeafe;color:#1e3a8a;font-weight:600;}";
  html += ".msg{margin-top:10px;padding:10px;border-radius:10px;background:#eff6ff;color:#1e3a8a;}";
  html += ".small{font-size:.9rem;color:#64748b;}";
  html += "@media(max-width:760px){.grid{grid-template-columns:1fr;}}";
  html += "</style></head><body><div class='wrap'><div class='card'>";
  html += "<div class='top'><a class='back' href='/'>â†</a><h1>Configuracion</h1></div>";
  html += "<p>Ajuste muestras por minuto, ventana LTE y modo de calibracion.</p>";
  html += "<div class='state'>Modo activo en ESP32: ";
  html += activeModeLabel;
  html += "</div>";
  html += "<form id='config-form' action='/configuracion/guardar' method='post'>";
  html += "<div class='grid'>";
  html += "<div class='field'><label>Muestras por minuto</label>";
  html += "<input type='number' min='1' max='120' name='samples_per_wake' value='" + String(samplesPerWake) + "'></div>";
  html += "<div class='field'><label>Enviar LTE cada (min)</label>";
  html += "<input type='number' min='1' max='1440' name='lte_window_min' value='" + String(lteSendWindowMin) + "'></div>";
  html += "<div class='field'><label>Modo</label><select id='modo' name='modo'>";
  html += "<option value='laboratorio' "; html += modeLabSel; html += ">Laboratorio (default)</option>";
  html += "<option value='conocidos' "; html += modeKnownSel; html += ">Valores conocidos</option></select></div>";
  html += "</div>";
  html += "<div id='actions-lab' class='inline'>";
  html += "<button class='btn' type='submit'>Guardar cambio</button>";
  html += "</div>";
  html += "<div id='actions-known' class='inline' style='display:none;'>";
  html += "<button id='next-btn' class='btn success' type='button'>Siguiente</button>";
  html += "</div>";

  html += "<div id='known-section' class='known-section'>";
  html += "<div class='rowtop'>";
  html += "<button class='btn warn' type='submit' name='known_action' value='continue_without_data'>Continuar sin agregar datos</button>";
  html += "<button id='add-row-btn' class='btn secondary' type='button'>Agregar otro rango</button>";
  html += "</div>";
  html += "<p class='small'>Use Medir para capturar temperatura y conductividad final de cada fila. Se generan rangos con la cantidad de K ingresada y medida.</p>";
  html += "<input type='hidden' id='rows_count' name='rows_count' value='1'>";
  html += "<table><thead><tr><th>Valor conocido (uS)</th><th>Medir</th><th>Temperatura medida (C)</th><th>Conductividad final (uS)</th></tr></thead><tbody id='known-body'></tbody></table>";
  html += "<button class='btn' type='submit' name='known_action' value='save_known'>Guardar</button>";
  html += "</div>";
  html += "</form>";

  html += "<script>";
  html += "(function(){";
  html += "const modo=document.getElementById('modo');";
  html += "const actionsLab=document.getElementById('actions-lab');";
  html += "const actionsKnown=document.getElementById('actions-known');";
  html += "const knownSection=document.getElementById('known-section');";
  html += "const knownBody=document.getElementById('known-body');";
  html += "const rowsCount=document.getElementById('rows_count');";
  html += "const nextBtn=document.getElementById('next-btn');";
  html += "const addRowBtn=document.getElementById('add-row-btn');";
  html += "let rowIndex=0;";

  html += "function esc(v){return String(v===undefined?'':v).replace(/</g,'&lt;').replace(/>/g,'&gt;');}";

  html += "function addRow(){";
  html += "const idx=rowIndex++;";
  html += "const tr=document.createElement('tr');";
  html += "tr.setAttribute('data-row',String(idx));";
  html += "tr.innerHTML=";
  html += "'<td><input type=\"number\" step=\"0.1\" min=\"0\" name=\"known_'+idx+'\" placeholder=\"Ej: 1413\"></td>' +";
  html += "'<td><button class=\"measure-btn\" type=\"button\" data-measure=\"'+idx+'\">Medir</button><input type=\"hidden\" name=\"volt_'+idx+'\" id=\"volt_'+idx+'\" value=\"\"></td>' +";
  html += "'<td id=\"temp_'+idx+'\">--</td>' +";
  html += "'<td id=\"ec_'+idx+'\">--</td>';";
  html += "knownBody.appendChild(tr);";
  html += "rowsCount.value=String(rowIndex);";
  html += "const btn=tr.querySelector('[data-measure]');";
  html += "btn.addEventListener('click',function(){measureRow(idx,btn);});";
  html += "}";

  html += "function measureRow(idx,btn){";
  html += "btn.disabled=true;";
  html += "btn.textContent='Midiendo...';";
  html += "fetch('/configuracion/medir',{method:'POST'})";
  html += ".then(r=>r.json())";
  html += ".then(data=>{";
  html += "if(!data||!data.ok){throw new Error((data&&data.error)||'No se pudo medir');}";
  html += "document.getElementById('temp_'+idx).textContent=esc(data.temp_c);";
  html += "document.getElementById('ec_'+idx).textContent=esc(data.ec_us);";
  html += "document.getElementById('volt_'+idx).value=String(data.voltage_at_25||'');";
  html += "btn.textContent='Medir de nuevo';";
  html += "})";
  html += ".catch(err=>{alert('Error de medicion: '+err.message);btn.textContent='Medir';})";
  html += ".finally(()=>{btn.disabled=false;});";
  html += "}";

  html += "function syncMode(){";
  html += "const known=modo.value==='conocidos';";
  html += "actionsLab.style.display=known?'none':'flex';";
  html += "actionsKnown.style.display=known?'flex':'none';";
  html += "if(!known){knownSection.style.display='none';}";
  html += "}";

  html += "addRowBtn.addEventListener('click',addRow);";
  html += "nextBtn.addEventListener('click',function(){knownSection.style.display='block';if(knownBody.children.length===0){addRow();}});";
  html += "modo.addEventListener('change',syncMode);";
  html += "syncMode();";
  html += "})();";
  html += "</script>";

  if (message.length() > 0) {
    html += "<div class='msg'>" + message + "</div>";
  }
  html += "</div></div></body></html>";
  return html;
}

// =========================================================================
//            14. SERVIDOR WEB AP (RUTAS DE CONFIG Y EXTRACCION)
// =========================================================================
void setupApPortal() {
  if (apModeActive && WiFi.getMode() == WIFI_AP) {
    return;
  }

    // Limpieza defensiva para evitar doble inicializacion del stack de red.
  webServer.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_OFF);
  delay(80);

  WiFi.mode(WIFI_AP);
  if (!WiFi.softAP(AP_SSID, AP_PASSWORD)) {
    Serial.println("AP_WARN no se pudo iniciar softAP");
    return;
  }

  if (!apRoutesRegistered) {
    webServer.on("/", HTTP_GET, []() {
    webServer.send(200, "text/html; charset=utf-8", buildApPageHtml());
  });

    webServer.on("/configuracion", HTTP_GET, []() {
    webServer.send(200, "text/html; charset=utf-8", buildConfigPageHtml(""));
  });

    webServer.on("/configuracion/medir", HTTP_POST, []() {
    float tempC = NAN;
    bool tempOk = ds18x20ReadTemperatureC(tempC);
    const float tempForCalc = tempOk ? tempC : TEMP_REF_C;

    float voltage = 0.0f;
    readAdsVoltage(voltage);
    const float voltageAt25 = normalizeVoltageToRefTemp(voltage, tempForCalc, TEMP_REF_C, COEF_TEMP);
    const float ecUs = ecFromVoltageAndTemp(voltage, tempForCalc);

    String payload = "{";
    payload += "\"ok\":true,";
    payload += "\"temp_c\":";
    if (tempOk) {
      payload += String(tempC, 2);
    } else {
      payload += "null";
    }
    payload += ",\"ec_us\":" + String(ecUs, 1);
    payload += ",\"voltage_at_25\":" + String(voltageAt25, 6);
    payload += "}";

    webServer.send(200, "application/json; charset=utf-8", payload);
  });

    webServer.on("/configuracion/guardar", HTTP_POST, []() {
    String message;
    const CalibrationMode prevMode = calibrationMode;
    const uint16_t prevLteWindowMin = lteSendWindowMin;

    if (webServer.hasArg("samples_per_wake")) {
      const int parsed = webServer.arg("samples_per_wake").toInt();
      if (parsed >= 1 && parsed <= 120) {
        samplesPerWake = static_cast<uint8_t>(parsed);
      }
    }

    if (webServer.hasArg("lte_window_min")) {
      const int parsed = webServer.arg("lte_window_min").toInt();
      if (parsed >= 1 && parsed <= 1440) {
        lteSendWindowMin = static_cast<uint16_t>(parsed);
      }
    }

    const String modo = webServer.hasArg("modo") ? webServer.arg("modo") : "laboratorio";
    if (modo == "conocidos") {
      const String knownAction = webServer.hasArg("known_action") ? webServer.arg("known_action") : "save_known";

      if (knownAction == "continue_without_data") {
        calibrationMode = MODE_KNOWN;
        if (knownRangesCount > 0) {
          message = "Configuracion guardada. Modo conocidos activo usando rangos existentes (" + String(knownRangesCount) + ").";
        } else {
          message = "Modo conocidos activo sin rangos cargados. Agregue una fila medida para fijar K.";
        }
      } else {
        uint16_t rowsCount = 0;
        if (webServer.hasArg("rows_count")) {
          const int parsedRows = webServer.arg("rows_count").toInt();
          if (parsedRows > 0) {
            rowsCount = static_cast<uint16_t>(parsedRows);
          }
        }

        if (rowsCount == 0) {
          rowsCount = 1;
        }

        float *knownUs = static_cast<float *>(malloc(sizeof(float) * rowsCount));
        float *volts = static_cast<float *>(malloc(sizeof(float) * rowsCount));
        if (knownUs == nullptr || volts == nullptr) {
          if (knownUs != nullptr) {
            free(knownUs);
          }
          if (volts != nullptr) {
            free(volts);
          }
          webServer.send(200, "text/html; charset=utf-8", buildConfigPageHtml("Memoria insuficiente para procesar rangos."));
          return;
        }

        for (uint16_t i = 0; i < rowsCount; i++) {
          knownUs[i] = 0.0f;
          volts[i] = 0.0f;
          const String kName = "known_" + String(i);
          const String vName = "volt_" + String(i);
          if (webServer.hasArg(kName)) {
            knownUs[i] = webServer.arg(kName).toFloat();
          }
          if (webServer.hasArg(vName)) {
            volts[i] = webServer.arg(vName).toFloat();
          }
        }

        uint16_t acceptedRows = 0;
        bool memoryError = false;
        const uint16_t totalRanges = appendKnownRangesFromPoints(knownUs, volts, rowsCount, acceptedRows, memoryError);
        free(knownUs);
        free(volts);

        calibrationMode = MODE_KNOWN;

        if (memoryError) {
          message = "Memoria insuficiente para actualizar rangos conocidos. Se conservaron " + String(totalRanges) + " rango(s) previos.";
        } else if (acceptedRows > 0) {
          message = "Configuracion guardada. Modo conocidos activo con " + String(totalRanges) + " rango(s) acumulados (" + String(acceptedRows) + " fila(s) nueva(s) valida(s)).";
        } else {
          message = "No se detectaron filas nuevas validas. Se mantienen " + String(totalRanges) + " rango(s) existentes en modo conocidos.";
        }
      }
    } else {
      calibrationMode = MODE_LABORATORY;
      clearKnownCalibrationData();
      message = "Configuracion guardada. Modo laboratorio activo.";
    }

    const bool modeChanged = (prevMode != calibrationMode);
    if (modeChanged) {
      // Si cambio el modo, reinicia acumuladores para no mezclar promedios.
      resetBatchAccumulator();
      forceLteSendRequested = false;
      message += " Se reiniciaron acumuladores por cambio de modo.";
    } else {
      // Si solo cambian ventanas, conserva acumulados para no retrasar envios.
      if (sdBatchTempValidCount > sdBatchCount) {
        sdBatchTempValidCount = sdBatchCount;
      }
      if (lteBatchTempValidCount > lteBatchCount) {
        lteBatchTempValidCount = lteBatchCount;
      }

      if (prevLteWindowMin != lteSendWindowMin && lteBatchCount > 0) {
        forceLteSendRequested = true;
        message += " Envio LTE forzado en la siguiente medicion.";
      }

      message += " Ventanas aplicadas sin reiniciar acumuladores.";
    }

    const bool persisted = saveRuntimeConfigToNvs();
    Serial.printf("CONFIG aplicada | modo=%s | muestras=%u | sd=fijo 1 min | lte=%u min | known_ranges=%u | sd_batch=%u | lte_batch=%u | lte_force=%s | nvs=%s\n",
                  calibrationModeToString(calibrationMode),
                  static_cast<unsigned>(samplesPerWake),
                  static_cast<unsigned>(lteSendWindowMin),
                  static_cast<unsigned>(knownRangesCount),
                  static_cast<unsigned>(sdBatchCount),
                  static_cast<unsigned>(lteBatchCount),
                  forceLteSendRequested ? "yes" : "no",
                  persisted ? "ok" : "error");

    if (modeChanged) {
      queueModeChangeNotification(calibrationMode);
      RtcDateTime now = {};
      if (!ds3231ReadTime(now)) {
        now = RTC_BOOT_DT;
        Serial.println("RTC_WARN lectura fallida al enviar cambio de modo, usando RTC_BOOT_DT");
      }

      Serial.println("LTE_INFO envio inmediato por cambio de modo");
      modemPowerOn();
      if (publishMqttModeChange(now, calibrationMode)) {
        pendingModeChangeNotification = false;
        message += " Cambio de modo enviado por LTE.";
      } else {
        message += " Cambio de modo pendiente de envio LTE.";
      }
    }

    if (!persisted) {
      message += " (Aviso: no se pudo guardar en memoria flash)";
    }
    webServer.send(200, "text/html; charset=utf-8", buildConfigPageHtml(message));
  });

    webServer.on("/extraccion", HTTP_GET, []() {
    webServer.send(200, "text/html; charset=utf-8", buildExtractionPageHtml(""));
  });

    webServer.on("/extraccion/descargar", HTTP_GET, []() {
    if (!initSdCard()) {
      webServer.send(200, "text/html; charset=utf-8", buildExtractionPageHtml("No se pudo inicializar la SD."));
      return;
    }

    if (!SD.exists(SD_LOG_PATH)) {
      webServer.send(200, "text/html; charset=utf-8", buildExtractionPageHtml("No existe archivo de datos en SD."));
      return;
    }

    if (!webServer.hasArg("fecha_ini") || !webServer.hasArg("fecha_fin") ||
        !webServer.hasArg("hora_ini") || !webServer.hasArg("hora_fin")) {
      webServer.send(200, "text/html; charset=utf-8", buildExtractionPageHtml("Complete todos los filtros de fecha/hora."));
      return;
    }

    RtcDateTime startDt = {};
    RtcDateTime endDt = {};
    if (!parseDateTimeFromStrings(webServer.arg("fecha_ini"), webServer.arg("hora_ini"), startDt) ||
        !parseDateTimeFromStrings(webServer.arg("fecha_fin"), webServer.arg("hora_fin"), endDt)) {
      webServer.send(200, "text/html; charset=utf-8", buildExtractionPageHtml("Formato de fecha/hora invalido."));
      return;
    }

    const uint32_t startTs = dateTimeToComparable(startDt);
    const uint32_t endTs = dateTimeToComparable(endDt);
    if (startTs > endTs) {
      webServer.send(200, "text/html; charset=utf-8", buildExtractionPageHtml("El rango es invalido: inicio mayor que fin."));
      return;
    }

    File file = SD.open(SD_LOG_PATH, FILE_READ);
    if (!file) {
      webServer.send(200, "text/html; charset=utf-8", buildExtractionPageHtml("No se pudo abrir el archivo de datos."));
      return;
    }

    webServer.setContentLength(CONTENT_LENGTH_UNKNOWN);
    webServer.sendHeader("Content-Disposition", "attachment; filename=datos_filtrados.txt");
    webServer.send(200, "text/plain; charset=utf-8", "");

    webServer.sendContent("# FechaHora Temperatura Conductividad\n");
    webServer.sendContent("# Filtro: ");
    webServer.sendContent(webServer.arg("fecha_ini") + " " + webServer.arg("hora_ini") + " -> " + webServer.arg("fecha_fin") + " " + webServer.arg("hora_fin") + "\n");

    while (file.available()) {
      String line = file.readStringUntil('\n');
      line.trim();
      if (line.length() == 0) {
        continue;
      }

      RtcDateTime rowDt = {};
      if (!parseTimestampFromLogLine(line, rowDt)) {
        continue;
      }

      const uint32_t rowTs = dateTimeToComparable(rowDt);
      if (rowTs >= startTs && rowTs <= endTs) {
        webServer.sendContent(line + "\n");
      }
    }

    file.close();
    webServer.sendContent("");
    });

    apRoutesRegistered = true;
  }

  webServer.begin();
  apModeActive = true;
  apModeStartMs = millis();

  IPAddress apIp = WiFi.softAPIP();
  Serial.printf("AP iniciado | SSID: %s | IP: %u.%u.%u.%u\n",
                AP_SSID,
                apIp[0], apIp[1], apIp[2], apIp[3]);
}




void stopApPortal() {
  webServer.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_OFF);
  apModeActive = false;
  apModeStartMs = 0;
  Serial.println("AP detenido");
}





void onewireDriveLow() {
  pinMode(oneWireDataPin, OUTPUT_OPEN_DRAIN);
  digitalWrite(oneWireDataPin, LOW);
}

void onewireRelease() {
  pinMode(oneWireDataPin, OUTPUT_OPEN_DRAIN);
  digitalWrite(oneWireDataPin, HIGH);
}

void onewireStrongPullupHold(uint16_t ms) {
  
  pinMode(oneWireDataPin, OUTPUT);
  digitalWrite(oneWireDataPin, HIGH);
  delay(ms);
  onewireRelease();
}

bool onewireReset() {
  onewireDriveLow();
  delayMicroseconds(480);
  noInterrupts();
  onewireRelease();
  delayMicroseconds(60);
  const bool present = digitalRead(oneWireDataPin) == LOW;
  interrupts();
  delayMicroseconds(420);
  return present;
}

void onewireWriteBit(uint8_t bit) {
  noInterrupts();
  onewireDriveLow();
  
  if (bit) {
    onewireRelease();
  } else {
    onewireDriveLow();
  }
  delayMicroseconds(60);
  onewireRelease();
  interrupts();
}

uint8_t onewireReadBit() {
  uint8_t bit;
  
  onewireRelease();
  noInterrupts();
  onewireDriveLow();
  onewireRelease();
  delayMicroseconds(5);
  bit = static_cast<uint8_t>(digitalRead(ONEWIRE_PIN));
  interrupts();
  delayMicroseconds(40);
  return bit;
}

void onewireWriteByte(uint8_t value) {
  for (uint8_t i = 0; i < 8; i++) {
    onewireWriteBit(value & 0x01);
    value >>= 1;
  }
}

uint8_t onewireReadByte() {
  uint8_t value = 0;
  for (uint8_t i = 0; i < 8; i++) {
    value |= static_cast<uint8_t>(onewireReadBit() << i);
  }
  return value;
}

uint8_t ds18x20Crc8(const uint8_t *data, uint8_t len) {
  uint8_t crc = 0;
  for (uint8_t i = 0; i < len; i++) {
    uint8_t inByte = data[i];
    for (uint8_t j = 0; j < 8; j++) {
      const uint8_t mix = (crc ^ inByte) & 0x01;
      crc >>= 1;
      if (mix) {
        crc ^= 0x8C;
      }
      inByte >>= 1;
    }
  }
  return crc;
}

bool ds18x20StartConversion() {
  if (!onewireReset()) {
    return false;
  }
  onewireWriteByte(0xCC);  
  onewireWriteByte(0x44);  
  return true;
}

bool ds18x20ReadRom(uint8_t *rom8) {
  if (!onewireReset()) {
    return false;
  }

  onewireWriteByte(0x33);  
  for (uint8_t i = 0; i < 8; i++) {
    rom8[i] = onewireReadByte();
  }

  return ds18x20Crc8(rom8, 7) == rom8[7];
}

bool ds18x20ReadScratchpad(uint8_t *scratch9) {
  if (!onewireReset()) {
    return false;
  }
  onewireWriteByte(0xCC);  
  onewireWriteByte(0xBE);  

  for (uint8_t i = 0; i < 9; i++) {
    scratch9[i] = onewireReadByte();
  }

  const uint8_t crc = ds18x20Crc8(scratch9, 8);
  lastScratchpadCrcCalc = crc;
  lastScratchpadValid = (crc == scratch9[8]);
  for (uint8_t i = 0; i < 9; i++) {
    lastScratchpad[i] = scratch9[i];
  }
  return lastScratchpadValid;
}

bool ds18x20ReadTemperatureC(float &tempC) {
  
  for (uint8_t attempt = 0; attempt < 3; attempt++) {
    if (!ds18x20StartConversion()) {
      delay(20);
      continue;
    }

    delay(1000);

    uint8_t scratch[9] = {0};
    if (!ds18x20ReadScratchpad(scratch)) {
      delay(20);
      continue;
    }

    const int16_t raw = static_cast<int16_t>((scratch[1] << 8) | scratch[0]);
    tempC = static_cast<float>(raw) / 16.0f;
    if (tempC < -55.0f || tempC > 125.0f || isnan(tempC)) {
      delay(20);
      continue;
    }

    return true;
  }

  return false;
}





bool acquireAveragedMinuteMeasurement(RtcDateTime &now, float &tempC, bool &tempOk, float &ecUs, float &voltageV) {
  if (!ds3231ReadTime(now)) {
    now = RTC_BOOT_DT;
    Serial.println("RTC_WARN lectura fallida en adquisicion, usando RTC_BOOT_DT");
  }

  tempC = NAN;
  tempOk = ds18x20ReadTemperatureC(tempC);
  const float tempForCalc = tempOk ? tempC : TEMP_REF_C;

  float sumVoltage = 0.0f;
  float sumEc = 0.0f;
  uint8_t validCount = 0;

  const uint8_t iterations = (samplesPerWake < 1) ? 1 : samplesPerWake;
  for (uint8_t i = 0; i < iterations; i++) {
    float localVoltage = 0.0f;
    if (readAdsVoltage(localVoltage)) {
      const float localEc = ecFromVoltageAndTemp(localVoltage, tempForCalc);
      sumVoltage += localVoltage;
      sumEc += localEc;
      validCount++;
    }
    // Tomar 30 muestras rápidas sin descanso, luego entra en deep sleep
    delay(4);
  }

  if (validCount == 0) {
    voltageV = 0.0f;
    ecUs = 0.0f;
    return true;
  }

  voltageV = sumVoltage / static_cast<float>(validCount);
  ecUs = sumEc / static_cast<float>(validCount);
  return true;
}

void printSingleSample(const RtcDateTime &now, float tempC, bool tempOk, float ecUs) {
  if (tempOk) {
    Serial.printf("%04u-%02u-%02u %02u:%02u:%02u %.2f %.1f\n",
                  now.year, now.month, now.day,
                  now.hour, now.minute, now.second,
                  tempC,
                  ecUs);
  } else {
    Serial.printf("%04u-%02u-%02u %02u:%02u:%02u NaN %.1f\n",
                  now.year, now.month, now.day,
                  now.hour, now.minute, now.second,
                  ecUs);
  }
}

void processPreparedMinuteSample(const RtcDateTime &now, float tempC, bool tempOk, float ecUs) {
  tryFlushPendingModeChange(now);

  printSingleSample(now, tempC, tempOk, ecUs);

  if (!appendMeasurementToSd(now, tempC, tempOk, ecUs)) {
    Serial.println("SD_WARN no se pudo guardar medicion en /mediciones.txt");
  }

  lteBatchCount++;

  Serial.printf("BATCH_INFO sd=%u/%u lte=%u/%u\n",
                1U,
                static_cast<unsigned>(DEFAULT_SD_SAVE_WINDOW_MIN),
                static_cast<unsigned>(lteBatchCount),
                static_cast<unsigned>(lteSendWindowMin));

  const bool shouldTryLteSend = forceLteSendRequested || (lteBatchCount >= lteSendWindowMin);
  if (shouldTryLteSend) {
    if (forceLteSendRequested) {
      Serial.println("LTE_INFO envio forzado por cambio de ventana");
    }

    if (publishMqttAverage(now, tempC, tempOk, ecUs)) {
      lteBatchCount = 0;
      forceLteSendRequested = false;
      Serial.println("4G_INFO medicion enviada por MQTT exitosamente");
    } else {
      Serial.println("4G_WARN no se pudo enviar medicion por MQTT, reintentando en siguiente ciclo");
      forceLteSendRequested = true;
    }
  }
}

void processOneMinuteSample() {
  // Registrar el timestamp del inicio de este ciclo de procesamiento
  lastProcessTimeMs = millis();
  
  RtcDateTime now = {};
  float tempC = NAN;
  bool tempOk = false;
  float ecUs = 0.0f;
  float voltageV = 0.0f;
  if (!acquireAveragedMinuteMeasurement(now, tempC, tempOk, ecUs, voltageV)) {
    return;
  }
  processPreparedMinuteSample(now, tempC, tempOk, ecUs);
}
// =========================================================================
//             17. CICLO DE PORTAL AP Y SALIDA POR BOTON/TIMEOUT
// =========================================================================
bool runApPortalUntilTimeout() {
  setupApPortal();
  if (WiFi.getMode() != WIFI_AP) {
    apModeActive = false;
    apModeStartMs = 0;
    Serial.println("AP_WARN no se pudo mantener AP activo");
    return false;
  }

  // 1) Recuperar GPIO0 del dominio RTC.
  rtc_gpio_deinit(GPIO_NUM_0);
  delay(100);
  
  // 2) Configurar GPIO0 con pull-up rtc_io.
  rtc_gpio_pullup_en(GPIO_NUM_0);
  rtc_gpio_pulldown_dis(GPIO_NUM_0);
  delay(100);

  // 3) Esperar tiempo FIJO para que el usuario suelte el botón.
  Serial.println("AP_INFO esperando liberacion del boton de entrada...");
  delay(2000);

  // 4) ESTABILIZACION: esperar a que GPIO0 este HIGH y permanezca por 1000ms.
  uint32_t stab_start = millis();
  uint32_t lastHighMs = 0;
  while (millis() - stab_start < 3000) {
    const int g0 = rtc_gpio_get_level(GPIO_NUM_0);
    if (g0 == HIGH) {
      if (lastHighMs == 0) {
        lastHighMs = millis();
      } else if (millis() - lastHighMs >= 1000) {
        break;  // Estable en HIGH durante 1000ms
      }
    } else {
      lastHighMs = 0;
    }
    delay(10);
  }

  Serial.println("AP_INFO boton estabilizado | esperando pulsacion...");
  
  // 5) ESPERA ADICIONAL de 1 segundo sin monitoria.
  delay(1000);

  // 6) Capturar estado REAL actual después de completa estabilización.
  int8_t gpio0_state = (rtc_gpio_get_level(GPIO_NUM_0) == HIGH) ? 0 : 1;
  uint32_t gpio0_debounceStart = millis();

  while (apModeActive) {
    modemHandlePowerWindow();
    webServer.handleClient();

    const uint32_t nowMs = millis();

    // Leer GPIO0.
    const int gpio0_level = rtc_gpio_get_level(GPIO_NUM_0);
    
    // Estado machine para GPIO0: detecta cambio de HIGH a LOW.
    if (gpio0_state == 0 && gpio0_level == LOW) {
      // Flanco HIGH -> LOW detectado
      gpio0_debounceStart = nowMs;
      gpio0_state = 1;
    } else if (gpio0_state == 1 && gpio0_level == HIGH) {
      // Volvio a HIGH, reset
      gpio0_state = 0;
    } else if (gpio0_state == 1 && (nowMs - gpio0_debounceStart) >= 50) {
      // Presion sostenida por 50ms -> salir
      Serial.println("AP_INFO pulsacion GPIO0 detectada, saliendo");
      stopApPortal();
      return true;
    }

    // Timeout.
    if (nowMs - apModeStartMs >= AP_AUTO_EXIT_MS) {
      Serial.println("AP_WARN timeout automatico");
      stopApPortal();
      break;
    }

    delay(20);
  }

  return false;
}
// =========================================================================
//               18. ENTRADA A DEEP SLEEP Y RETENCION DE PINES
// =========================================================================
void enterDeepSleep() {
  if (apModeActive) {
    stopApPortal();
  }

  modemPowerOff();

  if (MODEM_POWER_PIN_ALWAYS_HIGH) {
    pinMode(PIN_MODEM_POWER, OUTPUT);
    digitalWrite(PIN_MODEM_POWER, HIGH);
    rtc_gpio_hold_en(static_cast<gpio_num_t>(PIN_MODEM_POWER));
  }

  if (MODEM_USE_PWRKEY_BOOT) {
    pinMode(PIN_MODEM_PWRKEY, OUTPUT);
    digitalWrite(PIN_MODEM_PWRKEY, modemPwrKeyIdleLevel);
    rtc_gpio_hold_en(static_cast<gpio_num_t>(PIN_MODEM_PWRKEY));
  }

  WiFi.mode(WIFI_OFF);

  // ===== CÁLCULO ADAPTATIVO DEL TIEMPO DE DORMIR =====
  // Para evitar acumulación exponencial, calculamos cuánto tiempo REAL pasó
  // desde el inicio del procesamiento y dormimos solo lo necesario
  // para completar exactamente el intervalo configurado.
  
  uint32_t timeSinceLastProcessMs = 0;
  if (lastProcessTimeMs > 0) {
    uint32_t nowMs = millis();
    // Proteger contra overflow de millis()
    if (nowMs >= lastProcessTimeMs) {
      timeSinceLastProcessMs = nowMs - lastProcessTimeMs;
    }
  }
  
  // Calcular cuánto tiempo falta dormir para completar el intervalo
  int32_t sleepMs = static_cast<int32_t>(sampleIntervalMs) - static_cast<int32_t>(timeSinceLastProcessMs);
  
  // Asegurar que el tiempo de sueño sea positivo (mínimo 5 segundos de seguridad)
  if (sleepMs < 5000) {
    Serial.printf("SLEEP_WARN procesamiento tomó %lu ms (intervalo=%u ms). Ajustando a mínimo 5s\n",
                  static_cast<unsigned long>(timeSinceLastProcessMs),
                  static_cast<unsigned>(sampleIntervalMs));
    sleepMs = 5000;
  }
  
  Serial.printf("SLEEP_DEBUG procesado=%lu ms, dormir=%ld ms, total ciclo=%u ms\n",
                static_cast<unsigned long>(timeSinceLastProcessMs),
                static_cast<long>(sleepMs),
                static_cast<unsigned>(sampleIntervalMs));
  
  const uint64_t timerWakeUs = static_cast<uint64_t>(sleepMs) * 1000ULL;
  esp_sleep_enable_timer_wakeup(timerWakeUs);
  esp_sleep_enable_ext1_wakeup(1ULL << 0, ESP_EXT1_WAKEUP_ALL_LOW);

  Serial.printf("SLEEP_INFO entrando en deep sleep | timer=%llu us | ext1=GPIO0 LOW\n",
                static_cast<unsigned long long>(timerWakeUs));
  Serial.flush();
  delay(50);
  esp_deep_sleep_start();
}

// =========================================================================
//             19. UTILIDAD RTC (HORA BASE POR COMPILACION)
// =========================================================================
bool ds3231SetCompileTime() {
  const char *months = "JanFebMarAprMayJunJulAugSepOctNovDec";
  const char *date = __DATE__;
  const char *time = __TIME__;

  char monthStr[4] = {date[0], date[1], date[2], '\0'};
  int month = 1;
  const char *found = strstr(months, monthStr);
  if (found != nullptr) {
    month = static_cast<int>((found - months) / 3) + 1;
  }

  int day = atoi(date + 4);
  int year = atoi(date + 7);
  int hour = atoi(time);
  int minute = atoi(time + 3);
  int second = atoi(time + 6);

  Wire.beginTransmission(DS3231_ADDR);
  Wire.write(0x00);
  Wire.write(decToBcd(static_cast<uint8_t>(second)));
  Wire.write(decToBcd(static_cast<uint8_t>(minute)));
  Wire.write(decToBcd(static_cast<uint8_t>(hour)));  
  Wire.write(decToBcd(1));                           
  Wire.write(decToBcd(static_cast<uint8_t>(day)));
  Wire.write(decToBcd(static_cast<uint8_t>(month)));
  Wire.write(decToBcd(static_cast<uint8_t>(year - 2000)));
  return Wire.endTransmission() == 0;
}
}

// =========================================================================
//                      20. CICLO PRINCIPAL ARDUINO
// =========================================================================
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(300);

    // Mantener WiFi apagado en medicion reduce interferencia sobre OneWire.
  WiFi.mode(WIFI_OFF);

  apModeActive = false;

  rtc_gpio_hold_dis(static_cast<gpio_num_t>(PIN_MODEM_POWER));
  if (MODEM_USE_PWRKEY_BOOT) {
    rtc_gpio_hold_dis(static_cast<gpio_num_t>(PIN_MODEM_PWRKEY));
    pinMode(PIN_MODEM_PWRKEY, OUTPUT);
    digitalWrite(PIN_MODEM_PWRKEY, modemPwrKeyIdleLevel);
  }
  pinMode(PIN_MODEM_POWER, OUTPUT);
  digitalWrite(PIN_MODEM_POWER, MODEM_POWER_PIN_ALWAYS_HIGH ? HIGH : LOW);
  modemPowerActive = MODEM_POWER_PIN_ALWAYS_HIGH;
  modemSerialReady = false;
  modemActiveUntilMs = 0;
  if (MODEM_POWER_PIN_ALWAYS_HIGH) {
    Serial.println("LTE_INFO pin 12 en HIGH permanente para modem siempre energizado");
  } else {
    Serial.println("LTE_INFO modem inicia apagado y solo se encendera durante envio");
  }

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  if (initSdCard()) {
    Serial.println("SD_OK inicializada");
    if (!ensureSdLogFileReady()) {
      Serial.println("SD_WARN SD inicializada pero archivo de log no listo");
    }
  } else {
    Serial.println("SD_FAIL no se pudo inicializar");
  }
  oneWireDataPin = ONEWIRE_PIN;
  Serial.printf("OW pin fijo=%u\n", oneWireDataPin);
  onewireRelease();

  bool rtcAvailable = true;
  if (!ds3231IsPresent()) {
    Serial.println("[RTC] WARN: No se detecta DS3231 en I2C. Se continua sin bloqueo.");
    rtcAvailable = false;
  }

  RtcDateTime now = {};
  if (rtcAvailable && !ds3231ReadTime(now)) {
    Serial.println("[RTC] WARN: No se pudo leer la hora. Se continua con fallback.");
    rtcAvailable = false;
  }

  if (!rtcAvailable) {
    now = RTC_BOOT_DT;
  } else if (RTC_FORCE_SET_ON_BOOT) {
    if (!ds3231WriteTime(RTC_BOOT_DT)) {
      Serial.println("[RTC] ERROR: no se pudo forzar hora de arranque.");
    }
    ds3231ReadTime(now);
  } else if (now.year < VALID_TIME_MIN_YEAR || now.year > VALID_TIME_MAX_YEAR || now.month == 0 || now.month > 12 || now.day == 0 || now.day > 31) {
    Serial.println("[RTC] Aviso: hora no valida. Ajustando con hora de compilacion.");
    if (!ds3231SetCompileTime()) {
      Serial.println("[RTC] ERROR: no se pudo ajustar hora inicial.");
    }
    ds3231ReadTime(now);
  }

  float tempBoot = NAN;
  if (ds18x20ReadTemperatureC(tempBoot)) {
    Serial.printf("DS18_OK %.2fC\n", tempBoot);
  } else {
    Serial.println("DS18_FAIL startup");
  }

  const bool nvsLoaded = loadRuntimeConfigFromNvs();
  Serial.printf("CONFIG cargada | nvs=%s | modo=%s | muestras=%u | sd=fijo 1 min | lte=%u min | known_ranges=%u\n",
                nvsLoaded ? "ok" : "error",
                calibrationModeToString(calibrationMode),
                static_cast<unsigned>(samplesPerWake),
                static_cast<unsigned>(lteSendWindowMin),
                static_cast<unsigned>(knownRangesCount));

  const esp_sleep_wakeup_cause_t wakeupCause = esp_sleep_get_wakeup_cause();
  Serial.printf("WAKE_INFO causa=%d\n", static_cast<int>(wakeupCause));

  if (wakeupCause == ESP_SLEEP_WAKEUP_EXT1) {
    Serial.println("WAKE_INFO EXT1 GPIO0 LOW -> modo AP");
    apModeActive = true;
    const bool measureAfterAp = runApPortalUntilTimeout();
    if (measureAfterAp) {
      Serial.println("AP_INFO salida por boton -> ejecutando medicion inmediata");
      processOneMinuteSample();
    }
  } else {
    if (wakeupCause == ESP_SLEEP_WAKEUP_TIMER) {
      Serial.println("WAKE_INFO timer -> ciclo de medicion");
    } else {
      Serial.println("WAKE_INFO arranque normal -> ciclo de medicion");
    }
    processOneMinuteSample();
  }

  enterDeepSleep();
}

void loop() {
  delay(1000);
} 