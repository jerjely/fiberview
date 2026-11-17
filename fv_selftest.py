"""FiberView self-test on synthetic data.

    FiberView --self-test [logfile]          (installed app)
    python fiberview.py --self-test [logfile]  (from source)

Runs the processing, file formats, Arduino parser and the whole GUI (offscreen) on
synthetic data, without touching the user's settings or real hardware. Exit code 0
when everything passes, 1 when anything fails, 3 when it hangs. The release workflow
runs this on every built app before packaging it.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

LINES = []


def log(msg):
    LINES.append(msg)
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _synthetic(T=60.0, fs=1000.0, seed=0):
    import numpy as np
    t = np.arange(int(T * fs)) / fs
    F = 1.2 * (0.85 + 0.10 * np.exp(-t / 60) + 0.05 * np.exp(-t / 600)) + 0.003
    sync = ((t % 5.0) < 0.05).astype(np.float32)
    for e in np.arange(0.15, T, 5.0):
        d = t - e
        m = d >= 0
        F[m] += 1.2 * 0.06 * (1 - np.exp(-d[m] / 0.08)) * np.exp(-d[m] / 0.8)
    x = F + np.random.default_rng(seed).normal(0, 1.6e-3, len(t))
    return t, x, sync, fs


def run(argv):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    logfile = None
    for flag in ("--self-test", "--selftest"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                logfile = os.path.abspath(argv[i + 1])

    def watchdog():
        time.sleep(240)
        log("FAIL  self-test did not finish within 240 s")
        _write(logfile)
        os._exit(3)
    threading.Thread(target=watchdog, daemon=True).start()

    tmp = tempfile.mkdtemp(prefix="fiberview_selftest_")
    failures = []

    def check(name, fn):
        t0 = time.time()
        try:
            info = fn()
            log(f"PASS  {name}" + (f"  ({info})" if info else "") + f"  [{time.time() - t0:.1f} s]")
        except Exception:
            failures.append(name)
            log(f"FAIL  {name}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)))

    import fv_core as core
    log(f"FiberView {core.APP_VERSION} self-test  |  Python {sys.version.split()[0]}  |  "
        f"{sys.platform}  |  frozen={getattr(sys, 'frozen', False)}")

    # 1 ---------------------------------------------------------------- packages
    def t_imports():
        import numpy, scipy, scipy.signal, scipy.optimize, scipy.ndimage  # noqa: F401
        import PySide6, pyqtgraph  # noqa: F401
        import serial, serial.tools.list_ports  # noqa: F401
        import nidaqmx, nidaqmx.system  # noqa: F401   (Python package only; the NI driver is separate)
        import fv_sources as src
        devs = src.ni_devices()
        return (f"numpy {numpy.__version__}, scipy {scipy.__version__}, PySide6 {PySide6.__version__}, "
                f"pyqtgraph {pyqtgraph.__version__}; NI devices: {len(devs)} (driver optional)")
    check("bundled packages import", t_imports)

    # 2 ---------------------------------------------------------------- processing
    def t_dff():
        import numpy as np
        t, x, sync, fs = _synthetic()
        ev = core.sync_edges(t, sync)
        assert len(ev) == 11, f"expected 11 sync edges (first pulse starts at t=0), got {len(ev)}"
        out = []
        for m in core.BASELINE_METHODS:
            r = core.compute_dff(t, x, float("nan"), dark=0.003, baseline=m,
                                 baseline_param=2 if m.startswith("Poly") else 30, trim_s=2)
            assert np.all(np.isfinite(r["dff"])), f"{m}: non-finite dF/F"
            lag, tr = core.event_average(r["t"], r["dff"], ev, 2, 4)
            assert tr.shape[0] >= 10, f"{m}: only {tr.shape[0]} trials"
            avg = tr.mean(0) - tr.mean(0)[lag < 0].mean()
            peak = float(avg.max())
            if m != "None (mean)":
                assert 3.0 < peak < 7.0, f"{m}: peak {peak:.2f}% outside 3-7%"
            out.append(f"{m.split()[0]} {peak:.1f}%")
        f, asd = core.noise_spectrum(x, fs)
        assert len(f) and np.all(np.isfinite(asd))
        return ", ".join(out)
    check("dF/F, baselines, sync-aligned average", t_dff)

    # 3 ---------------------------------------------------------------- files
    def t_files():
        import numpy as np
        t, x, sync, fs = _synthetic(20)
        p = os.path.join(tmp, "rec.npz")
        w = core.RecordingWriter(p, {"source": "self-test", "fs": fs, "dark_offset_V": 0.003, "notes": "ok"})
        for k in range(0, len(t), 997):
            w.append(x[k:k + 997], t[k:k + 997], sync[k:k + 997])
        w.close()
        r = core.load_recording(p)
        assert len(r["signal"]) == len(t) and r["sync"] is not None
        assert abs(r["meta"]["dark_offset_V"] - 0.003) < 1e-9 and r["meta"]["notes"] == "ok"
        assert not os.path.exists(os.path.join(tmp, "rec.meta.json")), "part files not cleaned up"
        # crash recovery from part files
        w = core.RecordingWriter(os.path.join(tmp, "crash.npz"), {"source": "self-test", "fs": fs})
        w.append(x[:5000], t[:5000], None)
        for fh in (w._f_sig, w._f_t, w._f_sync):
            fh.flush()
        r2 = core.load_recording(os.path.join(tmp, "crash.meta.json"))
        assert len(r2["signal"]) == 5000
        for fh in (w._f_sig, w._f_t, w._f_sync):
            fh.close()
        # old apd_live_view.py .npy
        q = os.path.join(tmp, "apd_20261001_120000_fs10000.npy")
        np.save(q, x[:10000])
        assert core.load_recording(q)["fs"] == 10000.0
        return "npz round trip, crash recovery, .npy"
    check("recording files", t_files)

    # 4 ---------------------------------------------------------------- Arduino stream parser
    def t_serial():
        import numpy as np
        from fv_sources import SerialParser
        p = SerialParser()
        lines = [b"#FIBERVIEW ADS1115 v1 gain=1 lsb_mV=0.125 rate=860"]
        us = 2 ** 32 - 5000                       # crosses the micros() wrap
        for i in range(2000):
            lines.append(f"{us % 2 ** 32},{8000 + i % 7},{1 if i % 500 == 0 else 0}".encode())
            us += 1163 if i != 1000 else 1163 * 5
        blob = b"\r\n".join(lines) + b"\r\n"
        parts = [p.feed(blob[k:k + 777]) for k in range(0, len(blob), 777)]
        sig = np.concatenate([q[0] for q in parts])
        t = np.concatenate([q[1] for q in parts])
        s = np.concatenate([q[2] for q in parts])
        assert len(sig) == 2000 and p.header is not None
        assert np.all(np.diff(t) > 0), "timestamps not monotonic across the wrap"
        assert abs(sig[0] - 1.0) < 1e-6 and int(s.sum()) == 4 and p.gaps >= 1
        return f"{core.effective_fs(t):.0f} Hz, wrap ok, {p.gaps} gap detected"
    check("Arduino ADS1115 stream parser", t_serial)

    # 5 ---------------------------------------------------------------- GUI
    def t_gui():
        from PySide6 import QtCore, QtWidgets
        import fiberview as fv
        ini = os.path.join(tmp, "settings.ini")
        fv.settings = lambda: QtCore.QSettings(ini, QtCore.QSettings.IniFormat)   # never touch real settings
        QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
        QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
        QtWidgets.QMessageBox.about = staticmethod(lambda *a, **k: None)
        QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError(f"warning dialog: {a[2] if len(a) > 2 else a}")))
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
        w = fv.MainWindow()
        w.resize(1300, 820)
        w.show()

        def spin(sec):
            t_end = time.time() + sec
            while time.time() < t_end:
                app.processEvents()
                time.sleep(0.01)

        a = w.acq
        a.src_combo.setCurrentIndex(2)            # simulated source
        a.out_dir.setText(os.path.join(tmp, "recordings"))
        a.rec_name.setText("selftest")
        a.dark.setValue(0.003)
        a.start()
        spin(1.0)
        assert a.source is not None and len(a.x_buf) > 500, "no live samples"
        a.btn_rec.setChecked(True)
        spin(2.5)
        a.btn_rec.setChecked(False)
        spin(0.3)
        a.stop()
        recs = [f for f in os.listdir(os.path.join(tmp, "recordings")) if f.endswith(".npz")]
        assert len(recs) == 1, f"recordings: {recs}"
        n_live = len(core.load_recording(os.path.join(tmp, "recordings", recs[0]))["signal"])
        assert n_live > 1500, f"only {n_live} samples recorded"

        # Analyze a 60 s synthetic recording with sync pulses
        t, x, sync, fs = _synthetic()
        p = os.path.join(tmp, "synthetic.npz")
        core.save_npz(p, {"signal": x, "time": t, "sync": sync, "fs": fs,
                          "meta": {"source": "self-test", "dark_offset_V": 0.003}})
        w.tabs.setCurrentIndex(1)
        w.ana.load(p)
        spin(0.3)
        lag, tr = w.ana.ev_tr
        assert tr.shape[0] >= 10, f"Analyze averaged {tr.shape[0]} events"
        peak = float(tr.mean(0).max())
        assert 3.0 < peak < 7.0, f"Analyze peak {peak:.2f}%"
        csv = os.path.join(tmp, "out_dFF.csv")
        png = os.path.join(tmp, "out_dFF.png")
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (csv, ""))
        w.ana.export_csv()
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (png, ""))
        w.ana.export_png()
        for f in (csv, csv[:-4] + "_sync_events.csv", csv[:-4] + "_sync_aligned.csv", png):
            assert os.path.getsize(f) > 100, f"missing export {os.path.basename(f)}"
        img = w.grab()
        assert not img.isNull() and img.width() > 500
        w.about()
        assert not w.windowIcon().isNull(), "window icon missing"
        assert core.APP_VERSION in w.windowTitle()
        w.close()
        return f"live {n_live} samples recorded, {tr.shape[0]} events averaged, peak {peak:.1f}%, exports ok"
    check("GUI: acquire (simulated), record, analyze, export", t_gui)

    shutil.rmtree(tmp, ignore_errors=True)
    log("RESULT: " + ("all tests passed" if not failures else f"{len(failures)} FAILED: " + ", ".join(failures)))
    _write(logfile)
    return 0 if not failures else 1


def _write(logfile):
    if logfile:
        try:
            with open(logfile, "w", encoding="utf-8") as fh:
                fh.write("\n".join(LINES) + "\n")
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(run(sys.argv + ["--self-test"]))
