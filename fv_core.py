"""FiberView core: signal processing and recording files (no GUI code here).

Recording format (.npz)
    signal  float32  APD voltage (V)
    time    float64  sample times in seconds from start (always present)
    sync    float32  optional sync channel (TTL level or analog volts)
    fs      float    nominal / estimated sample rate (Hz)
    meta    str      JSON with source, settings, dark offset, notes, start time

While recording, data are streamed to part files next to the target
(<name>.signal.f32, .time.f64, .sync.f32, .meta.json) and packed into the
.npz when recording stops. If the program crashes, open the .meta.json file
to recover the recording.
"""
import json
import os
import re
import time

import numpy as np
from scipy import signal as sps
from scipy.optimize import curve_fit

APP_NAME = "FiberView"
try:
    from fv_version import APP_VERSION     # written from the git tag by the release workflow
except Exception:                          # pragma: no cover
    APP_VERSION = "dev"


# ---------------------------------------------------------------- processing
def lowpass(x, fs, fc, order=4):
    """Zero-phase Butterworth low-pass. Returns a copy if fc is outside (0, fs/2)."""
    x = np.asarray(x, dtype=float)
    if fc is None or fc <= 0 or fc >= 0.5 * fs or len(x) < 3 * (order + 1) * 2:
        return x.copy()
    sos = sps.butter(order, fc, btype="low", fs=fs, output="sos")
    padlen = min(len(x) - 1, 3 * (2 * len(sos) + 1))
    return sps.sosfiltfilt(sos, x, padlen=padlen)


def effective_fs(t):
    t = np.asarray(t, dtype=float)
    if len(t) < 2 or t[-1] <= t[0]:
        return float("nan")
    return (len(t) - 1) / (t[-1] - t[0])


def noise_spectrum(x, fs, seconds=2.0):
    """Amplitude spectral density (V/sqrt(Hz)) of the last `seconds` of x."""
    n = int(min(len(x), max(8, seconds * fs)))
    seg = np.asarray(x[-n:], dtype=float)
    if n < 8:
        return np.array([]), np.array([])
    seg = seg - seg.mean()
    w = np.hanning(n)
    X = np.fft.rfft(seg * w)
    psd = 2 * np.abs(X) ** 2 / (fs * np.sum(w ** 2))
    f = np.fft.rfftfreq(n, 1 / fs)
    return f[1:], np.sqrt(psd[1:])


def band_rms(f, asd, lo, hi):
    if len(f) < 2:
        return float("nan")
    df = f[1] - f[0]
    m = (f > lo) & (f < hi)
    return float(np.sqrt(np.sum(asd[m] ** 2) * df))


def resample_uniform(t, x, fs_out):
    """Linear interpolation of (t, x) onto a uniform grid at fs_out."""
    t = np.asarray(t, dtype=float)
    if len(t) < 2:
        return t.copy(), np.asarray(x, dtype=float).copy()
    tu = np.arange(t[0], t[-1], 1.0 / fs_out)
    return tu, np.interp(tu, t, x)


def sync_edges(t, sync, threshold=None):
    """Times of rising edges in a sync channel."""
    if sync is None or len(sync) < 2:
        return np.array([])
    s = np.asarray(sync, dtype=float)
    if threshold is None:
        lo, hi = np.nanmin(s), np.nanmax(s)
        if hi - lo < 1e-6:
            return np.array([])
        threshold = lo + 0.5 * (hi - lo)
    b = s > threshold
    idx = np.flatnonzero(~b[:-1] & b[1:]) + 1
    return np.asarray(t)[idx]


BASELINE_METHODS = ["None (mean)", "Double exponential", "Polynomial", "Rolling percentile"]


def _dexp(t, a, ta, b, tb, c):
    return a * np.exp(-t / ta) + b * np.exp(-t / tb) + c


def fit_baseline(t, F, method, param=None):
    """Slow baseline F0 for bleaching correction. Works on an already low-passed,
    downsampled trace. param: polynomial degree / percentile window (s)."""
    t = np.asarray(t, float)
    F = np.asarray(F, float)
    if len(F) < 5:
        return np.full_like(F, np.nanmean(F) if len(F) else 0.0)
    if method.startswith("Double"):
        tt = t - t[0]
        dur = max(tt[-1], 1.0)
        span = F.max() - F.min()
        p0 = [span * 0.5, dur / 20, span * 0.5, dur / 2, F[-1]]
        lb = [0, 1e-3, 0, 1e-3, -np.inf]
        ub = [np.inf, dur * 10, np.inf, dur * 100, np.inf]
        try:
            p, _ = curve_fit(_dexp, tt, F, p0=p0, bounds=(lb, ub), maxfev=20000)
            return _dexp(tt, *p)
        except Exception:
            method = "Polynomial"
            param = 2
    if method.startswith("Poly"):
        deg = int(param or 2)
        tt = (t - t[0]) / max(t[-1] - t[0], 1e-9)
        c = np.polyfit(tt, F, deg)
        return np.polyval(c, tt)
    if method.startswith("Rolling"):
        from scipy.ndimage import percentile_filter
        fs = effective_fs(t)
        win = max(3, int((param or 30) * fs) | 1)
        win = min(win, len(F) - (1 - len(F) % 2))
        return percentile_filter(F, 10, size=win, mode="nearest")
    return np.full_like(F, np.mean(F))


def compute_dff(t, x, fs, *, lowpass_hz=10.0, out_hz=50.0, dark=0.0,
                baseline="Double exponential", baseline_param=None, trim_s=0.0):
    """Returns dict with t, F (low-passed, dark-subtracted), F0 and dff (%)."""
    t = np.asarray(t, float)
    x = np.asarray(x, float)
    if trim_s > 0:
        keep = t >= t[0] + trim_s
        t, x = t[keep], x[keep]
    fs_true = effective_fs(t) if not np.isfinite(fs) or fs <= 0 else fs
    tu, xu = resample_uniform(t, x, fs_true)            # handles jittered Arduino timing
    lp = lowpass(xu, fs_true, min(lowpass_hz, 0.45 * out_hz) if out_hz else lowpass_hz)
    step = max(1, int(round(fs_true / out_hz))) if out_hz else 1
    td, Fd = tu[::step], lp[::step] - dark
    F0 = fit_baseline(td, Fd, baseline, baseline_param)
    with np.errstate(divide="ignore", invalid="ignore"):
        dff = 100.0 * (Fd - F0) / F0
    return {"t": td, "F": Fd, "F0": F0, "dff": dff, "fs": fs_true / step}


def event_average(t, y, events, pre=2.0, post=5.0):
    """Peri-event traces of y around event times. Returns (lag, traces[n_events, n_lags])."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    if len(t) < 2 or len(events) == 0:
        return np.array([]), np.empty((0, 0))
    dt = np.median(np.diff(t))
    lag = np.arange(-pre, post + dt / 2, dt)
    rows = []
    for e in events:
        if e - pre < t[0] or e + post > t[-1]:
            continue
        rows.append(np.interp(e + lag, t, y))
    return lag, (np.vstack(rows) if rows else np.empty((0, len(lag))))


# ---------------------------------------------------------------- recording files
class RecordingWriter:
    """Streams samples to part files, packs them into one .npz on close()."""

    def __init__(self, path_npz, meta):
        self.path = path_npz
        base = path_npz[:-4] if path_npz.endswith(".npz") else path_npz
        self.base = base
        self.meta = dict(meta)
        self.meta.setdefault("app", f"{APP_NAME} {APP_VERSION}")
        self.meta.setdefault("start_time", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.n = 0
        self.has_sync = False
        self._f_sig = open(base + ".signal.f32", "wb")
        self._f_t = open(base + ".time.f64", "wb")
        self._f_sync = open(base + ".sync.f32", "wb")
        self._write_meta()

    def _write_meta(self):
        with open(self.base + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump(self.meta, fh, indent=2)

    def append(self, sig, t, sync=None):
        sig = np.asarray(sig, np.float32)
        if len(sig) == 0:
            return
        self._f_sig.write(sig.tobytes())
        self._f_t.write(np.asarray(t, np.float64).tobytes())
        if sync is None:
            sync = np.zeros(len(sig), np.float32)
        else:
            self.has_sync = True
        self._f_sync.write(np.asarray(sync, np.float32).tobytes())
        self.n += len(sig)

    def set_meta(self, **kw):
        self.meta.update(kw)
        self._write_meta()

    def close(self):
        for f in (self._f_sig, self._f_t, self._f_sync):
            f.close()
        self.meta["n_samples"] = self.n
        self.meta["stop_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_meta()
        rec = _load_parts(self.base)
        save_npz(self.path, rec)
        for ext in (".signal.f32", ".time.f64", ".sync.f32", ".meta.json"):
            try:
                os.remove(self.base + ext)
            except OSError:
                pass
        return self.path


def _load_parts(base):
    with open(base + ".meta.json", encoding="utf-8") as fh:
        meta = json.load(fh)
    sig = np.fromfile(base + ".signal.f32", np.float32)
    t = np.fromfile(base + ".time.f64", np.float64)
    n = min(len(sig), len(t))
    sync = None
    if os.path.exists(base + ".sync.f32"):
        s = np.fromfile(base + ".sync.f32", np.float32)[:n]
        if np.any(s != 0):
            sync = s
    sig, t = sig[:n], t[:n]
    fs = meta.get("fs") or effective_fs(t)
    return {"signal": sig, "time": t, "sync": sync, "fs": float(fs), "meta": meta}


def save_npz(path, rec):
    kw = dict(signal=np.asarray(rec["signal"], np.float32), time=np.asarray(rec["time"], np.float64),
              fs=float(rec["fs"]), meta=json.dumps(rec.get("meta", {})))
    if rec.get("sync") is not None:
        kw["sync"] = np.asarray(rec["sync"], np.float32)
    np.savez(path, **kw)


def load_recording(path):
    """Load .npz (FiberView), .meta.json (crash recovery) or .npy (old apd_live_view.py)."""
    low = path.lower()
    if low.endswith(".meta.json"):
        return _load_parts(path[: -len(".meta.json")])
    if low.endswith(".npz"):
        d = np.load(path, allow_pickle=False)
        meta = json.loads(str(d["meta"])) if "meta" in d else {}
        sig = d["signal"].astype(np.float64)
        t = d["time"] if "time" in d else np.arange(len(sig)) / float(d["fs"])
        sync = d["sync"] if "sync" in d else None
        return {"signal": sig, "time": t, "sync": sync, "fs": float(d["fs"]), "meta": meta}
    if low.endswith(".npy"):
        sig = np.load(path).astype(np.float64)
        m = re.search(r"fs(\d+)", os.path.basename(path))
        fs = float(m.group(1)) if m else 10000.0
        return {"signal": sig, "time": np.arange(len(sig)) / fs, "sync": None, "fs": fs,
                "meta": {"source": "apd_live_view.py (.npy)"}}
    raise ValueError("Unsupported file type: " + os.path.basename(path))
