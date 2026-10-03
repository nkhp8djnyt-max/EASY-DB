"""Build the desktop application with PyInstaller.

    python scripts/build_app.py            # dist/EasyDBMS/ (and EasyDBMS.app on macOS)
    python scripts/build_app.py --check    # also start the result with --version

Needs ``pip install -e ".[dev,build]"`` (add ``cloud`` to bundle the AWS / Azure / Google SDKs).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def executable() -> Path:
    folder = ROOT / "dist" / "EasyDBMS"
    return folder / ("EasyDBMS.exe" if sys.platform == "win32" else "EasyDBMS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="run the built app with --version")
    args = parser.parse_args()
    if not (ROOT / "packaging" / "icon.png").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_icon.py")], check=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "packaging/easydbms.spec"],
        cwd=ROOT,
        check=True,
    )
    built = executable()
    if not built.exists():
        print(f"expected {built} was not produced", file=sys.stderr)
        return 1
    if args.check:
        done = subprocess.run(
            [str(built), "--version"], capture_output=True, text=True, timeout=120
        )
        print(done.stdout.strip() or done.stderr.strip())
        return done.returncode
    print(f"built {built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
