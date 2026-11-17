"""FiberView - fiber photometry acquisition and review.

Acquire: live APD signal from an NI-DAQmx device or an Arduino + ADS1115,
         noise statistics, dark-offset measurement and recording to .npz.
Analyze: open a recording, low-pass, bleaching baseline, dF/F, sync-aligned
         average, export CSV / PNG.
"""
import json
import os
import sys
import time
import traceback

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

import fv_core as core
import fv_sources as src

HERE = os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", HERE)          # bundled resources in the installed (PyInstaller) app
REPO_URL = "https://github.com/jerjely/FiberView"


def resource(*parts):
    """Path of a bundled file: next to the script when run from source, inside the app when installed."""
    for base in (RES, HERE):
        p = os.path.join(base, *parts)
        if os.path.exists(p):
            return p
    return os.path.join(HERE, *parts)


def app_icon():
    icon = QtGui.QIcon()
    for name in ("fiberview_256.png", "fiberview_512.png", "fiberview.ico"):
        p = resource("assets", name)
        if os.path.exists(p):
            icon.addFile(p)
    return icon
pg.setConfigOptions(antialias=False, background="w", foreground="#333")
SIG_COLOR = (31, 119, 180)
LP_COLOR = (230, 110, 20)
F0_COLOR = (90, 90, 90)
DFF_COLOR = (34, 139, 34)
SYNC_COLOR = (200, 40, 40, 90)
APD_MAX_V = 4.1


def settings():
    return QtCore.QSettings("PTE-Volgyi-lab", "FiberView")


def spin(lo, hi, val, step=1.0, dec=2, suffix=""):
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def fmt_v(v):
    if not np.isfinite(v):
        return "–"
    a = abs(v)
    if a >= 1:
        return f"{v:.3f} V"
    if a >= 1e-3:
        return f"{v * 1e3:.2f} mV"
    return f"{v * 1e6:.1f} µV"


# ===================================================================== Acquire
class AcquireTab(QtWidgets.QWidget):
    recording_saved = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.writer = None
        self.dark_samples = None
        self.tick = 0
        self._build()
        self._load_settings()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._on_timer)

    # ------------------------------------------------------------ layout
    def _build(self):
        ctrl = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(ctrl)
        cl.setContentsMargins(4, 4, 4, 4)

        # source
        g = QtWidgets.QGroupBox("Source")
        gl = QtWidgets.QVBoxLayout(g)
        self.src_combo = QtWidgets.QComboBox()
        self.src_combo.addItems(["NI-DAQmx", "Arduino ADS1115", "Simulated"])
        gl.addWidget(self.src_combo)
        self.src_stack = QtWidgets.QStackedWidget()
        gl.addWidget(self.src_stack)

        ni = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(ni)
        f.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.ni_dev = QtWidgets.QComboBox()
        self.ni_dev.setEditable(True)
        b = QtWidgets.QPushButton("↻")
        b.setFixedWidth(28)
        b.setToolTip("Refresh device list")
        b.clicked.connect(self._refresh_ni)
        row.addWidget(self.ni_dev, 1)
        row.addWidget(b)
        f.addRow("Device", row)
        self.ni_chan = QtWidgets.QComboBox()
        self.ni_chan.setEditable(True)
        self.ni_chan.addItems([f"ai{i}" for i in range(8)])
        f.addRow("APD channel", self.ni_chan)
        self.ni_cfg = QtWidgets.QComboBox()
        self.ni_cfg.addItems(["Differential", "RSE", "NRSE"])
        self.ni_cfg.setToolTip("Differential: red → AI 0, black → AI 8")
        f.addRow("Input", self.ni_cfg)
        self.ni_range = QtWidgets.QComboBox()
        self.ni_range.addItems(["±5 V", "±10 V", "±1 V", "±0.2 V"])
        f.addRow("Range", self.ni_range)
        self.ni_rate = spin(100, 250000, 10000, 1000, 0, " Hz")
        f.addRow("Sample rate", self.ni_rate)
        self.ni_sync = QtWidgets.QComboBox()
        self.ni_sync.setEditable(True)
        self.ni_sync.addItems(["none"] + [f"ai{i}" for i in range(1, 8)])
        self.ni_sync.setToolTip("Optional TTL sync input (recorded single-ended)")
        f.addRow("Sync channel", self.ni_sync)
        self.src_stack.addWidget(ni)

        ar = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(ar)
        f.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.ar_port = QtWidgets.QComboBox()
        self.ar_port.setEditable(True)
        b = QtWidgets.QPushButton("↻")
        b.setFixedWidth(28)
        b.clicked.connect(self._refresh_ports)
        row.addWidget(self.ar_port, 1)
        row.addWidget(b)
        f.addRow("Port", row)
        self.ar_baud = QtWidgets.QComboBox()
        self.ar_baud.addItems(["500000", "250000", "115200"])
        f.addRow("Baud", self.ar_baud)
        lab = QtWidgets.QLabel("ADS1115, 860 samples/s, ±4.096 V.\nSync TTL on Arduino pin 3.")
        lab.setStyleSheet("color:#666")
        f.addRow(lab)
        self.src_stack.addWidget(ar)

        sim = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(sim)
        f.setContentsMargins(0, 0, 0, 0)
        self.sim_rate = spin(100, 20000, 1000, 100, 0, " Hz")
        f.addRow("Sample rate", self.sim_rate)
        lab = QtWidgets.QLabel("Synthetic signal with a sync\npulse and transient every 5 s.")
        lab.setStyleSheet("color:#666")
        f.addRow(lab)
        self.src_stack.addWidget(sim)
        self.src_combo.currentIndexChanged.connect(self.src_stack.setCurrentIndex)

        row = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("▶  Start")
        self.btn_stop = QtWidgets.QPushButton("■  Stop")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        gl.addLayout(row)
        cl.addWidget(g)

        # display
        g = QtWidgets.QGroupBox("Display")
        f = QtWidgets.QFormLayout(g)
        self.win_s = spin(1, 600, 10, 1, 0, " s")
        f.addRow("Window", self.win_s)
        self.lp_hz = spin(0.5, 1000, 20, 1, 1, " Hz")
        self.lp_hz.setToolTip("Low-pass for the orange trace and 'low-pass SD'")
        f.addRow("Low-pass", self.lp_hz)
        self.show_raw = QtWidgets.QCheckBox("Show raw trace")
        self.show_raw.setChecked(True)
        f.addRow(self.show_raw)
        self.show_spec = QtWidgets.QCheckBox("Show noise spectrum")
        self.show_spec.setChecked(True)
        self.show_spec.toggled.connect(lambda on: self.spec_plot.setVisible(on))
        f.addRow(self.show_spec)
        cl.addWidget(g)

        # dark offset
        g = QtWidgets.QGroupBox("Dark offset")
        f = QtWidgets.QFormLayout(g)
        self.dark = spin(-5, 5, 0.0, 0.0001, 5, " V")
        self.dark.setToolTip("APD output with the LED off. Saved with recordings and\n"
                             "subtracted before ΔF/F in the Analyze tab.")
        f.addRow("Offset", self.dark)
        self.btn_dark = QtWidgets.QPushButton("Measure (5 s, LED off)")
        self.btn_dark.clicked.connect(self._measure_dark)
        self.btn_dark.setEnabled(False)
        f.addRow(self.btn_dark)
        cl.addWidget(g)

        # recording
        g = QtWidgets.QGroupBox("Recording")
        f = QtWidgets.QFormLayout(g)
        row = QtWidgets.QHBoxLayout()
        self.out_dir = QtWidgets.QLineEdit()
        b = QtWidgets.QPushButton("…")
        b.setFixedWidth(28)
        b.clicked.connect(self._choose_dir)
        row.addWidget(self.out_dir, 1)
        row.addWidget(b)
        f.addRow("Folder", row)
        self.rec_name = QtWidgets.QLineEdit()
        self.rec_name.setPlaceholderText("e.g. PV-Cre_m12_SC")
        f.addRow("Name", self.rec_name)
        self.rec_notes = QtWidgets.QPlainTextEdit()
        self.rec_notes.setPlaceholderText("Notes (gain, LED power, fiber, stimulus…)")
        self.rec_notes.setFixedHeight(64)
        f.addRow(self.rec_notes)
        self.btn_rec = QtWidgets.QPushButton("●  Record")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setEnabled(False)
        self.btn_rec.toggled.connect(self._toggle_record)
        f.addRow(self.btn_rec)
        self.rec_label = QtWidgets.QLabel("Not recording")
        self.rec_label.setWordWrap(True)
        f.addRow(self.rec_label)
        cl.addWidget(g)
        cl.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(ctrl)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(290)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        # plots
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.stats = QtWidgets.QLabel("Press Start.")
        self.stats.setStyleSheet("font-size:13pt; padding:4px;")
        self.stats.setTextFormat(QtCore.Qt.RichText)
        rl.addWidget(self.stats)
        self.trace_plot = pg.PlotWidget()
        self.trace_plot.setLabel("left", "APD output", units="V")
        self.trace_plot.setLabel("bottom", "time", units="s")
        self.trace_plot.showGrid(x=True, y=True, alpha=0.2)
        self.trace_plot.addLegend(offset=(10, 10))
        self.trace_plot.setDownsampling(auto=True, mode="peak")
        self.trace_plot.setClipToView(True)
        self.raw_curve = self.trace_plot.plot(pen=pg.mkPen(SIG_COLOR + (140,), width=1), name="raw")
        self.lp_curve = self.trace_plot.plot(pen=pg.mkPen(LP_COLOR, width=2), name="low-pass")
        self.sync_curve = self.trace_plot.plot(pen=pg.mkPen((200, 40, 40), width=1.5), name="sync")
        rl.addWidget(self.trace_plot, 3)
        self.spec_plot = pg.PlotWidget()
        self.spec_plot.setLogMode(x=True, y=True)
        self.spec_plot.setLabel("left", "noise (V/√Hz)")
        self.spec_plot.setLabel("bottom", "frequency", units="Hz")
        self.spec_plot.showGrid(x=True, y=True, alpha=0.2)
        self.spec_plot.getAxis("left").enableAutoSIPrefix(False)
        self.spec_plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.spec_curve = self.spec_plot.plot(pen=pg.mkPen(SIG_COLOR, width=1))
        rl.addWidget(self.spec_plot, 2)

        lay = QtWidgets.QHBoxLayout(self)
        lay.addWidget(scroll)
        lay.addWidget(right, 1)

    # ------------------------------------------------------------ settings
    def _load_settings(self):
        s = settings()
        self._refresh_ni()
        self._refresh_ports()
        self.src_combo.setCurrentIndex(int(s.value("acq/source", 0)))
        self.src_stack.setCurrentIndex(self.src_combo.currentIndex())
        for w, key in ((self.ni_dev, "acq/ni_dev"), (self.ni_chan, "acq/ni_chan"), (self.ni_sync, "acq/ni_sync"),
                       (self.ar_port, "acq/ar_port")):
            v = s.value(key)
            if v:
                w.setCurrentText(str(v))
        for w, key in ((self.ni_cfg, "acq/ni_cfg"), (self.ni_range, "acq/ni_range"), (self.ar_baud, "acq/ar_baud")):
            v = s.value(key)
            if v and w.findText(str(v)) >= 0:
                w.setCurrentText(str(v))
        for w, key in ((self.ni_rate, "acq/ni_rate"), (self.sim_rate, "acq/sim_rate"), (self.win_s, "acq/win"),
                       (self.lp_hz, "acq/lp"), (self.dark, "acq/dark")):
            v = s.value(key)
            if v is not None:
                w.setValue(float(v))
        self.out_dir.setText(s.value("acq/out_dir", os.path.join(os.path.expanduser("~"), "Documents",
                                                                 "FiberView data")))
        self.rec_name.setText(s.value("acq/name", ""))

    def save_settings(self):
        s = settings()
        s.setValue("acq/source", self.src_combo.currentIndex())
        s.setValue("acq/ni_dev", self.ni_dev.currentText())
        s.setValue("acq/ni_chan", self.ni_chan.currentText())
        s.setValue("acq/ni_cfg", self.ni_cfg.currentText())
        s.setValue("acq/ni_range", self.ni_range.currentText())
        s.setValue("acq/ni_rate", self.ni_rate.value())
        s.setValue("acq/ni_sync", self.ni_sync.currentText())
        s.setValue("acq/ar_port", self.ar_port.currentText())
        s.setValue("acq/ar_baud", self.ar_baud.currentText())
        s.setValue("acq/sim_rate", self.sim_rate.value())
        s.setValue("acq/win", self.win_s.value())
        s.setValue("acq/lp", self.lp_hz.value())
        s.setValue("acq/dark", self.dark.value())
        s.setValue("acq/out_dir", self.out_dir.text())
        s.setValue("acq/name", self.rec_name.text())

    def _refresh_ni(self):
        cur = self.ni_dev.currentText()
        self.ni_dev.clear()
        devs = src.ni_devices()
        for name, prod in devs:
            self.ni_dev.addItem(f"{name}  ({prod})", name)
        if not devs:
            self.ni_dev.addItem("Dev1")
        if cur:
            self.ni_dev.setCurrentText(cur)

    def _refresh_ports(self):
        cur = self.ar_port.currentText()
        self.ar_port.clear()
        for dev, desc in src.serial_ports():
            self.ar_port.addItem(f"{dev}  ({desc})", dev)
        if cur:
            self.ar_port.setCurrentText(cur)

    def _choose_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Recording folder", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    # ------------------------------------------------------------ run
    def _make_source(self):
        i = self.src_combo.currentIndex()
        if i == 0:
            dev = self.ni_dev.currentData() or self.ni_dev.currentText().split()[0]
            chan = self.ni_chan.currentText().strip()
            sync = self.ni_sync.currentText().strip()
            rng = float(self.ni_range.currentText().replace("±", "").replace("V", ""))
            return src.NISource(f"{dev}/{chan}", self.ni_cfg.currentText(), rng, self.ni_rate.value(),
                                None if sync in ("", "none") else f"{dev}/{sync}")
        if i == 1:
            port = self.ar_port.currentData() or self.ar_port.currentText().split()[0]
            return src.ArduinoSource(port, int(self.ar_baud.currentText()))
        return src.SimSource(self.sim_rate.value())

    def start(self):
        try:
            self.source = self._make_source()
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                self.source.start()
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        except Exception as e:
            self.source = None
            QtWidgets.QMessageBox.warning(self, "Could not start", str(e))
            return
        self.t_buf = np.zeros(0)
        self.x_buf = np.zeros(0)
        self.s_buf = np.zeros(0)
        self.has_sync = False
        self.tick = 0
        self.src_combo.setEnabled(False)
        self.src_stack.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_rec.setEnabled(True)
        self.btn_dark.setEnabled(True)
        self.save_settings()
        self.timer.start()

    def stop(self):
        if self.btn_rec.isChecked():
            self.btn_rec.setChecked(False)
        self.timer.stop()
        if self.source is not None:
            self.source.stop()
        self.source = None
        self.src_combo.setEnabled(True)
        self.src_stack.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_rec.setEnabled(False)
        self.btn_dark.setEnabled(False)
        self.dark_samples = None
        self.btn_dark.setText("Measure (5 s, LED off)")

    def _on_timer(self):
        try:
            sig, t, sync = self.source.read()
        except Exception as e:
            self.stop()
            QtWidgets.QMessageBox.warning(self, "Acquisition stopped", str(e))
            return
        if len(sig):
            if sync is not None:
                self.has_sync = True
            else:
                sync = np.zeros(len(sig), np.float32)
            if self.writer is not None:
                try:
                    self.writer.append(sig, t, sync if self.has_sync else None)
                except Exception as e:
                    self.btn_rec.setChecked(False)
                    QtWidgets.QMessageBox.warning(self, "Recording stopped", str(e))
            if self.dark_samples is not None:
                self.dark_samples.append(sig)
                n = sum(len(a) for a in self.dark_samples)
                if n >= 5 * self.source.fs:
                    v = float(np.mean(np.concatenate(self.dark_samples)))
                    self.dark.setValue(v)
                    self.dark_samples = None
                    self.btn_dark.setText("Measure (5 s, LED off)")
                    self.btn_dark.setEnabled(True)
                    if self.writer is not None:
                        self.writer.set_meta(dark_offset_V=v)
            keep_t = self.win_s.value()
            self.t_buf = np.concatenate([self.t_buf, t])
            self.x_buf = np.concatenate([self.x_buf, sig])
            self.s_buf = np.concatenate([self.s_buf, sync])
            cut = np.searchsorted(self.t_buf, self.t_buf[-1] - keep_t)
            if cut > 0:
                self.t_buf, self.x_buf, self.s_buf = self.t_buf[cut:], self.x_buf[cut:], self.s_buf[cut:]
        self.tick += 1
        self._redraw()
        if self.writer is not None:
            el = time.time() - self.rec_t0
            self.rec_label.setText(f"<b style='color:#c00'>● {int(el // 60):02d}:{int(el % 60):02d}</b>  "
                                   f"{self.writer.n:,} samples<br>{os.path.basename(self.writer.path)}")

    def _redraw(self):
        n = len(self.x_buf)
        if n < 4:
            return
        fs = core.effective_fs(self.t_buf)
        if not np.isfinite(fs):
            return
        t = self.t_buf - self.t_buf[-1]
        x = self.x_buf
        lp = core.lowpass(x, fs, self.lp_hz.value())
        self.raw_curve.setData(t, x) if self.show_raw.isChecked() else self.raw_curve.setData([], [])
        self.lp_curve.setData(t, lp)
        if self.has_sync and np.ptp(self.s_buf) > 0:
            lo, hi = np.min(x), np.max(x)
            s = self.s_buf / max(np.max(self.s_buf), 1e-9)
            self.sync_curve.setData(t, lo + s * (hi - lo) * 0.15)
        else:
            self.sync_curve.setData([], [])
        if self.show_spec.isChecked() and (self.tick % 10 == 1):
            f, asd = core.noise_spectrum(x, fs, 2.0)
            if len(f):
                self.spec_curve.setData(f, np.maximum(asd, 1e-12))
                self._mains = core.band_rms(f, asd, 48, 52)
        if self.tick % 4 == 1:
            mean = float(np.mean(x))
            sd = float(np.std(x))
            # SD of the low-passed trace, ignoring edge effects
            m = max(1, int(0.05 * len(lp)))
            lpsd = float(np.std(lp[m:-m])) if len(lp) > 2 * m + 2 else float("nan")
            mains = getattr(self, "_mains", float("nan"))
            warn = ""
            rng = getattr(self.source, "vrange", None)
            if np.max(x) > APD_MAX_V - 0.1 or (rng and np.max(np.abs(x)) > 0.98 * rng):
                warn = "  <b style='color:#c00'>⚠ near saturation – lower gain or LED</b>"
            mean_d = mean - self.dark.value()
            snr = f"{abs(mean_d) / lpsd:,.0f}" if lpsd > 0 and abs(mean_d) > 5 * lpsd else "–"
            self.stats.setText(
                f"mean <b>{fmt_v(mean)}</b> &nbsp;|&nbsp; raw SD {fmt_v(sd)} &nbsp;|&nbsp; "
                f"low-pass SD <b>{fmt_v(lpsd)}</b> &nbsp;|&nbsp; 50 Hz {fmt_v(mains)} rms &nbsp;|&nbsp; "
                f"signal/noise {snr} &nbsp;|&nbsp; {fs:,.0f} Hz{warn}")

    # ------------------------------------------------------------ dark + record
    def _measure_dark(self):
        if QtWidgets.QMessageBox.question(
                self, "Dark offset", "Switch the LED off (keep the gain at the recording setting), "
                "then press Yes. The next 5 s are averaged.") != QtWidgets.QMessageBox.Yes:
            return
        self.dark_samples = []
        self.btn_dark.setEnabled(False)
        self.btn_dark.setText("Measuring…")

    def _toggle_record(self, on):
        if on:
            folder = self.out_dir.text().strip() or os.path.expanduser("~")
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                QtWidgets.QMessageBox.warning(self, "Folder", str(e))
                self.btn_rec.setChecked(False)
                return
            name = self.rec_name.text().strip() or "recording"
            name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
            path = os.path.join(folder, f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{name}.npz")
            meta = self.source.meta()
            meta.update(name=self.rec_name.text(), notes=self.rec_notes.toPlainText(),
                        dark_offset_V=self.dark.value())
            try:
                self.writer = core.RecordingWriter(path, meta)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Recording", str(e))
                self.btn_rec.setChecked(False)
                return
            self.rec_t0 = time.time()
            self.btn_rec.setText("■  Stop recording")
            self.btn_stop.setEnabled(False)
            self.save_settings()
        else:
            if self.writer is None:
                return
            w, self.writer = self.writer, None
            try:
                w.set_meta(fs=getattr(self.source, "fs", None) or w.meta.get("fs"),
                           notes=self.rec_notes.toPlainText())
                path = w.close()
                self.rec_label.setText(f"Saved {os.path.basename(path)}<br>({w.n:,} samples)")
                self.recording_saved.emit(path)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Saving failed",
                                              f"{e}\n\nThe raw part files are kept next to it; open the "
                                              f".meta.json file in Analyze to recover the data.")
            self.btn_rec.setText("●  Record")
            self.btn_stop.setEnabled(self.source is not None)


# ===================================================================== Analyze
class AnalyzeTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rec = None
        self.res = None
        self.path = None
        self._build()
        s = settings()
        for w, key in ((self.lp, "ana/lp"), (self.out_hz, "ana/out"), (self.trim, "ana/trim"),
                       (self.bparam, "ana/bparam"), (self.pre, "ana/pre"), (self.post, "ana/post")):
            v = s.value(key)
            if v is not None:
                w.setValue(float(v))
        self.ev_bl.setChecked(str(s.value("ana/ev_bl", "true")).lower() == "true")
        v = s.value("ana/baseline")
        if v and self.baseline.findText(v) >= 0:
            self.baseline.setCurrentText(v)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.recompute)

    def _build(self):
        ctrl = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(ctrl)
        cl.setContentsMargins(4, 4, 4, 4)

        g = QtWidgets.QGroupBox("Recording")
        gl = QtWidgets.QVBoxLayout(g)
        b = QtWidgets.QPushButton("Open…")
        b.clicked.connect(self.open_dialog)
        gl.addWidget(b)
        self.info = QtWidgets.QLabel("No file loaded.\n\nOpens FiberView .npz files, .meta.json\n"
                                     "(crash recovery) and old .npy files\nfrom apd_live_view.py.")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#444")
        gl.addWidget(self.info)
        cl.addWidget(g)

        g = QtWidgets.QGroupBox("Processing")
        f = QtWidgets.QFormLayout(g)
        self.lp = spin(0.1, 200, 10, 1, 1, " Hz")
        f.addRow("Low-pass", self.lp)
        self.out_hz = spin(1, 1000, 50, 10, 0, " Hz")
        self.out_hz.setToolTip("Sample rate of the processed trace and the CSV export")
        f.addRow("Output rate", self.out_hz)
        self.dark = spin(-5, 5, 0.0, 0.0001, 5, " V")
        f.addRow("Dark offset", self.dark)
        self.trim = spin(0, 3600, 0, 1, 1, " s")
        self.trim.setToolTip("Skip the start of the recording (fast initial bleaching, connecting the fiber)")
        f.addRow("Trim start", self.trim)
        self.baseline = QtWidgets.QComboBox()
        self.baseline.addItems(core.BASELINE_METHODS)
        self.baseline.setCurrentText("Double exponential")
        f.addRow("Baseline F0", self.baseline)
        self.bparam = spin(1, 600, 30, 1, 0)
        self.bparam_lab = QtWidgets.QLabel("Window (s)")
        f.addRow(self.bparam_lab, self.bparam)
        self.baseline.currentTextChanged.connect(self._baseline_changed)
        cl.addWidget(g)

        g = QtWidgets.QGroupBox("Sync-aligned average")
        f = QtWidgets.QFormLayout(g)
        self.pre = spin(0, 60, 2, 0.5, 1, " s")
        self.post = spin(0.1, 120, 5, 0.5, 1, " s")
        f.addRow("Before", self.pre)
        f.addRow("After", self.post)
        self.ev_bl = QtWidgets.QCheckBox("Subtract pre-sync mean per trial")
        self.ev_bl.setChecked(True)
        self.ev_bl.setToolTip("Each trial's ΔF/F is shifted so its mean over the 'Before' window is 0")
        self.ev_bl.toggled.connect(self._schedule)
        f.addRow(self.ev_bl)
        self.ev_info = QtWidgets.QLabel("–")
        f.addRow(self.ev_info)
        cl.addWidget(g)

        g = QtWidgets.QGroupBox("Export")
        gl = QtWidgets.QVBoxLayout(g)
        b = QtWidgets.QPushButton("Save ΔF/F as CSV…")
        b.clicked.connect(self.export_csv)
        gl.addWidget(b)
        b = QtWidgets.QPushButton("Save figure as PNG…")
        b.clicked.connect(self.export_png)
        gl.addWidget(b)
        cl.addWidget(g)
        cl.addStretch(1)

        for w in (self.lp, self.out_hz, self.dark, self.trim, self.bparam, self.pre, self.post):
            w.valueChanged.connect(self._schedule)
        self.baseline.currentTextChanged.connect(self._schedule)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(ctrl)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(290)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        self.glw = pg.GraphicsLayoutWidget()
        self.p_sig = self.glw.addPlot(row=0, col=0, colspan=2)
        self.p_sig.setLabel("left", "APD output", units="V")
        self.p_sig.showGrid(x=True, y=True, alpha=0.2)
        self.p_sig.addLegend(offset=(10, 10))
        self.p_sig.setDownsampling(auto=True, mode="peak")
        self.p_sig.setClipToView(True)
        self.c_raw = self.p_sig.plot(pen=pg.mkPen(SIG_COLOR + (90,)), name="raw")
        self.c_F = self.p_sig.plot(pen=pg.mkPen(LP_COLOR, width=2), name="F (low-pass − dark)")
        self.c_F0 = self.p_sig.plot(pen=pg.mkPen(F0_COLOR, width=2, style=QtCore.Qt.DashLine), name="F0")
        self.p_dff = self.glw.addPlot(row=1, col=0, colspan=2)
        self.p_dff.setLabel("left", "ΔF/F", units="%")
        self.p_dff.setLabel("bottom", "time", units="s")
        self.p_dff.showGrid(x=True, y=True, alpha=0.2)
        self.p_dff.setXLink(self.p_sig)
        self.c_dff = self.p_dff.plot(pen=pg.mkPen(DFF_COLOR, width=1.5))
        self.p_ev = self.glw.addPlot(row=2, col=0)
        self.p_ev.setLabel("left", "ΔF/F", units="%")
        self.p_ev.setLabel("bottom", "time from sync", units="s")
        self.p_ev.showGrid(x=True, y=True, alpha=0.2)
        self.p_ev.setTitle("Sync-aligned average")
        self.p_ev.addLine(x=0, pen=pg.mkPen((200, 40, 40), style=QtCore.Qt.DashLine))
        self.glw.ci.layout.setRowStretchFactor(0, 3)
        self.glw.ci.layout.setRowStretchFactor(1, 3)
        self.glw.ci.layout.setRowStretchFactor(2, 3)
        self.ev_items = []
        self.sync_items = []

        lay = QtWidgets.QHBoxLayout(self)
        lay.addWidget(scroll)
        lay.addWidget(self.glw, 1)
        self._baseline_changed(self.baseline.currentText())

    def save_settings(self):
        s = settings()
        s.setValue("ana/lp", self.lp.value())
        s.setValue("ana/out", self.out_hz.value())
        s.setValue("ana/trim", self.trim.value())
        s.setValue("ana/bparam", self.bparam.value())
        s.setValue("ana/pre", self.pre.value())
        s.setValue("ana/post", self.post.value())
        s.setValue("ana/baseline", self.baseline.currentText())
        s.setValue("ana/ev_bl", self.ev_bl.isChecked())

    def _baseline_changed(self, m):
        poly = m.startswith("Poly")
        roll = m.startswith("Rolling")
        self.bparam.setVisible(poly or roll)
        self.bparam_lab.setVisible(poly or roll)
        self.bparam_lab.setText("Degree" if poly else "Window (s)")
        if poly and self.bparam.value() > 6:
            self.bparam.setValue(2)
        if roll and self.bparam.value() < 5:
            self.bparam.setValue(30)

    def _schedule(self, *a):
        if self.rec is not None:
            self._timer.start()

    def open_dialog(self):
        start = settings().value("ana/last_dir", settings().value("acq/out_dir", ""))
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open recording", start, "Photometry recordings (*.npz *.npy *.meta.json);;All files (*)")
        if p:
            self.load(p)

    def load(self, path):
        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                rec = core.load_recording(path)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Open", f"Could not open the file.\n\n{e}")
            return
        self.rec, self.path = rec, path
        settings().setValue("ana/last_dir", os.path.dirname(path))
        m = rec["meta"]
        dur = rec["time"][-1] - rec["time"][0] if len(rec["time"]) > 1 else 0
        self.dark.blockSignals(True)
        self.dark.setValue(float(m.get("dark_offset_V", 0.0) or 0.0))
        self.dark.blockSignals(False)
        lines = [f"<b>{os.path.basename(path)}</b>",
                 f"{m.get('source', '?')}, {core.effective_fs(rec['time']):,.1f} Hz",
                 f"{dur / 60:.1f} min, {len(rec['signal']):,} samples"]
        if m.get("start_time"):
            lines.append(m["start_time"])
        if m.get("notes"):
            lines.append("<i>" + m["notes"].replace("\n", "<br>") + "</i>")
        self.info.setText("<br>".join(lines))
        self.info.setTextFormat(QtCore.Qt.RichText)
        # raw trace (display only, decimated for very long files)
        t, x = rec["time"], rec["signal"]
        step = max(1, len(x) // 2_000_000)
        self.c_raw.setData(t[::step], x[::step])
        self.recompute()
        self.p_sig.enableAutoRange()

    def recompute(self):
        if self.rec is None:
            return
        rec = self.rec
        try:
            res = core.compute_dff(rec["time"], rec["signal"], float("nan"), lowpass_hz=self.lp.value(),
                                   out_hz=self.out_hz.value(), dark=self.dark.value(),
                                   baseline=self.baseline.currentText(), baseline_param=self.bparam.value(),
                                   trim_s=self.trim.value())
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Processing", str(e))
            return
        self.res = res
        self.c_F.setData(res["t"], res["F"] + self.dark.value())
        self.c_F0.setData(res["t"], res["F0"] + self.dark.value())
        self.c_dff.setData(res["t"], res["dff"])
        for it in self.sync_items:
            self.p_dff.removeItem(it)
        self.sync_items = []
        ev = core.sync_edges(rec["time"], rec["sync"]) if rec["sync"] is not None else np.array([])
        ev = ev[ev >= res["t"][0]] if len(res["t"]) else ev
        self.events = ev
        if len(ev) <= 500:
            for e in ev:
                ln = pg.InfiniteLine(pos=e, angle=90, pen=pg.mkPen(SYNC_COLOR))
                self.p_dff.addItem(ln)
                self.sync_items.append(ln)
        for it in self.ev_items:
            self.p_ev.removeItem(it)
        self.ev_items = []
        lag, tr = core.event_average(res["t"], res["dff"], ev, self.pre.value(), self.post.value())
        if tr.shape[0] and self.ev_bl.isChecked() and np.any(lag < 0):
            tr = tr - tr[:, lag < 0].mean(axis=1, keepdims=True)
        self.ev_tr = (lag, tr)
        if tr.shape[0]:
            for row in tr[:200]:
                self.ev_items.append(self.p_ev.plot(lag, row, pen=pg.mkPen((150, 150, 150, 60))))
            mu = tr.mean(0)
            if tr.shape[0] > 1:
                sem = tr.std(0, ddof=1) / np.sqrt(tr.shape[0])
                lo = pg.PlotDataItem(lag, mu - sem)
                hi = pg.PlotDataItem(lag, mu + sem)
                fill = pg.FillBetweenItem(lo, hi, brush=pg.mkBrush(DFF_COLOR + (60,)))
                self.p_ev.addItem(fill)
                self.ev_items.append(fill)
            self.ev_items.append(self.p_ev.plot(lag, mu, pen=pg.mkPen(DFF_COLOR, width=2.5)))
            self.ev_info.setText(f"{tr.shape[0]} of {len(ev)} sync events averaged (mean ± SEM)")
        else:
            self.ev_info.setText("No sync events in this recording." if len(ev) == 0
                                 else f"{len(ev)} events, none with a full window.")
        self.save_settings()

    def _need(self):
        if self.res is None:
            QtWidgets.QMessageBox.information(self, "Export", "Open a recording first.")
            return False
        return True

    def export_csv(self):
        if not self._need():
            return
        base = os.path.splitext(self.path)[0].replace(".meta", "")
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save ΔF/F", base + "_dFF.csv", "CSV (*.csv)")
        if not p:
            return
        r = self.res
        hdr = (f"# {core.APP_NAME} {core.APP_VERSION} | {os.path.basename(self.path)} | low-pass "
               f"{self.lp.value()} Hz | dark {self.dark.value():.6f} V | baseline {self.baseline.currentText()}"
               f" | trim {self.trim.value()} s\ntime_s,F_V,F0_V,dFF_percent")
        np.savetxt(p, np.column_stack([r["t"], r["F"], r["F0"], r["dff"]]), delimiter=",", header=hdr,
                   comments="", fmt=["%.4f", "%.7g", "%.7g", "%.5f"])
        extra = []
        if len(self.events):
            ep = p[:-4] + "_sync_events.csv"
            np.savetxt(ep, self.events, delimiter=",", header="sync_rise_time_s", comments="", fmt="%.5f")
            extra.append(os.path.basename(ep))
            lag, tr = self.ev_tr
            if tr.shape[0]:
                ap = p[:-4] + "_sync_aligned.csv"
                np.savetxt(ap, np.column_stack([lag, tr.T]), delimiter=",", comments="", fmt="%.5f",
                           header="lag_s," + ",".join(f"event{i + 1}" for i in range(tr.shape[0])))
                extra.append(os.path.basename(ap))
        QtWidgets.QMessageBox.information(self, "Saved", "Saved " + os.path.basename(p) +
                                          ("\nand " + ", ".join(extra) if extra else ""))

    def export_png(self):
        if not self._need():
            return
        base = os.path.splitext(self.path)[0].replace(".meta", "")
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save figure", base + "_dFF.png", "PNG (*.png)")
        if p:
            self.glw.grab().save(p)


# ===================================================================== main window
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{core.APP_NAME} {core.APP_VERSION} – fiber photometry")
        self.setWindowIcon(app_icon())
        self.tabs = QtWidgets.QTabWidget()
        self.acq = AcquireTab()
        self.ana = AnalyzeTab()
        self.tabs.addTab(self.acq, "Acquire")
        self.tabs.addTab(self.ana, "Analyze")
        self.setCentralWidget(self.tabs)
        self.acq.recording_saved.connect(self._offer_open)
        self._build_menus()
        g = settings().value("win/geometry")
        if g is not None:
            self.restoreGeometry(g)
        else:
            self.resize(1300, 820)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        a = m.addAction("&Open recording…")
        a.setShortcut(QtGui.QKeySequence.Open)
        a.triggered.connect(lambda: (self.tabs.setCurrentIndex(1), self.ana.open_dialog()))
        m.addSeparator()
        a = m.addAction("&Quit")
        a.setShortcut(QtGui.QKeySequence.Quit)
        a.triggered.connect(self.close)
        m = mb.addMenu("&Help")
        a = m.addAction("Arduino sketch folder…")
        a.triggered.connect(self._open_arduino)
        a = m.addAction("Online help and downloads")
        a.triggered.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(REPO_URL)))
        m.addSeparator()
        a = m.addAction(f"&About {core.APP_NAME}")
        a.setMenuRole(QtGui.QAction.AboutRole)
        a.triggered.connect(self.about)

    def _open_arduino(self):
        d = resource("arduino", "fiberview_ads1115")
        if os.path.isdir(d):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d))
        else:
            QtWidgets.QMessageBox.information(self, "Arduino sketch",
                                              f"The sketch is in the repository:\n{REPO_URL}")

    def about(self):
        try:
            import nidaqmx.system
            drv = nidaqmx.system.System.local().driver_version
            ni = f"NI-DAQmx driver {drv.major_version}.{drv.minor_version} found"
        except Exception:
            ni = "NI-DAQmx driver not found (only needed for NI devices)"
        QtWidgets.QMessageBox.about(
            self, f"About {core.APP_NAME}",
            f"<h3>{core.APP_NAME} {core.APP_VERSION}</h3>"
            "<p>Fiber photometry acquisition and analysis: live APD signal from an NI-DAQmx device or an "
            "Arduino + ADS1115, recording, ΔF/F and sync-aligned averages.</p>"
            f"<p>{ni}.</p>"
            f"<p>© Gergely Szarka, University of Pécs – MIT license<br>"
            f"<a href='{REPO_URL}'>{REPO_URL}</a></p>")

    def _offer_open(self, path):
        self.statusBar().showMessage(f"Saved {path} – opened in the Analyze tab", 10000)
        self.ana.load(path)

    def closeEvent(self, ev):
        if self.acq.writer is not None:
            if QtWidgets.QMessageBox.question(self, "Recording", "A recording is running. Stop it, save it "
                                              "and quit?") != QtWidgets.QMessageBox.Yes:
                ev.ignore()
                return
        self.acq.stop()
        self.acq.save_settings()
        self.ana.save_settings()
        settings().setValue("win/geometry", self.saveGeometry())
        ev.accept()


def _excepthook(etype, value, tb):
    msg = "".join(traceback.format_exception(etype, value, tb))
    try:
        log = os.path.join(os.path.expanduser("~"), "fiberview_error.log")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("\n--- %Y-%m-%d %H:%M:%S\n") + msg)
    except OSError:
        pass
    sys.__stderr__ and sys.__stderr__.write(msg)
    app = QtWidgets.QApplication.instance()
    if app is not None:
        QtWidgets.QMessageBox.critical(None, "FiberView error", msg[-2000:])


def main():
    sys.excepthook = _excepthook
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PTE.FiberView")
        except Exception:
            pass
    if "--self-test" in sys.argv or "--selftest" in sys.argv:
        import fv_selftest
        sys.exit(fv_selftest.run(sys.argv))
    if "--version" in sys.argv:
        print(core.APP_VERSION)
        return
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(core.APP_NAME)
    app.setApplicationVersion(core.APP_VERSION)
    app.setWindowIcon(app_icon())
    w = MainWindow()
    w.show()
    files = [a for a in sys.argv[1:] if not a.startswith("-") and os.path.exists(a)]
    if files:
        w.tabs.setCurrentIndex(1)
        w.ana.load(files[0])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
