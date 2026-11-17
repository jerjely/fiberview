/*
  FiberView - Arduino + ADS1115 photometry DAQ
  -------------------------------------------------
  Board : Arduino Uno Rev3 / Uno R4 Minima (5 V)
  ADC   : Adafruit ADS1115 breakout, powered from 5 V
  Needs : "Adafruit ADS1X15" library (Arduino IDE: Tools > Manage Libraries)

  Wiring
    APD red  (signal) --[1 kOhm]-- ADS1115 A0
    APD black (shield) ----------- ADS1115 A1  and  GND
    ADS1115 VDD -> 5V, GND -> GND, SCL -> SCL, SDA -> SDA
    ADS1115 ALRT -> pin 2          (one interrupt per finished sample)
    Sync TTL (optional) -> pin 3   (plus its ground to GND)

  Output, one line per sample at ~860 samples/s, 500000 baud:
    micros,raw,sync
  raw is the ADS1115 count (0.125 mV per count at +/-4.096 V),
  sync is 1 if pin 3 was high or had a rising edge since the last sample.
  Lines starting with '#' are messages.
*/
#include <Wire.h>
#include <Adafruit_ADS1X15.h>

const uint8_t RDY_PIN  = 2;
const uint8_t SYNC_PIN = 3;
const unsigned long PERIOD_US = 1163;     // 1 / 860 Hz, used only without ALRT

Adafruit_ADS1115 ads;
volatile bool ready = false;
volatile unsigned long readyMicros = 0;
volatile bool syncSeen = false;
bool useAlrt = true;
unsigned long nextPoll = 0;

void onReady() { readyMicros = micros(); ready = true; }
void onSync()  { syncSeen = true; }

void setup() {
  Serial.begin(500000);
  pinMode(RDY_PIN, INPUT_PULLUP);          // ALRT is open-drain
  pinMode(SYNC_PIN, INPUT);
  if (!ads.begin()) {
    while (true) { Serial.println("#ERROR ADS1115 not found - check SDA/SCL/VDD/GND"); delay(1000); }
  }
  Wire.setClock(400000);
  ads.setGain(GAIN_ONE);                   // +/-4.096 V range, 0.125 mV per count
  ads.setDataRate(RATE_ADS1115_860SPS);
  attachInterrupt(digitalPinToInterrupt(RDY_PIN), onReady, FALLING);
  attachInterrupt(digitalPinToInterrupt(SYNC_PIN), onSync, RISING);
  Serial.println("#FIBERVIEW ADS1115 v1 gain=1 lsb_mV=0.125 rate=860");
  ads.startADCReading(ADS1X15_REG_CONFIG_MUX_DIFF_0_1, /*continuous=*/true);

  // check that ALRT pulses arrive; otherwise fall back to timed polling
  unsigned long t0 = millis();
  while (!ready && millis() - t0 < 300) {}
  if (!ready) {
    useAlrt = false;
    detachInterrupt(digitalPinToInterrupt(RDY_PIN));
    Serial.println("#WARN no ALRT signal on pin 2 - using timed polling (less even timing)");
    nextPoll = micros();
  }
}

void sendSample(unsigned long t) {
  int16_t v = ads.getLastConversionResults();
  noInterrupts();
  bool s = syncSeen;
  syncSeen = false;
  interrupts();
  s = s || digitalRead(SYNC_PIN);
  Serial.print(t);
  Serial.print(',');
  Serial.print(v);
  Serial.print(',');
  Serial.println(s ? 1 : 0);
}

void loop() {
  if (useAlrt) {
    if (ready) {
      noInterrupts();
      unsigned long t = readyMicros;
      ready = false;
      interrupts();
      sendSample(t);
    }
  } else if ((long)(micros() - nextPoll) >= 0) {
    nextPoll += PERIOD_US;
    sendSample(micros());
  }
}
