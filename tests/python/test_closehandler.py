"""Windows only: closing the companion's console window (X) must still run cleanup.

Starts a child Python in its own console that installs install_close_handler()
with a cleanup that writes a marker file, then sends that console window
WM_CLOSE (what clicking X does) and checks the marker. No browser is started.
usage: python -I test_closehandler.py <spotify_fm.py>"""
import ctypes, os, subprocess, sys, tempfile, time
from pathlib import Path

if sys.platform != "win32":
    print("skipped (Windows only)")
    sys.exit(0)

COMPANION = Path(sys.argv[1]).resolve()
tmp = Path(tempfile.mkdtemp(prefix="closehandler_"))
hwnd_file, marker, ready = tmp / "hwnd.txt", tmp / "cleaned.txt", tmp / "ready.txt"
child = tmp / "child.py"
child.write_text(f"""
import ctypes, importlib.util, sys, time
spec = importlib.util.spec_from_file_location("spotify_fm", {str(COMPANION)!r})
fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
fm.LOG_PATH = __import__("pathlib").Path({str(tmp / "child.log")!r})
def cleanup():
    time.sleep(0.5)  # cleanup takes a moment, like killing the player
    open({str(marker)!r}, "w").write("cleaned")
ok = fm.install_close_handler(cleanup)
open({str(hwnd_file)!r}, "w").write(str(ctypes.windll.kernel32.GetConsoleWindow()))
open({str(ready)!r}, "w").write(str(ok))
while True:
    time.sleep(0.2)
""", encoding="utf-8")

fails = 0
def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    fails += 0 if cond else 1

# conhost.exe forces a classic console window even if Windows Terminal is the default terminal.
conhost = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "conhost.exe"
cmd = [str(conhost), sys.executable, "-I", "-B", str(child)] if conhost.exists() else [sys.executable, "-I", "-B", str(child)]
proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
try:
    deadline = time.time() + 20
    while not ready.exists() and time.time() < deadline:
        time.sleep(0.1)
    check(ready.exists() and ready.read_text() == "True", "handler installed in child console")
    hwnd = int(hwnd_file.read_text()) if hwnd_file.exists() else 0
    check(hwnd != 0, "child has a console window (hwnd %d)" % hwnd)
    if hwnd:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE = clicking X
        deadline = time.time() + 10
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.1)
        check(marker.exists(), "cleanup ran when the console window was closed")
        try:
            proc.wait(timeout=10)
            exited = True
        except subprocess.TimeoutExpired:
            exited = False
        check(exited, "child process ended after the close")
finally:
    if proc.poll() is None:
        proc.kill()
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
