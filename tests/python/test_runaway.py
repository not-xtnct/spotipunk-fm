"""Regression tests for the runaway relaunch loop (sound test opened dozens of windows)."""
import importlib.util, json, subprocess, sys, tempfile, threading, time
from pathlib import Path

COMPANION, CHROME = Path(sys.argv[1]), sys.argv[2]
spec = importlib.util.spec_from_file_location("spotipunk_fm", COMPANION)
fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
tmp = Path(tempfile.mkdtemp())
fm.LOG_PATH = tmp / "log"; fm.PLAYER_PROFILE_DIR = tmp / ".player-profile"
fails = 0
def check(c, m):
    global fails; print(("ok   " if c else "FAIL ") + m); fails += 0 if c else 1

class SP:  # minimal Spotify stand-in for the token endpoint
    def access_token(self): return "AT"

# Fake SDK served from data: so pages become "ready" (exercises page heartbeat)
fm.SDK_URL = "data:text/javascript," + __import__("urllib.parse").parse.quote(
    "window.Spotify={Player:class{constructor(o){this.o=o;this.l={}}addListener(e,f){this.l[e]=f}"
    "connect(){setTimeout(()=>this.l.ready({device_id:'d1'}),200);return Promise.resolve(true)}"
    "setVolume(){return Promise.resolve()}getVolume(){return Promise.resolve(0)}getCurrentState(){return Promise.resolve(null)}"
    "activateElement(){return Promise.resolve()}resume(){return Promise.resolve()}}};window.onSpotifyWebPlaybackSDKReady();")

wrapper = tmp / "browser.sh"
wrapper.write_text("#!/bin/sh\nexec %s --headless=new --no-sandbox \"$@\"\n" % CHROME); wrapper.chmod(0o755)
cfg = dict(fm.DEFAULT_CONFIG, browser_path=str(wrapper))

def launches_in_log():
    return open(fm.LOG_PATH).read().count("Starting the built-in player")

# --- 1. leftover player browser from an earlier companion (the real-life trigger)
fm.PLAYER_PROFILE_DIR.mkdir()
leftover = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", f"--user-data-dir={fm.PLAYER_PROFILE_DIR}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)
check(len(fm.profile_processes(fm.PLAYER_PROFILE_DIR)) > 0, "leftover player browser is detected by its profile folder")
p = fm.BrowserPlayer(cfg, SP(), 18891, hide=lambda t: 0)
p.start()
for _ in range(40):  # 20 s of the companion's maintain loop
    p.maintain(); time.sleep(0.5)
check(leftover.poll() is not None, "leftover browser was closed before launching")
check(p.ready and p.device_id == "d1", "player came up normally")
check(launches_in_log() == 1, "launched exactly once (log shows %d)" % launches_in_log())

# --- 2. two companions at once: second must refuse the port
try:
    fm.BrowserPlayer(cfg, SP(), 18891, hide=lambda t: 0)
    check(False, "second companion on the same port refused")
except SystemExit:
    check(True, "second companion on the same port refused")
p.shutdown()
time.sleep(1)
check(fm.profile_processes(fm.PLAYER_PROFILE_DIR) == [], "shutdown closed every player process")

# --- 3. browser hands over to an existing copy and exits at once, and cleanup is impossible
open(fm.LOG_PATH, "w").close()
leftover = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", f"--user-data-dir={fm.PLAYER_PROFILE_DIR}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)
real_kill = fm.kill_profile_processes
fm.kill_profile_processes = lambda d: 0
p = fm.BrowserPlayer(cfg, SP(), 18892, hide=lambda t: 0)
p.start()
time.sleep(1)
check(p.proc.poll() is not None, "our launched browser exited immediately (hand-off reproduced)")
for _ in range(60):  # 30 s
    p.maintain(); time.sleep(0.5)
check(launches_in_log() <= 2, "no relaunch storm after hand-off (%d launches in 30 s)" % launches_in_log())
fm.kill_profile_processes = real_kill
p.shutdown(); leftover.kill()

# --- 4. player that never comes up: strict budget, then give up (fake clock, 30 simulated minutes)
open(fm.LOG_PATH, "w").close()
class Dead:
    args = []
    def poll(self): return 1
    def terminate(self): pass
    def wait(self, timeout=None): return 1
    def kill(self): pass
clock = [1000.0]
launched = []
p = fm.BrowserPlayer(cfg, SP(), 18893, launch=lambda url: (launched.append(clock[0]), Dead())[1],
                     hide=lambda t: 0, clock=lambda: clock[0])
p.start()
for _ in range(3600):
    clock[0] += 0.5; p.maintain()
gaps = [b - a for a, b in zip(launched, launched[1:])]
check(len(launched) <= 3, "at most 3 launches in 30 simulated minutes (got %d)" % len(launched))
check(all(g >= 20 for g in gaps), "launches at least 20 s apart (gaps %s)" % gaps)
check(p.gave_up and "Restart the companion" in p.error, "gives up with a clear message")
p.shutdown()

print("\n%d failure(s)" % fails); sys.exit(1 if fails else 0)
