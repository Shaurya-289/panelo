# -*- mode: python ; coding: utf-8 -*-
# Build:  uv run pyinstaller packaging/panelo.spec --noconfirm --clean --distpath dist --workpath build
import os
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

ROOT = Path(SPECPATH).resolve().parent          # SPECPATH = .../packaging
SRC = ROOT / "src"
PKG = SRC / "panelo"
ICON = PKG / "ui" / "icons" / "app-icon.ico"

with (ROOT / "pyproject.toml").open("rb") as fh:
    VERSION = tomllib.load(fh)["project"]["version"]
VERSION_TUPLE = tuple(([int(p) for p in VERSION.split(".")] + [0, 0, 0, 0])[:4])


def tree(src: Path, dest: str) -> list[tuple[str, str]]:
    out = []
    for path in sorted(src.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
            out.append((str(path), str(Path(dest) / path.relative_to(src).parent)))
    return out


datas = (
    tree(PKG / "ui" / "qml", "panelo/ui/qml")
    + tree(PKG / "ui" / "fonts", "panelo/ui/fonts")
    + tree(PKG / "ui" / "icons", "panelo/ui/icons")
    # Alembic loads env.py and versions/*.py from disk by path: ship them as source.
    + tree(PKG / "db" / "migrations", "panelo/db/migrations")
    + collect_data_files("tzdata")
)

hiddenimports = (
    collect_submodules("panelo", filter=lambda n: not n.startswith("panelo.db.migrations"))
    + collect_submodules("alembic", filter=lambda n: not n.startswith("alembic.testing"))
    + collect_submodules("tzdata")
    + [
        "logging.config", "mako.template",
        "sqlalchemy.dialects.sqlite", "sqlalchemy.dialects.sqlite.pysqlite",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtSvg", "PySide6.QtMultimedia",
        "PySide6.QtNetwork", "PySide6.QtTest",
    ]
)

EXCLUDES = [
    "tkinter", "_tkinter", "pytest", "_pytest", "mypy", "coverage", "pydoc_data",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView", "PySide6.QtHttpServer",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets",
    "PySide6.QtQuick3D", "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtSensors",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtUiTools", "PySide6.QtSql", "PySide6.QtSvgWidgets", "PySide6.QtQuickWidgets",
    "PySide6.QtAxContainer", "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtXml",
    "PySide6.QtPrintSupport", "PySide6.QtOpenGLWidgets",
]

a = Analysis(
    [str(ROOT / "packaging" / "panelo_launcher.py")],
    pathex=[str(SRC)], binaries=[], datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=EXCLUDES,
    noarchive=False, optimize=0,
)

# PyInstaller's QtQml hook collects the entire PySide6/qml tree: trim it.
# Keep QtQml, QtQuick (Window/Effects/Shapes/Dialogs) and what Dialogs needs (QtQuick/Controls, Templates, Layouts).
QML_DROP = {
    "qt3d", "qtquick3d", "qtwebengine", "qtwebview", "qtwebchannel", "qtwebsockets",
    "qtcharts", "qtdatavisualization", "qtgraphs", "qtlocation", "qtpositioning", "qtsensors",
    "qtscxml", "qtremoteobjects", "qttexttospeech", "qtmultimedia", "qtspatialaudio", "qttest",
    "qtinsighttracker", "qtquick/virtualkeyboard", "qtquick/pdf", "qtquick/scene3d",
    "qtquick/scene2d", "qtquick/localstorage", "qtqml/statemachine",
}
PLUGIN_DROP = {
    "position", "sensors", "geoservices", "sqldrivers", "designer", "webview", "texttospeech",
    "canbus", "scxmldatamodel", "qmltooling", "sceneparsers", "geometryloaders",
    "renderplugins", "renderers", "assetimporters", "virtualkeyboard", "printsupport",
}
BIN_DROP_PREFIXES = (
    "qt6webengine", "qtwebengineprocess", "qt6pdf", "qt63d", "qt6quick3d", "qt6charts",
    "qt6datavisualization", "qt6graphs", "qt6location", "qt6positioning", "qt6sensors",
    "qt6scxml", "qt6statemachine", "qt6remoteobjects", "qt6texttospeech", "qt6spatialaudio",
    "qt6webchannel", "qt6websockets", "qt6webview", "qt6virtualkeyboard", "qt6bluetooth",
    "qt6nfc", "qt6serialport", "qt6serialbus", "qt6designer", "qt6help", "qt6sql",
    "qt6uitools", "qt6httpserver", "opengl32sw",
)


def _keep(entry) -> bool:
    dest = entry[0].replace("\\", "/").lower()
    if dest.startswith("pyside6/qml/"):
        rel = dest[len("pyside6/qml/"):]
        if any(rel == m or rel.startswith(m + "/") for m in QML_DROP):
            return False
    if dest.startswith("pyside6/plugins/"):
        if dest[len("pyside6/plugins/"):].split("/", 1)[0] in PLUGIN_DROP:
            return False
    if dest.startswith(("pyside6/translations/", "pyside6/resources/qtwebengine")):
        return False
    return not dest.rsplit("/", 1)[-1].startswith(BIN_DROP_PREFIXES)


a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]

VERSION_FILE = os.path.join(workpath, "panelo_version_info.txt")
os.makedirs(workpath, exist_ok=True)
Path(VERSION_FILE).write_text(str(VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE, mask=0x3F, flags=0x0,
                      OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Shaurya"),
            StringStruct("FileDescription", "Panelo"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "Panelo"),
            StringStruct("LegalCopyright", "Copyright (c) 2026 Shaurya. MIT License."),
            StringStruct("OriginalFilename", "Panelo.exe"),
            StringStruct("ProductName", "Panelo"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)), encoding="utf-8")

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Panelo", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=[str(ICON)],
    version=VERSION_FILE, contents_directory="_internal",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="Panelo")
