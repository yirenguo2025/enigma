"""Build script: produce a single-file executable using PyInstaller.

Usage (after `pip install -r requirements.txt pyinstaller`):
    python build/build.py

Output: dist/Enigma  (or dist/Enigma.exe on Windows, dist/Enigma.app on Mac)
"""

import os
import platform
import shutil
import subprocess
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "Enigma",
        "--noconfirm",
        "--clean",
        "--windowed",  # no terminal window
        "--onefile",
        "main.py",
    ]
    if platform.system() == "Darwin":
        # Bundle as .app
        pass  # --windowed already does this on Mac
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)
    print("\nBuild complete. Look in:", os.path.join(ROOT, "dist"))


if __name__ == "__main__":
    main()
