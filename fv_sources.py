"""Data sources for FiberView. Each source returns (signal_V, time_s, sync) chunks.

    NISource       NI-DAQmx device (e.g. USB-6229, PCIe-6251, USB-6002)
    ArduinoSource  Arduino + ADS1115 running arduino/fiberview_ads1115.ino
    SimSource      synthetic photometry signal, for trying the program without hardware
"""
import time

import numpy as np


class SourceError(RuntimeError):
    pass


class BaseSource:
    name = "source"
    fs = 1000.0

    def start(self):
        pass

    def read(self):
        """Return (signal, time, sync_or_None) for all new samples."""
        raise NotImplementedError

    def stop(self):
        pass

    def meta(self):
        return {"source": self.name, "fs": self.fs}


# ---------------------------------------------------------------- simulated
class SimSource(BaseSource):
    name = "Simulated"

    def __init__(self, fs=1000.0, baseline=1.2, noise_mV=1.6, transients=True):
        self.fs = float(fs)
        self.baseline = baseline
        self.noise = noise_mV * 1e-3
        self.transients = transients
        self.rng = np.random.default_rng()

    def start(self):
        self.t0 = time.perf_counter()
        self.n = 0
        self.events = []

    def _clean(self, t):
        F = self.baseline * (0.85 + 0.10 * np.exp(-t / 60) + 0.05 * np.exp(-t / 600))
        if self.transients:
            for e in self.events:
                d = t - e
                m = d >= 0
                F[m] += self.baseline * 0.06 * (1 - np.exp(-d[m] / 0.08)) * np.exp(-d[m] / 0.8)
        return F

    def read(self):
        n_due = int((time.perf_counter() - self.t0) * self.fs)
        k = n_due - self.n
        if k <= 0:
            return np.array([]), np.array([]), None
        t = (self.n + np.arange(k)) / self.fs
        # visual stimulus every 5 s (sync TTL 50 ms) evokes a transient
        sync = ((t % 5.0) < 0.05).astype(np.float32)
        for e in np.flatnonzero(np.diff(np.r_[0, sync]) > 0):
            self.events.append(t[e] + 0.15)
        self.events = [e for e in self.events if e > t[0] - 10]
        sig = self._clean(t) + self.rng.normal(0, self.noise, k) + 0.04e-3 * np.sin(2 * np.pi * 50 * t)
        self.n = n_due
        return sig, t, sync


# ---------------------------------------------------------------- NI-DAQmx
def ni_devices():
    """List of (name, product_type). Empty if NI-DAQmx or nidaqmx is missing."""
    try:
        import nidaqmx.system
        return [(d.name, d.product_type) for d in nidaqmx.system.System.local().devices]
    except Exception:
        return []


class NISource(BaseSource):
    name = "NI-DAQmx"

    def __init__(self, channel="Dev1/ai0", config="Differential", vrange=5.0, fs=10000.0,
                 sync_channel=None):
        self.channel = channel
        self.config = config
        self.vrange = float(vrange)
        self.fs = float(fs)
        self.sync_channel = sync_channel or None
        self.task = None

    def start(self):
        try:
            import nidaqmx
            from nidaqmx.constants import AcquisitionType, TerminalConfiguration
        except Exception as e:
            raise SourceError("The nidaqmx package or the NI-DAQmx driver is not available.\n" + str(e))
        TC = TerminalConfiguration
        term = {"Differential": getattr(TC, "DIFF", None) or getattr(TC, "DIFFERENTIAL", None),
                "RSE": TC.RSE, "NRSE": TC.NRSE}[self.config]
        try:
            self.task = nidaqmx.Task()
            self.task.ai_channels.add_ai_voltage_chan(self.channel, terminal_config=term,
                                                      min_val=-self.vrange, max_val=self.vrange)
            if self.sync_channel:
                self.task.ai_channels.add_ai_voltage_chan(self.sync_channel, terminal_config=TC.RSE,
                                                          min_val=-10, max_val=10)
            self.task.timing.cfg_samp_clk_timing(self.fs, sample_mode=AcquisitionType.CONTINUOUS,
                                                 samps_per_chan=int(self.fs * 10))
            self.task.start()
        except Exception as e:
            self.stop()
            msg = str(e)
            if "-50103" in msg or "reserved" in msg.lower():
                msg = "The device is busy. Close the NI MAX Test Panel or other programs using it.\n\n" + msg
            raise SourceError(msg)
        self.n = 0

    def read(self):
        from nidaqmx.constants import READ_ALL_AVAILABLE
        data = self.task.read(number_of_samples_per_channel=READ_ALL_AVAILABLE)
        if self.sync_channel:
            sig = np.asarray(data[0], float)
            sync = np.asarray(data[1], np.float32)
        else:
            sig = np.asarray(data, float)
            sync = None
        k = len(sig)
        t = (self.n + np.arange(k)) / self.fs
        self.n += k
        return sig, t, sync

    def stop(self):
        if self.task is not None:
            try:
                self.task.stop()
            except Exception:
                pass
            try:
                self.task.close()
            except Exception:
                pass
            self.task = None

    def meta(self):
        return {"source": self.name, "fs": self.fs, "channel": self.channel, "config": self.config,
                "range_V": self.vrange, "sync_channel": self.sync_channel}


# ---------------------------------------------------------------- Arduino + ADS1115
def serial_ports():
    try:
        from serial.tools import list_ports
        return [(p.device, p.description) for p in list_ports.comports()]
    except Exception:
        return []


class SerialParser:
    """Parses 'micros,raw,sync' lines from the FiberView Arduino sketch.
    Separate from the port so it can be tested without hardware."""

    def __init__(self):
        self.buf = b""
        self.lsb_V = 0.125e-3          # ADS1115 at gain 1 (+/-4.096 V)
        self.header = None
        self.messages = []
        self.t_first = None
        self.last_us = None
        self.wraps = 0
        self.gaps = 0

    def feed(self, data):
        self.buf += data
        *lines, self.buf = self.buf.split(b"\n")
        us, raw, syn = [], [], []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            if ln.startswith(b"#"):
                txt = ln.decode("ascii", "replace")
                self.messages.append(txt)
                if txt.startswith("#FIBERVIEW"):
                    self.header = txt
                    for tok in txt.split():
                        if tok.startswith("lsb_mV="):
                            try:
                                self.lsb_V = float(tok[7:]) * 1e-3
                            except ValueError:
                                pass
                continue
            parts = ln.split(b",")
            if len(parts) < 2:
                continue
            try:
                u = int(parts[0])
                r = int(parts[1])
                s = int(parts[2]) if len(parts) > 2 else 0
            except ValueError:
                continue
            if self.last_us is not None and u < self.last_us - 2 ** 31:
                self.wraps += 1                    # micros() wrapped (every ~71 min)
            self.last_us = u
            us.append(u + self.wraps * 2 ** 32)
            raw.append(r)
            syn.append(s)
        if not us:
            return np.array([]), np.array([]), np.array([], np.float32)
        us = np.asarray(us, np.float64)
        if self.t_first is None:
            self.t_first = us[0]
        t = (us - self.t_first) * 1e-6
        if len(t) > 1:
            d = np.diff(t)
            self.gaps += int(np.sum(d > 3 * np.median(d)))
        return np.asarray(raw, float) * self.lsb_V, t, np.asarray(syn, np.float32)


class ArduinoSource(BaseSource):
    name = "Arduino ADS1115"

    def __init__(self, port, baud=500000):
        self.port = port
        self.baud = int(baud)
        self.fs = 860.0
        self.ser = None

    def start(self):
        try:
            import serial
        except Exception as e:
            raise SourceError("The pyserial package is missing.\n" + str(e))
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0)
        except Exception as e:
            raise SourceError(f"Could not open {self.port}.\n{e}")
        self.parser = SerialParser()
        # the Uno resets when the port opens: wait for the sketch's header line
        t_end = time.time() + 5
        while time.time() < t_end and self.parser.header is None:
            self.parser.feed(self.ser.read(4096))
            for m in self.parser.messages:
                if m.startswith("#ERROR"):
                    self.stop()
                    raise SourceError("Arduino reports: " + m[1:])
            time.sleep(0.02)
        if self.parser.header is None:
            self.stop()
            raise SourceError(f"No FiberView data on {self.port}.\nIs fiberview_ads1115.ino uploaded, "
                              "and is the right port selected? (Close the Arduino Serial Monitor.)")
        self.parser.t_first = None
        self._fs_t, self._fs_n = None, 0

    def read(self):
        sig, t, sync = self.parser.feed(self.ser.read(self.ser.in_waiting or 1))
        if len(t):
            if self._fs_t is None:
                self._fs_t = t[0]
            self._fs_n += len(t)
            if t[-1] - self._fs_t > 1:
                self.fs = (self._fs_n - 1) / (t[-1] - self._fs_t)
        return sig, t, sync

    def stop(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def meta(self):
        return {"source": self.name, "fs": self.fs, "port": self.port, "baud": self.baud,
                "header": getattr(getattr(self, "parser", None), "header", None),
                "lsb_V": getattr(getattr(self, "parser", None), "lsb_V", None)}
