"""start_with_game.pyw (Steam launch option): the game must ALWAYS start, the
companion is started hidden with --background first, and the game gets exactly
the command Steam passed. Uses a fake companion and a fake game (no browser,
no real game). usage: python -I test_launcher.py <start_with_game.pyw>"""
import importlib.machinery, importlib.util, json, sys, tempfile, time
from pathlib import Path

LAUNCHER = Path(sys.argv[1]).resolve()
loader = importlib.machinery.SourceFileLoader("start_with_game", str(LAUNCHER))
spec = importlib.util.spec_from_loader("start_with_game", loader)
sw = importlib.util.module_from_spec(spec)
loader.exec_module(sw)

fails = 0
def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    fails += 0 if cond else 1

root = Path(tempfile.mkdtemp(prefix="launcher_"))
here = root / "bin" / "x64" / "plugins" / "cyber_engine_tweaks" / "mods" / "spotipunkFM" / "companion"
here.mkdir(parents=True)

# --- game_command: what Steam's %command% turns into
exe = str(root / "bin" / "x64" / "Cyberpunk2077.exe")
check(sw.game_command([r"D:\g\bin\x64\Cyberpunk2077.exe", "--launcher-skip"], here) ==
      [r"D:\g\bin\x64\Cyberpunk2077.exe", "--launcher-skip"], "Steam's exe + args passed through unchanged")
check(sw.game_command([], here) == [exe], "no %command% substitution: starts Cyberpunk2077.exe next to the mod")
check(sw.game_command(["-skipStartScreen"], here) == [exe, "-skipStartScreen"], "args only: exe added in front")

# --- fake companion: records its argv, logs 'ready' after 1 s, lingers a little
(here / "spotipunk_fm.py").write_text(f"""
import sys, time, json
open({str(root / "companion_argv.json")!r}, "w").write(json.dumps(sys.argv[1:]))
time.sleep(1.0)
with open({str(here / "companion.log")!r}, "a", encoding="utf-8") as f:
    f.write("2026-11-01 12:00:00 Built-in player is ready.\\n")
time.sleep(2)
""", encoding="utf-8")
game = root / "fake_game.py"
game.write_text(f"""
import sys, json, time
open({str(root / "game_argv.json")!r}, "w").write(json.dumps({{"args": sys.argv[1:], "t": time.time()}}))
sys.exit(7)
""", encoding="utf-8")
(here / "companion.log").write_text("old line: Built-in player is ready.\n", encoding="utf-8")  # must be ignored

t0 = time.time()
rc = sw.main([sys.executable, str(game), "--launcher-skip", "-x"], here=here, python=sys.executable)
g = json.loads((root / "game_argv.json").read_text())
check(rc == 7, "launcher returns the game's exit code (%s)" % rc)
check(g["args"] == ["--launcher-skip", "-x"], "game got Steam's arguments: %s" % g["args"])
deadline = time.time() + 5
while not (root / "companion_argv.json").exists() and time.time() < deadline:
    time.sleep(0.1)
cargs = json.loads((root / "companion_argv.json").read_text()) if (root / "companion_argv.json").exists() else None
check(cargs and cargs[-1] == "--background", "companion started with --background: %s" % cargs)
waited = g["t"] - t0
check(0.9 <= waited < 6, "game started once the player was ready, not before the old log line (%.1f s)" % waited)
logtxt = (here / "launcher.log").read_text(encoding="utf-8")
check("companion says: Built-in player is ready" in logtxt and "game exited (7)" in logtxt, "launcher.log tells the story")

# --- the game still starts when the companion can't
(root / "game_argv.json").unlink()
rc = sw.main([sys.executable, str(game)], here=here, python=root / "missing" / "pythonw.exe")
check(rc == 7 and (root / "game_argv.json").exists(), "missing .venv: game still starts")
check("companion not started" in (here / "launcher.log").read_text(encoding="utf-8"), "missing .venv logged")

(root / "game_argv.json").unlink()
rc = sw.main([sys.executable, str(game)], here=here, python=root)  # a folder: Popen fails
check(rc == 7 and (root / "game_argv.json").exists(), "companion launch error: game still starts")

(root / "game_argv.json").unlink()
(here / "spotipunk_fm.py").write_text("import time; time.sleep(5)\n")  # never says ready
t0 = time.time()
sw.WAIT_FOR_PLAYER = 1
rc = sw.main([sys.executable, str(game)], here=here, python=sys.executable)
check(rc == 7 and time.time() - t0 < 5, "companion never ready: game starts after the wait (%.1f s)" % (time.time() - t0))

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
