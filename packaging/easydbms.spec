# PyInstaller spec: ``pyinstaller --noconfirm --clean packaging/easydbms.spec`` (or scripts/build_app.py).
# Builds a windowed one-folder app (dist/EasyDBMS/) and, on macOS, dist/EasyDBMS.app.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / ("icon.ico" if sys.platform == "win32" else "icon.icns" if sys.platform == "darwin" else "icon.png")

# modules loaded by name at run time, which PyInstaller's import scan cannot see
hiddenimports = [
    *collect_submodules("sqlglot.dialects"),
    *collect_submodules("keyring.backends"),
    "psycopg_binary",
    "pymysql",
    "paramiko",
    "cryptography.hazmat.primitives.asymmetric.ec",
    # cloud providers are optional: bundled when their SDK is installed in the build environment
    "boto3",
    "botocore",
    "azure.identity",
    "google.auth",
]
hiddenimports = [m for m in hiddenimports if m.split(".")[0] not in ("boto3", "botocore", "azure", "google") or __import__("importlib.util").util.find_spec(m.split(".")[0])]

datas = [
    (str(ROOT / "easydbms" / "py.typed"), "easydbms"),
    (str(ROOT / "packaging" / "icon.png"), "packaging"),  # the window icon, see ui/icons.py
    *collect_data_files("sqlglot"),
]
binaries = []
# the optional C accelerator, if it was built
for native in (ROOT / "easydbms" / "core" / "simd").glob("_native*"):
    if native.suffix in (".so", ".pyd", ".dylib"):
        binaries.append((str(native), "easydbms/core/simd"))

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff", "benchmarks", "tests", "pandas", "sqlalchemy", "matplotlib", "IPython", "duckdb", "chdb"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EasyDBMS",
    console=False,
    icon=str(ICON) if ICON.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="EasyDBMS")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="EasyDBMS.app",
        icon=str(ICON) if ICON.exists() else None,
        bundle_identifier="app.easydbms",
        info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "0.7.0"},
    )
