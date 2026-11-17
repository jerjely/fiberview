# FiberView – Arduino + ADS1115 DAQ

A low-cost alternative to an NI device for continuous-excitation photometry
(16-bit, ~860 samples/s, ±4.096 V).

**Parts:** Arduino Uno Rev3 or Uno R4 Minima (5 V), Adafruit ADS1115 breakout, a 1 kΩ resistor.

**Upload:** open `fiberview_ads1115/fiberview_ads1115.ino` in the Arduino IDE, install the
*Adafruit ADS1X15* library (Tools → Manage Libraries), select the board and port, Upload.

```
APD red  (signal) --[1 kΩ]-- ADS1115 A0
APD black (shield) ---------- ADS1115 A1 and GND
ADS1115 VDD → 5V   GND → GND   SCL → SCL   SDA → SDA
ADS1115 ALRT → pin 2           Sync TTL → pin 3 (+ its ground to GND)
```

In FiberView choose **Source: Arduino ADS1115** and the Arduino's port. Close the Arduino Serial
Monitor first. Power the Arduino before switching on the APD, and switch the APD off first.
