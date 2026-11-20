"""One command setup: check Python and Tk, build .venv, install, test, open the GUI."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def fail(message, hint):
    """Print an error with a fix hint and exit with code 1."""
    print(f"error: {message}")
    print(f"fix: {hint}")
    sys.exit(1)


def must(cmd, message, hint):
    """Run a command. Call fail with the hint if it exits with a non-zero code."""
    if subprocess.run(cmd, check=False).returncode != 0:
        fail(message, hint)


def main():
    """Run the six steps in order."""
    parser = argparse.ArgumentParser(description="Set up the app and open the GUI.")
    parser.add_argument("--no-gui", action="store_true", help="set up and test only")
    parser.add_argument("--no-tests", action="store_true", help="skip the test run")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # absent in some IDE consoles
        sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with child output
    os.chdir(ROOT)
    python = str(VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))

    print("[1/6] Python version")
    if sys.version_info < (3, 9):
        found = ".".join(str(n) for n in sys.version_info[:3])
        fail(f"found Python {found}", "install Python 3.9 or newer")
    print("ok")

    print("[2/6] Tk for the GUI")
    if subprocess.run([sys.executable, "-c", "import tkinter"], check=False).returncode == 0:
        print("ok")
    else:
        print("Tk is missing. Install it:")
        print("  Debian/Ubuntu/Kali: sudo apt install python3-tk")
        print("  Fedora: sudo dnf install python3-tkinter")
        print("  macOS: use the python.org installer, it includes Tk")
        print("  Windows: re-run the Python installer and tick tcl/tk")
        if not args.no_gui:
            sys.exit(1)
        print("warning: continuing because of --no-gui")

    print("[3/6] Virtual environment")
    if Path(python).exists():
        print("exists, reused")
    else:
        must([sys.executable, "-m", "venv", ".venv"], "could not create .venv",
             "on Debian/Ubuntu/Kali run: sudo apt install python3-venv, "
             "then delete the .venv folder and run this script again")
        print("ok")

    print("[4/6] Install requirements")
    must([python, "-m", "pip", "install", "--disable-pip-version-check",
          "-r", "requirements.txt"], "pip install failed",
         "check the network, or delete the .venv folder and run this script again")
    print("ok")

    print("[5/6] Verify and test")
    must([python, "-c", "import numpy, scipy; print('numpy', numpy.__version__, 'scipy', scipy.__version__)"],
         "a package does not import", "delete .venv and run this script again")
    tests_ok = True
    if args.no_tests:
        print("tests skipped")
    else:
        for name in ("tests/test_app.py", "tests/test_wtx.py"):
            result = subprocess.run([python, name], capture_output=True, text=True, check=False)
            lines = (result.stdout + result.stderr).strip().splitlines()
            tail = lines[-1] if lines else "no output"
            ok = result.returncode == 0
            print(f"  {name}: {'ok' if ok else 'FAILED'} ({tail})")
            tests_ok = tests_ok and ok
        print("ok" if tests_ok else "tests failed, the GUI may still run")

    print("[6/6] Launch")
    if os.name == "nt":
        print("Later, activate with: .venv\\Scripts\\activate")
    else:
        print("Later, activate with: source .venv/bin/activate")
    print("Then run: python -m wtxapp")
    gui_code = 0
    if args.no_gui:
        print("skipped (--no-gui)")
    else:
        print("starting the GUI, close its window to finish")
        gui_code = subprocess.call([python, "-m", "wtxapp"])
    sys.exit(0 if tests_ok and gui_code == 0 else 1)


if __name__ == "__main__":
    main()
