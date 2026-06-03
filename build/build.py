"""Build script: produce a versioned executable using PyInstaller.

Usage (after `pip install -r requirements.txt pyinstaller`):
    python build/build.py

Output:
    macOS:   dist/Enigma-v{VERSION}.app
    Windows: dist/Enigma-v{VERSION}.exe
    Linux:   dist/Enigma-v{VERSION}
"""

import os
import platform
import subprocess
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_version() -> str:
    """Read version from core/version.py without importing project deps."""
    ns: dict = {}
    with open(os.path.join(ROOT, "core", "version.py")) as f:
        exec(f.read(), ns)
    return ns["__version__"]


def main():
    version = get_version()
    name = f"Enigma-v{version}"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        name,
        "--noconfirm",
        "--clean",
        "--windowed",  # no terminal window; .app bundle on macOS
        "--onefile",
        "main.py",
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)
    print(f"\nBuild complete (v{version}). Output in: {os.path.join(ROOT, 'dist')}")


if __name__ == "__main__":
    main()
