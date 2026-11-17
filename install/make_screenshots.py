"""Makes the README screenshots (docs/screenshots/*.jpg, 1400 px wide) from the real app, offscreen,
with the Simulated source and synthetic recordings. Does not touch your settings.

    python install/make_screenshots.py
"""
import os
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "docs", "screenshots")
W, H = 1400, 860

import numpy as np                                    # noqa: E402
from PIL import Image                                 # noqa: E402
from PySide6 import QtCore, QtWidgets                 # noqa: E402

import fiberview as fv                                # noqa: E402
import fv_core as core                                # noqa: E402

TMP = tempfile.mkdtemp(prefix="fiberview_shots_")
fv.settings = lambda: QtCore.QSettings(os.path.join(TMP, "s.ini"), QtCore.QSettings.IniFormat)
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
SHOWN_FOLDER = os.path.join("C:\\Users\\lab", "Documents", "FiberView data").replace("/", "\\")


def synthetic_session(T=300.0, fs=1000.0, seed=3):
    """5-min GCaMP-like recording: bleaching, a visual stimulus every 10 s, spontaneous events."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(T * fs)) / fs
    base = 0.95 * (0.86 + 0.09 * np.exp(-t / 45) + 0.05 * np.exp(-t / 400))
    F = base.copy()

    def transient(t0, amp, tau=0.9):
        d = t - t0
        m = (d >= 0) & (d < 8)
        F[m] += base[m] * amp * (1 - np.exp(-d[m] / 0.07)) * np.exp(-d[m] / tau)

    stim = np.arange(5.0, T - 5, 10.0)
    for s in stim:
        transient(s + 0.12, rng.uniform(0.05, 0.09), rng.uniform(0.7, 1.1))
    for s in rng.uniform(2, T - 2, 45):
        transient(s, rng.uniform(0.01, 0.035), rng.uniform(0.4, 0.8))
    sync = np.zeros(len(t), np.float32)
    for s in stim:
        sync[(t >= s) & (t < s + 0.05)] = 1
    x = F + 0.0023 + rng.normal(0, 1.7e-3, len(t)) + 0.04e-3 * np.sin(2 * np.pi * 50 * t)
    return t, x, sync, fs


def spin(app, sec):
    t_end = time.time() + sec
    while time.time() < t_end:
        app.processEvents()
        time.sleep(0.01)


def save(widget, name):
    os.makedirs(OUT, exist_ok=True)
    img = widget.grab().toImage()
    p = os.path.join(TMP, name + ".png")
    img.save(p)
    im = Image.open(p).convert("RGB")
    if im.width != W:
        im = im.resize((W, round(im.height * W / im.width)), Image.LANCZOS)
    im.save(os.path.join(OUT, name + ".jpg"), quality=88, optimize=True)
    print("wrote", os.path.join("docs", "screenshots", name + ".jpg"), im.size)


def main():
    app = QtWidgets.QApplication([sys.argv[0]])
    w = fv.MainWindow()
    w.resize(W, H)
    w.show()
    a = w.acq

    # 1 live view with noise spectrum
    a.src_combo.setCurrentIndex(2)
    a.src_stack.setCurrentIndex(2)
    a.win_s.setValue(10)
    a.lp_hz.setValue(20)
    a.dark.setValue(0.00231)
    a.out_dir.setText(os.path.join(TMP, "FiberView data"))
    a.start()
    a.out_dir.setText(SHOWN_FOLDER)          # display only; nothing is recorded in this shot
    spin(app, 11.5)
    save(w, "1-acquire-live")

    # 2 recording in progress, longer window, low-pass trace only
    a.show_raw.setChecked(False)
    a.show_spec.setChecked(False)
    a.win_s.setValue(30)
    a.lp_hz.setValue(10)
    a.rec_name.setText("PV-Cre_m12_SC")
    a.rec_notes.setPlainText("APD gain max, LED 40 %, CFM12L05 fiber\nvisual stimulus every 5 s, sync on AI 1")
    a.out_dir.setText(os.path.join(TMP, "FiberView data"))
    a.btn_rec.setChecked(True)               # the recording is written to the temporary folder ...
    a.out_dir.setText(SHOWN_FOLDER)          # ... the field shows a typical path
    spin(app, 24)
    save(w, "2-acquire-recording")
    a.btn_rec.setChecked(False)
    spin(app, 0.3)
    a.stop()

    # 3 analysis of a 5-min session
    t, x, sync, fs = synthetic_session()
    p = os.path.join(TMP, "2026-11-17_14-05-12_PV-Cre_m12_SC.npz")
    core.save_npz(p, {"signal": x, "time": t, "sync": sync, "fs": fs,
                      "meta": {"source": "NI-DAQmx", "dark_offset_V": 0.00231, "start_time": "2026-11-17 14:05:12",
                               "notes": "APD gain max, LED 40 %\nvisual stimulus every 10 s"}})
    an = w.ana
    an.trim.setValue(0)
    an.baseline.setCurrentText("Double exponential")
    an.pre.setValue(2)
    an.post.setValue(6)
    w.tabs.setCurrentIndex(1)
    an.load(p)
    w.statusBar().clearMessage()
    an.p_sig.legend.anchor(itemPos=(1, 0), parentPos=(1, 0), offset=(-12, 8))   # legend top-right
    spin(app, 0.8)
    save(w, "3-analyze-dff")

    # 4 zoomed in, rolling-percentile baseline
    an.baseline.setCurrentText("Rolling percentile")
    an.bparam.setValue(30)
    an.recompute()
    an.p_sig.setXRange(120, 165, padding=0)
    seg = (t >= 120) & (t <= 165)
    an.p_sig.setYRange(float(x[seg].min()) - 0.005, float(x[seg].max()) + 0.005, padding=0)
    an.p_dff.enableAutoRange(axis="y")
    spin(app, 0.8)
    save(w, "4-analyze-zoom")
    w.close()


if __name__ == "__main__":
    main()
