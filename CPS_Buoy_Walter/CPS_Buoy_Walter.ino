/**
 * CPS_Buoy_Walter.ino
 * ─────────────────────────────────────────────────────────────────────────────
 * Water-quality monitoring buoy — Walter (ESP32-S3 + Sequans GM02SP modem)
 *
 * Sensors  : pH  → ADC GPIO1  (analogue)
 *            EC  → ADC GPIO2  (analogue)
 *            DO  → ADC GPIO4  (analogue)
 * Storage  : DFRobot microSD module → SPI (MOSI/MISO/SCK/SS)
 * RTC      : SD2405 → I2C (SDA/SCL)
 * Modem    : Walter built-in GM02SP → LTE-M + GNSS
 *
 * Every cycle the sketch:
 *   1. Reads RTC timestamp
 *   2. Reads the three water-quality sensors (10-sample average)
 *   3. Logs data to SD card (CSV)
 *   4. Obtains a GNSS fix via the Walter modem
 *   5. Connects to LTE (Soracom)
 *   6. Publishes all data to ThingsBoard via MQTT
 *   7. Disconnects and sleeps before repeating
 * ─────────────────────────────────────────────────────────────────────────────
 */

#include <WalterModem.h>
#include <esp_mac.h>
#include <inttypes.h>
#include <SPI.h>
#include <SD.h>
#include <Wire.h>
#include <MPU9250.h>
#include <time.h>

// Timezone rule (shifted +1 h from real Europe/Brussels):
//   winter = UTC+2, summer = UTC+3, DST = last Sun Mar 02:00 → last Sun Oct 03:00
#define TZ_BELGIUM "CET-3CEST,M3.5.0/2,M10.5.0/3"


// --- I2C busses 
// RTC
#define RTC_SDA 39
#define RTC_SCL 38

// IMU
#define IMU_SDA 4
#define IMU_SCL 5

TwoWire I2CBusIMU = TwoWire(0);
TwoWire I2CBusRTC = TwoWire(1);


// ─── ThingsBoard / MQTT ───────────────────────────────────────────────────────
#define TB_HOST       "mqtt.eu.thingsboard.cloud"
#define TB_PORT       1883
#define TB_TOPIC      "v1/devices/me/telemetry"
#define TB_TOKEN      "wvJVgNAp0eHuXr0hBeEu"
#define TB_CLIENT_ID  "admin"
#define TB_USERNAME   "admin"
#define TB_PASSWORD   "admin"

// ─── Cellular ────────────────────────────────────────────────────────────────
#define APN           "soracom.io"
#define RADIO_TECH    WALTER_MODEM_RAT_LTEM

// -- IMU 
MPU9250 IMU(I2CBusIMU, 0x68);
int status = -1;


// ─── Sensor ADC pins ─────────────────────────────────────────────────────────
#define PH_PIN  12   // GPIO9 → pH sensor
#define EC_PIN  11   // GPIO8 → EC sensor
#define DO_PIN  13   // GPI15 → DO sensor

// ─── SD card ─────────────────────────────────────────────────────────────────

#define SD_SCK_PIN  7
#define SD_MISO_PIN 10
#define SD_MOSI_PIN 9
#define SD_CS_PIN   8   // SS pin of DFRobot microSD module
#define LOG_FILENAME  "/datalog.csv"

// ─── SD2405 RTC ──────────────────────────────────────────────────────────────
// Walter I2C: SDA=GPIO8, SCL=GPIO9 (hardware I2C)
#define SD2405_ADDR   0x32  // Fixed I2C address of SD2405

// ─── Sensor calibration ──────────────────────────────────────────────────────
const float PH_VOLT_NEUTRAL  = 1527.0f;  // mV at pH 7
const float PH_VOLT_ACID     = 2050.0f;  // mV at pH 4
const float EC_K_VALUE       = 6.44f;    // EC probe cell constant
const float DO_VOLT_SAT      = 1500.0f;  // mV at DO saturation
const float DO_SAT_MG        = 8.24f;    // mg/L DO at saturation

// ─── Timing ──────────────────────────────────────────────────────────────────
#define CYCLE_DELAY_MS      2000   // Delay between full cycles (ms) 
#define MAX_GNSS_CONFIDENCE 100.0f  // Confidence threshold for a valid fix
#define GNSS_MAX_ATTEMPTS   3       // Fix attempts per cycle

// ─── RTC time struct (defined here so all functions can use it) ──────────────
struct RtcTime { uint16_t year; uint8_t month, day, hour, minute, second; };

// ─── Globals ─────────────────────────────────────────────────────────────────
WalterModem modem;

volatile bool  gnssFix_received   = false;
bool           gnssAssist_received = false;
WMGNSSFixEvent latestFix = {};

int  publishCounter = 0;
bool sdReady        = false;
bool rtcReady       = false;

// Last-known LTE info, carried across cycles so SD logging never depends on
// LTE actually being up this cycle.
char  lastLteOp[32] = "";
int   lastLteBand   = -1;
float lastLteRsrp   = 0.0f;

// RTC sync: resync at boot and then once per N cycles.
uint32_t lastRtcSyncEpoch = 0;          // last UTC epoch we synced from
#define RTC_RESYNC_INTERVAL_SEC  (6 * 60 * 60)   // re-sync every 6 h

// ═════════════════════════════════════════════════════════════════════════════
// SECTION 1 — SD2405 RTC
// ═════════════════════════════════════════════════════════════════════════════

uint8_t rtcReadReg(uint8_t reg) {
  I2CBusRTC.beginTransmission(SD2405_ADDR);
  I2CBusRTC.write(reg);
  I2CBusRTC.endTransmission(false);
  I2CBusRTC.requestFrom(SD2405_ADDR, (uint8_t)1);
  return I2CBusRTC.available() ? I2CBusRTC.read() : 0;
}

void rtcWriteReg(uint8_t reg, uint8_t val) {
  // SD2405 requires write-enable sequence before modifying time registers
  I2CBusRTC.beginTransmission(SD2405_ADDR);
  I2CBusRTC.write(0x10); I2CBusRTC.write(0x80); // WRTC1 enable
  I2CBusRTC.endTransmission();
  I2CBusRTC.beginTransmission(SD2405_ADDR);
  I2CBusRTC.write(0x0F); I2CBusRTC.write(0x84); // WRTC2 + WRTC3 enable
  I2CBusRTC.endTransmission();

  I2CBusRTC.beginTransmission(SD2405_ADDR);
  I2CBusRTC.write(reg);
  I2CBusRTC.write(val);
  I2CBusRTC.endTransmission();

  // Clear write-enable
  I2CBusRTC.beginTransmission(SD2405_ADDR);
  I2CBusRTC.write(0x0F); I2CBusRTC.write(0x00);
  I2CBusRTC.endTransmission();
  I2CBusRTC.beginTransmission(SD2405_ADDR);
  I2CBusRTC.write(0x10); I2CBusRTC.write(0x00);
  I2CBusRTC.endTransmission();
}

uint8_t bcd2dec(uint8_t b) { return (b >> 4) * 10 + (b & 0x0F); }
uint8_t dec2bcd(uint8_t d) { return ((d / 10) << 4) | (d % 10); }

bool rtcGetTime(RtcTime &t) {
  if (!rtcReady) return false;
  t.second = bcd2dec(rtcReadReg(0x00) & 0x7F);
  t.minute = bcd2dec(rtcReadReg(0x01) & 0x7F);
  t.hour   = bcd2dec(rtcReadReg(0x02) & 0x3F);  // 24h mode
  t.day    = bcd2dec(rtcReadReg(0x04) & 0x3F);
  t.month  = bcd2dec(rtcReadReg(0x05) & 0x1F);
  t.year   = 2000 + bcd2dec(rtcReadReg(0x06));
  return true;
}

/** Call once to set the RTC time, then comment out. */
void rtcSetTime(uint16_t year, uint8_t month, uint8_t day,
                uint8_t hour, uint8_t minute, uint8_t second) {
  rtcWriteReg(0x00, dec2bcd(second));
  rtcWriteReg(0x01, dec2bcd(minute));
  rtcWriteReg(0x02, dec2bcd(hour));
  rtcWriteReg(0x04, dec2bcd(day));
  rtcWriteReg(0x05, dec2bcd(month));
  rtcWriteReg(0x06, dec2bcd(year - 2000));
}

void rtcFormat(const RtcTime &t, char *buf) {
  snprintf(buf, 20, "%04d-%02d-%02d %02d:%02d:%02d",
           t.year, t.month, t.day, t.hour, t.minute, t.second);
}

/**
 * Pulls UTC epoch from the Walter modem (GNSS or LTE-derived) and writes it
 * to the SD2405 RTC after converting to Belgium local time (CET/CEST, with
 * automatic DST via the POSIX TZ string).
 *
 * Returns true on success. Safe to call repeatedly — only updates the RTC
 * when the modem reports a sane epoch.
 */
bool rtcSyncFromModem(bool force = false) {
  if (!rtcReady) return false;

  WalterModemRsp rsp = {};
  if (!modem.gnssGetUTCTime(&rsp)) return false;
  int64_t epoch = rsp.data.clock.epochTime;
  if (epoch < 1700000000LL) return false;   // not a real time yet

  // Skip if we synced very recently (and caller didn't force a resync)
  if (!force && lastRtcSyncEpoch &&
      (uint32_t)(epoch - lastRtcSyncEpoch) < RTC_RESYNC_INTERVAL_SEC) {
    return true;
  }

  // Convert UTC epoch → Belgium local broken-down time
  setenv("TZ", TZ_BELGIUM, 1);
  tzset();
  time_t   t = (time_t)epoch;
  struct tm lt;
  localtime_r(&t, &lt);

  rtcSetTime((uint16_t)(lt.tm_year + 1900),
             (uint8_t)(lt.tm_mon + 1),
             (uint8_t)lt.tm_mday,
             (uint8_t)lt.tm_hour,
             (uint8_t)lt.tm_min,
             (uint8_t)lt.tm_sec);

  lastRtcSyncEpoch = (uint32_t)epoch;
  Serial.printf("[RTC] Synced to Belgium time %04d-%02d-%02d %02d:%02d:%02d (%s)\r\n",
                lt.tm_year + 1900, lt.tm_mon + 1, lt.tm_mday,
                lt.tm_hour, lt.tm_min, lt.tm_sec,
                lt.tm_isdst > 0 ? "CEST" : "CET");
  return true;
}

// ═════════════════════════════════════════════════════════════════════════════
// SECTION 2 — SD CARD LOGGING
// ═════════════════════════════════════════════════════════════════════════════

void sdInitFile() {
  if (!sdReady) return;
  if (!SD.exists(LOG_FILENAME)) {
    File f = SD.open(LOG_FILENAME, FILE_WRITE);
    if (f) {
      f.println(
        "cycle,timestamp,"
        "pH,EC_mS_cm,DO_mg_L,"
        "chip_temp_C,"
        "gps_fix,lat,lon,gps_speed_n,gps_speed_s,gps_confidence,gps_sats,gps_sats_good,"
        "imu_ok,ax_mss,ay_mss,az_mss,gx_rads,gy_rads,gz_rads,mx_uT,my_uT,mz_uT,"
        "lte_operator,lte_band,lte_rsrp_dBm"
      );
      f.close();
    }
    Serial.println("[SD] Created " LOG_FILENAME " with header");
  }
}

/**
 * Logs one complete cycle row. Any field that is unavailable (no GPS fix,
 * IMU init failed, no LTE info, etc.) is left blank in the CSV.
 */
void sdLogAll(int cycle, const char *timestamp,
              float ph, float ec, float do_val,
              float chipTemp,
              bool hasGps,
              float lat, float lon,
              float gpsSpeedN, float gpsSpeedE,
              float gpsConfidence, int gpsSats, int gpsSatsGood,
              bool imuOk,
              float ax, float ay, float az,
              float gx, float gy, float gz,
              float mx, float my, float mz,
              const char *lteOperator, int lteBand, float lteRsrp) {
  if (!sdReady) return;
  File f = SD.open(LOG_FILENAME, FILE_APPEND);
  if (!f) { Serial.println("[SD] Could not open log file"); return; }

  // cycle, timestamp
  f.print(cycle);                                 f.print(",");
  f.print(timestamp);                             f.print(",");

  // water-quality sensors
  f.print(ph, 2);                                 f.print(",");
  f.print(ec, 2);                                 f.print(",");
  f.print(do_val, 2);                             f.print(",");

  // chip temperature
  f.print(chipTemp, 2);                           f.print(",");

  // GPS
  f.print(hasGps ? 1 : 0);                        f.print(",");
  if (hasGps) {
    f.print(lat, 6);          f.print(",");
    f.print(lon, 6);          f.print(",");
    f.print(gpsSpeedN, 4);    f.print(",");
    f.print(gpsSpeedE, 4);    f.print(",");
    f.print(gpsConfidence, 2);f.print(",");
    f.print(gpsSats);         f.print(",");
    f.print(gpsSatsGood);     f.print(",");
  } else {
    f.print(",,,,,,,");
  }

  // IMU
  f.print(imuOk ? 1 : 0);                         f.print(",");
  if (imuOk) {
    f.print(ax, 4); f.print(",");
    f.print(ay, 4); f.print(",");
    f.print(az, 4); f.print(",");
    f.print(gx, 4); f.print(",");
    f.print(gy, 4); f.print(",");
    f.print(gz, 4); f.print(",");
    f.print(mx, 4); f.print(",");
    f.print(my, 4); f.print(",");
    f.print(mz, 4); f.print(",");
  } else {
    f.print(",,,,,,,,,");
  }

  // LTE info
  if (lteOperator && lteOperator[0]) f.print(lteOperator);
  f.print(",");
  if (lteBand >= 0) f.print(lteBand);
  f.print(",");
  f.println(lteRsrp, 1);

  f.close();
  Serial.printf("[SD] Row logged (cycle %d)\r\n", cycle);
}

// ═════════════════════════════════════════════════════════════════════════════
// SECTION 3 — SENSOR READING
// ═════════════════════════════════════════════════════════════════════════════

float analogAverage(int pin) {
  long sum = 0;
  for (int i = 0; i < 10; i++) { sum += analogRead(pin); delay(10); }
  return sum / 10.0f;
}

void readSensors(float &ph, float &ec, float &do_val) {
  const float ADC_REF_MV = 3300.0f;
  const float ADC_MAX    = 4095.0f;

  float ph_mv = analogAverage(PH_PIN) * (ADC_REF_MV / ADC_MAX);
  float ec_mv = analogAverage(EC_PIN) * (ADC_REF_MV / ADC_MAX);
  float do_mv = analogAverage(DO_PIN) * (ADC_REF_MV / ADC_MAX);

  ph     = ((ph_mv - PH_VOLT_NEUTRAL) * ((7.0f - 4.0f) / (PH_VOLT_NEUTRAL - PH_VOLT_ACID))) + 7.0f;
  ec     = (ec_mv / 1000.0f) * EC_K_VALUE;
  do_val = (do_mv / DO_VOLT_SAT) * DO_SAT_MG;

  Serial.printf("[Sensors] pH=%.2f  EC=%.2f mS/cm  DO=%.2f mg/L\r\n", ph, ec, do_val);
}

// ═════════════════════════════════════════════════════════════════════════════
// SECTION 4 — LTE CONNECTIVITY
// ═════════════════════════════════════════════════════════════════════════════

bool lteConnected() {
  WalterModemNetworkRegState s = modem.getNetworkRegState();
  return (s == WALTER_MODEM_NETWORK_REG_REGISTERED_HOME ||
          s == WALTER_MODEM_NETWORK_REG_REGISTERED_ROAMING);
}

bool waitForNetwork(int timeout_sec = 120) {
  Serial.print("[LTE] Waiting for network");
  for (int i = 0; i < timeout_sec; i++) {
    if (lteConnected()) { Serial.println(" connected!"); return true; }
    Serial.print("."); delay(1000);
  }
  Serial.println(" TIMEOUT");
  return false;
}

bool lteConnect() {
  if (lteConnected()) return true;
  if (!modem.setOpState(WALTER_MODEM_OPSTATE_NO_RF))                           { Serial.println("[LTE] Error: NO_RF");        return false; }
  if (!modem.definePDPContext(1, APN))                                          { Serial.println("[LTE] Error: PDP context");  return false; }
  if (!modem.setOpState(WALTER_MODEM_OPSTATE_FULL))                            { Serial.println("[LTE] Error: FULL");         return false; }
  if (!modem.setNetworkSelectionMode(WALTER_MODEM_NETWORK_SEL_MODE_AUTOMATIC)) { Serial.println("[LTE] Error: net selection"); return false; }
  return waitForNetwork();
}

bool lteDisconnect() {
  if (!modem.setOpState(WALTER_MODEM_OPSTATE_MINIMUM)) { Serial.println("[LTE] Error: MINIMUM"); return false; }
  while (modem.getNetworkRegState() != WALTER_MODEM_NETWORK_REG_NOT_SEARCHING) delay(100);
  Serial.println("[LTE] Disconnected");
  return true;
}

static void onNetworkEvent(WMNetworkEventType event, const WMNetworkEventData* data, void*) {
  if (event != WALTER_MODEM_NETWORK_EVENT_REG_STATE_CHANGE) return;
  switch (data->cereg.state) {
    case WALTER_MODEM_NETWORK_REG_REGISTERED_HOME:    Serial.println("[LTE] Registered (home)");    break;
    case WALTER_MODEM_NETWORK_REG_REGISTERED_ROAMING: Serial.println("[LTE] Registered (roaming)"); break;
    case WALTER_MODEM_NETWORK_REG_SEARCHING:          Serial.println("[LTE] Searching...");          break;
    case WALTER_MODEM_NETWORK_REG_DENIED:             Serial.println("[LTE] Registration denied");   break;
    default: break;
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// IMU 
// ═════════════════════════════════════════════════════════════════════════════

void get_imu_data(char *buffer,size_t maxLen,const char *timestamp){
  // If sensor failed in setup(), return an empty JSON object
  if (status < 0) {
    snprintf(buffer, maxLen, "{}");
    return;
  }

  //Read from the sensor 
  IMU.readSensor();

  //Construct the imu batch at dt 
  snprintf(buffer, maxLen,
             "{\"timestamp\":\"%s\",\"ax\":%.4f,\"ay\":%.4f,\"gz\":%.4f,"
             "\"mx\":%.4f,\"my\":%.4f}",
             timestamp,
             IMU.getAccelX_mss(), 
             IMU.getAccelY_mss(),
             IMU.getGyroZ_rads(),
             IMU.getMagX_uT(),
             IMU.getMagY_uT());

}


// ═════════════════════════════════════════════════════════════════════════════
// SECTION 5 — GNSS / GPS
// ═════════════════════════════════════════════════════════════════════════════

void onGnssEvent(WMGNSSEventType type, const WMGNSSEventData* data, void*) {
  switch (type) {
    case WALTER_MODEM_GNSS_EVENT_FIX: {
      memcpy(&latestFix, &data->gnssfix, sizeof(WMGNSSFixEvent));
      int goodSats = 0;
      for (int i = 0; i < latestFix.satCount; i++)
        if (latestFix.sats[i].signalStrength >= 30) goodSats++;
      Serial.printf("[GNSS] Fix: lat=%.6f lon=%.6f confidence=%.2f sats=%d (good:%d)\r\n",
                    latestFix.latitude, latestFix.longitude,
                    latestFix.estimatedConfidence, latestFix.satCount, goodSats);
      gnssFix_received = true;
      break;
    }
    case WALTER_MODEM_GNSS_EVENT_ASSISTANCE:
      Serial.println("[GNSS] Assistance data updated");
      gnssAssist_received = true;
      break;
    default: break;
  }
}

bool syncClock() {
  WalterModemRsp rsp = {};
  modem.gnssGetUTCTime(&rsp);
  if (rsp.data.clock.epochTime > 4) return true;
  Serial.println("[GNSS] Clock invalid - syncing via LTE");
  if (!lteConnected() && !lteConnect()) return false;
  for (int i = 0; i < 5; i++) {
    modem.gnssGetUTCTime(&rsp);
    if (rsp.data.clock.epochTime > 4) {
      Serial.printf("[GNSS] Clock synced: %" PRIi64 "\r\n", rsp.data.clock.epochTime);
      return true;
    }
    delay(2000);
  }
  Serial.println("[GNSS] Clock sync failed");
  return false;
}

bool updateAssistance() {
  WalterModemRsp rsp = {};
  if (!modem.gnssGetAssistanceStatus(&rsp) ||
      rsp.type != WALTER_MODEM_RSP_DATA_TYPE_GNSS_ASSISTANCE_DATA) {
    Serial.println("[GNSS] Could not read assistance status");
    return false;
  }
  const WMGNSSAssistance& alm = rsp.data.gnssAssistance[WALTER_MODEM_GNSS_ASSISTANCE_TYPE_ALMANAC];
  const WMGNSSAssistance& eph = rsp.data.gnssAssistance[WALTER_MODEM_GNSS_ASSISTANCE_TYPE_REALTIME_EPHEMERIS];
  bool needAlm = (!alm.available || alm.timeToUpdate <= 0);
  bool needEph = (!eph.available || eph.timeToUpdate <= 0);
  if (!needAlm && !needEph) { Serial.println("[GNSS] Assistance current"); return true; }
  if (!lteConnected() && !lteConnect()) return false;
  if (needAlm) {
    gnssAssist_received = false;
    if (!modem.gnssUpdateAssistance(WALTER_MODEM_GNSS_ASSISTANCE_TYPE_ALMANAC)) return false;
    while (!gnssAssist_received) delay(200);
    Serial.println("[GNSS] Almanac updated");
  }
  if (needEph) {
    gnssAssist_received = false;
    if (!modem.gnssUpdateAssistance(WALTER_MODEM_GNSS_ASSISTANCE_TYPE_REALTIME_EPHEMERIS)) return false;
    while (!gnssAssist_received) delay(200);
    Serial.println("[GNSS] Ephemeris updated");
  }
  return true;
}

bool getGnssFix(int maxAttempts = GNSS_MAX_ATTEMPTS) {
  if (!syncClock()) { Serial.println("[GNSS] Skipping - clock invalid"); return false; }
  updateAssistance();
  if (lteConnected()) lteDisconnect();
  for (int attempt = 1; attempt <= maxAttempts; attempt++) {
    gnssFix_received = false;
    if (!modem.gnssPerformAction()) { Serial.println("[GNSS] Error starting fix"); return false; }
    Serial.printf("[GNSS] Fix attempt %d/%d ...", attempt, maxAttempts);
    while (!gnssFix_received) { Serial.print("."); delay(500); }
    Serial.println();
    if (latestFix.estimatedConfidence <= MAX_GNSS_CONFIDENCE) {
      Serial.println("[GNSS] Valid fix obtained"); return true;
    }
    Serial.printf("[GNSS] Confidence %.2f too low, retrying\r\n", latestFix.estimatedConfidence);
  }
  Serial.println("[GNSS] Could not obtain valid fix");
  return false;
}

// ═════════════════════════════════════════════════════════════════════════════
// SECTION 6 — THINGSBOARD MQTT PUBLISH
// ═════════════════════════════════════════════════════════════════════════════

bool publishToThingsBoard(const char *timestamp, float ph, float ec,
                          float do_val, bool hasGps) {
  if (!modem.mqttConfig(TB_CLIENT_ID, TB_USERNAME, TB_PASSWORD)) { Serial.println("[MQTT] Config failed"); return false; }
  if (!modem.mqttConnect(TB_HOST, TB_PORT))           { Serial.println("[MQTT] Connect failed"); return false; }

  Serial.println("[MQTT] Connected to broker");  // ← add this
  delay(1000);
  float chipTemp = temperatureRead();


  // --- 1. PUBLISH SENSORS & GPS ---
  char gpsDict[128] = "null"; 
  if (hasGps) {
    snprintf(gpsDict, sizeof(gpsDict),
             "{\"lat\":%.6f,\"lon\":%.6f,\"speed_n\":%.4f,\"speed_s\":%.4f}",
             (float)latestFix.latitude, (float)latestFix.longitude,
             (float)latestFix.northSpeed, (float)latestFix.eastSpeed);
  }

  char payload1[256]; 
  snprintf(payload1, sizeof(payload1),
           "{\"timestamp\":\"%s\",\"temp\":%.2f,\"ph\":%.2f,\"ec\":%.2f,\"do\":%.2f,\"counter\":%d,\"gps\":%s}",
           timestamp, chipTemp, ph, ec, do_val, publishCounter, gpsDict);

  Serial.printf("[MQTT] Pub 1: %s\r\n", payload1);
  bool ok1 = modem.mqttPublish(TB_TOPIC, (uint8_t*)payload1, strlen(payload1), 1);
  delay(300); // Give modem a moment to clear the buffer

  // --- 1b. PUBLISH GPS AS SEPARATE FLAT VALUES ---
  bool ok1b = true;
  if (hasGps) {
    char payloadGps[256];
    snprintf(payloadGps, sizeof(payloadGps),
             "{\"lat\":%.6f,\"lon\":%.6f,\"speed_n\":%.4f,\"speed_s\":%.4f}",
             (float)latestFix.latitude, (float)latestFix.longitude,
             (float)latestFix.northSpeed, (float)latestFix.eastSpeed);
    Serial.printf("[MQTT] Pub 1b (gps flat): %s\r\n", payloadGps);
    ok1b = modem.mqttPublish(TB_TOPIC, (uint8_t*)payloadGps, strlen(payloadGps), 1);
    delay(300);
  }


  char imuDict[256];
  get_imu_data(imuDict, sizeof(imuDict), timestamp);

  bool ok2 = true;
  // Only publish IMU if the string is longer than "{}"
  if (strlen(imuDict) > 3) {
    char payload2[300];
    snprintf(payload2, sizeof(payload2), "{\"imu\":%s}", imuDict);
    Serial.printf("[MQTT] Pub 2: %s\r\n", payload2);
    ok2 = modem.mqttPublish(TB_TOPIC, (uint8_t*)payload2, strlen(payload2), 1);
  }

  // --- 2b. PUBLISH IMU AS SEPARATE FLAT VALUES ---
  bool ok2b = true;
  if (status >= 0) {
    IMU.readSensor();
    char payloadImu[256];
    snprintf(payloadImu, sizeof(payloadImu),
             "{\"ax\":%.4f,\"ay\":%.4f,\"gz\":%.4f,\"mx\":%.4f,\"my\":%.4f}",
             IMU.getAccelX_mss(), IMU.getAccelY_mss(),
             IMU.getGyroZ_rads(),
             IMU.getMagX_uT(),  IMU.getMagY_uT());
    Serial.printf("[MQTT] Pub 2b (imu flat): %s\r\n", payloadImu);
    delay(300);
    ok2b = modem.mqttPublish(TB_TOPIC, (uint8_t*)payloadImu, strlen(payloadImu), 1);
  }

  Serial.println((ok1 && ok1b && ok2 && ok2b) ? "[MQTT] Publish succeeded" : "[MQTT] Publish failed");
  publishCounter++;
  delay(500);
  modem.mqttDisconnect();
  return (ok1 && ok2);
}

// ═════════════════════════════════════════════════════════════════════════════
// SECTION 7 — SETUP
// ═════════════════════════════════════════════════════════════════════════════

void setup() {
  delay(3000);
  Serial.begin(115200);
  delay(500);

  Serial.println("\r\n========================================");
  Serial.println("  CPS Buoy - Walter Edition");
  Serial.println("========================================\r\n");

  pinMode(SD_CS_PIN, OUTPUT);
  digitalWrite(SD_CS_PIN, HIGH);
  delay(10);
  SPI.begin(SD_SCK_PIN, SD_MISO_PIN, SD_MOSI_PIN);
  I2CBusRTC.begin(RTC_SDA, RTC_SCL, 100000); // 100kHz
  I2CBusIMU.begin(IMU_SDA, IMU_SCL, 400000); // 400kHz is standard for MPU9250
  Serial.print("[IMU] Initializing...");
  status = IMU.begin();
  if (status < 0) {
    Serial.println(" FAILED. Check wiring or change address to 0x69");
  } else {
    Serial.println(" OK");
  }

  I2CBusRTC.beginTransmission(SD2405_ADDR);
  if (I2CBusRTC.endTransmission() == 0) {
    rtcReady = true;
    Serial.println("[RTC] SD2405 found");
    // Uncomment once to set the time, then comment out again:
    // rtcSetTime(2025, 6, 1, 12, 0, 0);
  } else {
    Serial.println("[RTC] SD2405 not found - timestamps will be empty");
  }

  for (int i = 1; i <= 3 && !sdReady; i++) {
    if (SD.begin(SD_CS_PIN, SPI, 4000000)) {
      sdReady = true;
      Serial.printf("[SD] Card ready (attempt %d)\r\n", i);
    } else {
      Serial.printf("[SD] begin failed, attempt %d/3\r\n", i);
      SD.end();
      delay(500);
    }
  }
  if (sdReady) {
    uint8_t  ct = SD.cardType();
    uint64_t sz = SD.cardSize() / (1024ULL * 1024ULL);
    Serial.printf("[SD] Card type=%u size=%llu MB\r\n", ct, sz);
    sdInitFile();
  } else {
    Serial.println("[SD] Card not found - logging disabled");
  }

  // Configure Belgium timezone for any time conversions in this firmware
  setenv("TZ", TZ_BELGIUM, 1);
  tzset();
  // Walter modem
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  Serial.printf("[SYS] MAC: %02X:%02X:%02X:%02X:%02X:%02X\r\n",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

  if (!modem.begin(&Serial2)) {
    Serial.println("[SYS] FATAL: Modem init failed - restarting");
    delay(3000); ESP.restart();
  }
  Serial.println("[SYS] Modem initialised");

  modem.setNetworkEventHandler(onNetworkEvent, nullptr);
  modem.setGNSSEventHandler(onGnssEvent, nullptr);

  WalterModemRsp rsp = {};
  if (modem.getRAT(&rsp) && rsp.data.rat != RADIO_TECH) {
    modem.setRAT(RADIO_TECH);
    Serial.println("[SYS] Radio set to LTE-M");
  }
  if (modem.getIdentity(&rsp))  Serial.printf("[SYS] IMEI:  %s\r\n", rsp.data.identity.imei);
  if (modem.getSIMCardID(&rsp)) Serial.printf("[SYS] ICCID: %s\r\n", rsp.data.simCardID.iccid);

  if (!modem.gnssConfig()) {
    Serial.println("[SYS] FATAL: GNSS config failed - restarting");
    delay(3000); ESP.restart();
  }
  Serial.println("[SYS] GNSS configured");
  Serial.println("\r\n[SYS] Setup complete - entering main loop\r\n");
}

// ═════════════════════════════════════════════════════════════════════════════
// SECTION 8 — MAIN LOOP
// ═════════════════════════════════════════════════════════════════════════════

void loop() {
  Serial.println("----------- New Cycle -----------");

  // 1. Get timestamp from RTC (Belgium local time)
  char timestamp[20] = "unknown";
  RtcTime t;
  if (rtcGetTime(t)) rtcFormat(t, timestamp);
  Serial.printf("[RTC] %s\r\n", timestamp);

  // Read water-quality sensors
  float ph, ec, do_val;
  readSensors(ph, ec, do_val);

  // Chip temperature
  float chipTemp = temperatureRead();

  // Read IMU (full 9-axis)
  bool  imuOk = (status >= 0);
  float ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0, mx = 0, my = 0, mz = 0;
  if (imuOk) {
    IMU.readSensor();
    ax = IMU.getAccelX_mss(); ay = IMU.getAccelY_mss(); az = IMU.getAccelZ_mss();
    gx = IMU.getGyroX_rads(); gy = IMU.getGyroY_rads(); gz = IMU.getGyroZ_rads();
    mx = IMU.getMagX_uT();    my = IMU.getMagY_uT();    mz = IMU.getMagZ_uT();
    Serial.printf("[IMU] a=(%.2f,%.2f,%.2f) g=(%.2f,%.2f,%.2f) m=(%.1f,%.1f,%.1f)\r\n",
                  ax, ay, az, gx, gy, gz, mx, my, mz);
  }

  // Get GPS fix
  bool hasGps = getGnssFix();
  //bool hasGps = false;
  float lat = hasGps ? (float)latestFix.latitude  : 0.0f;
  float lon = hasGps ? (float)latestFix.longitude : 0.0f;
  float gpsSpeedN  = hasGps ? (float)latestFix.northSpeed         : 0.0f;
  float gpsSpeedE  = hasGps ? (float)latestFix.eastSpeed          : 0.0f;
  float gpsConf    = hasGps ? (float)latestFix.estimatedConfidence: 0.0f;
  int   gpsSats    = hasGps ? latestFix.satCount                  : 0;
  int   gpsSatsGood = 0;
  if (hasGps) {
    for (int i = 0; i < latestFix.satCount; i++)
      if (latestFix.sats[i].signalStrength >= 30) gpsSatsGood++;
  }

  // After getting a GNSS fix, the modem usually has a valid UTC clock.
  // Resync the SD2405 to Belgium time (CET/CEST) if needed.
  rtcSyncFromModem();
  // Re-read RTC so this cycle's timestamp reflects the freshly-synced value
  if (rtcGetTime(t)) rtcFormat(t, timestamp);

  // ──────────────────────────────────────────────────────────────────────
  // SD log NOW, before LTE is even attempted.  This guarantees that every
  // cycle is persisted to the card regardless of cellular connectivity.
  // LTE columns use the last-known values from the previous successful cycle
  // (empty on first boot).
  // ──────────────────────────────────────────────────────────────────────
  sdLogAll(publishCounter, timestamp,
           ph, ec, do_val, chipTemp,
           hasGps, lat, lon, gpsSpeedN, gpsSpeedE, gpsConf, gpsSats, gpsSatsGood,
           imuOk, ax, ay, az, gx, gy, gz, mx, my, mz,
           lastLteOp, lastLteBand, lastLteRsrp);

  // Connect to LTE
  if (!lteConnect()) {
    Serial.println("[LOOP] LTE connect failed - skipping publish");
    delay(CYCLE_DELAY_MS);
    return;
  }

  // Print cell info and remember it for the next cycle's SD row
  WalterModemRsp rsp = {};
  if (modem.getCellInformation(WALTER_MODEM_SQNMONI_REPORTS_SERVING_CELL, &rsp)) {
    Serial.printf("[LTE] Operator: %s | Band: %u | RSRP: %.1f dBm\r\n",
                  rsp.data.cellInformation.netName,
                  rsp.data.cellInformation.band,
                  rsp.data.cellInformation.rsrp);
    strncpy(lastLteOp, rsp.data.cellInformation.netName, sizeof(lastLteOp) - 1);
    lastLteOp[sizeof(lastLteOp) - 1] = '\0';
    lastLteBand = (int)rsp.data.cellInformation.band;
    lastLteRsrp = rsp.data.cellInformation.rsrp;
  }

  // Publish to ThingsBoard
  publishToThingsBoard(timestamp, ph, ec, do_val, hasGps);

  // Drop LTE to save power
  lteDisconnect();

  Serial.printf("[LOOP] Cycle %d done. Sleeping %d s...\r\n\r\n",
                publishCounter, CYCLE_DELAY_MS / 1000);
  delay(CYCLE_DELAY_MS);
}
