"""Writes the release notes: python install/release_notes.py 1.2.3 > notes.md"""
import sys

v = sys.argv[1]
print(f"""## FiberView {v}

Fiber photometry acquisition and analysis (NI-DAQmx or Arduino + ADS1115).

| Computer | Download |
|---|---|
| Windows 10/11 (64-bit) | `FiberView-{v}-Windows-setup.exe` |
| Mac with Apple silicon (M1–M4) | `FiberView-{v}-macOS-AppleSilicon.pkg` |
| Mac with Intel processor | `FiberView-{v}-macOS-Intel.pkg` |
| Arduino + ADS1115 DAQ (optional) | `FiberView-{v}-Arduino.zip` (sketch and wiring) |

No Python needed. Windows installs for the current user only (no admin rights), with a Start menu entry
and an optional desktop shortcut. The Mac package installs **FiberView.app** into Applications.

### The installers are not code-signed
- **Windows:** if "Windows protected your PC" appears, click **More info → Run anyway**.
- **Mac:** if the package "cannot be opened", go to **System Settings → Privacy & Security**, scroll down
  and click **Open Anyway** (or right-click the .pkg → Open).

### Install alongside, if you need it
- **NI devices (USB-6229, PCIe-6251, USB-6002…):** the **NI-DAQmx driver** from ni.com (Windows; it comes with NI MAX).
  FiberView works without it for the Arduino and the Analyze tab.
- **Arduino + ADS1115:** upload the sketch from the Arduino zip with the Arduino IDE
  (needs the *Adafruit ADS1X15* library).

Each installer was self-tested on synthetic data before release (processing, file formats, Arduino
stream parser and the full GUI). Help → About shows the installed version.
""")
