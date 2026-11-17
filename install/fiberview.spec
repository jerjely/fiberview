# PyInstaller recipe for FiberView (Windows, macOS, Linux).
#   python -m PyInstaller --noconfirm --distpath dist --workpath build install/fiberview.spec
# Windows/Linux: dist/FiberView/FiberView(.exe)    macOS: dist/FiberView.app
# The NI-DAQmx *driver* is not bundled (it cannot be redistributed and needs NI's installer);
# the nidaqmx Python package is, so NI devices work as soon as the driver is installed.
import os
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = os.environ.get("FIBERVIEW_VERSION_NUMERIC", "0.0.0")
A = lambda *p: os.path.join(ROOT, *p)

datas = [
    (A("assets", "fiberview_256.png"), "assets"),
    (A("assets", "fiberview_512.png"), "assets"),
    (A("assets", "fiberview.ico"), "assets"),
    (A("arduino", "fiberview_ads1115", "fiberview_ads1115.ino"), os.path.join("arduino", "fiberview_ads1115")),
    (A("arduino", "README.md"), "arduino"),
]
# nidaqmx and its dependencies (nitypes, hightime, ...) read their own package metadata at import time
datas += copy_metadata("nidaqmx", recursive=True)
for pkg in ("pyqtgraph", "numpy", "scipy", "pyserial"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

hiddenimports = ["fiberview", "fv_core", "fv_sources", "fv_selftest", "fv_version",
                 "serial", "serial.tools.list_ports"]
hiddenimports += collect_submodules("nidaqmx", filter=lambda m: "grpc" not in m and "stubs" not in m)

a = Analysis(
    [A("fiberview.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "PyQt5", "PyQt6", "pandas", "grpc"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = A("assets", "fiberview.icns") if sys.platform == "darwin" else A("assets", "fiberview.ico")
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FiberView",
    console=False,            # GUI app; the self-test writes its result to a file and the exit code
    icon=icon,
    codesign_identity=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="FiberView")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="FiberView.app",
        icon=icon,
        bundle_identifier="hu.pte.fiberview",
        version=VERSION,
        info_plist={
            "CFBundleName": "FiberView",
            "CFBundleDisplayName": "FiberView",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "© Gergely Szarka, University of Pécs – MIT license",
        },
    )
