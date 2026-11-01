"""Spotify FM: start the companion together with the game (Steam launch option).

Steam > Cyberpunk 2077 > Properties > General > Launch options:

    "<this folder>\\.venv\\Scripts\\pythonw.exe" "<this folder>\\start_with_game.pyw" %command%

steam_launch_option.bat puts that exact line (with your paths) on the clipboard.

What it does: starts the companion hidden (spotify_fm.py --background), waits up
to 10 s for its player to load (it must load before the game goes full screen),
then starts the game exactly as Steam asked and waits for it. The companion
exits by itself shortly after the game closes.

The game ALWAYS starts, whatever happens with the companion. This file uses
only the standard library and never imports spotify_fm.py, so a companion bug
can't stop the game from launching. Log: launcher.log in this folder.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WAIT_FOR_PLAYER = 10  # seconds
# Lines the companion logs once the launcher can stop waiting.
READY_MARKERS = ("Built-in player is ready", "already running", "can't start by itself",
                 "Companion stopped by an error", "Player: Spotify app")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000  # so Steam doesn't count the companion as part of the game


def log(msg, here=HERE):
    try:
        with open(here / "launcher.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def game_command(argv, here=HERE):
    """Steam replaces %command% with the game's exe and its arguments. If it
    passed only arguments (or nothing), start the game exe next to us."""
    if argv and argv[0].lower().endswith(".exe"):
        return list(argv)
    exe = here.parents[4] / "Cyberpunk2077.exe"  # companion -> spotifyFM -> mods -> cet -> plugins -> x64
    return [str(exe)] + list(argv)


def start_companion(here=HERE, python=None, wait=None):
    wait = WAIT_FOR_PLAYER if wait is None else wait
    python = Path(python) if python else here / ".venv" / "Scripts" / "pythonw.exe"
    script = here / "spotify_fm.py"
    if not python.exists() or not script.exists():
        log(f"companion not started: {python if not python.exists() else script} is missing "
            "(double-click run_companion.bat once to set it up)", here)
        return False
    companion_log = here / "companion.log"
    try:
        before = companion_log.stat().st_size
    except OSError:
        before = 0
    cmd = [str(python), str(script), "--background"]
    base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    try:
        subprocess.Popen(cmd, cwd=str(here), creationflags=base | CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    except OSError:
        subprocess.Popen(cmd, cwd=str(here), creationflags=base, close_fds=True)  # job forbids breakaway
    log("companion started", here)
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.25)
        try:
            with open(companion_log, "rb") as f:
                f.seek(before)
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        for marker in READY_MARKERS:
            if marker in tail:
                log(f"companion says: {marker}", here)
                return True
    log(f"companion not ready after {wait} s; starting the game anyway", here)
    return True


def main(argv=None, here=HERE, python=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = game_command(argv, here)
    log("launch: " + subprocess.list2cmdline(cmd), here)
    try:
        start_companion(here, python)
    except Exception as e:  # never block the game
        log(f"companion not started: {e!r}", here)
    try:
        code = subprocess.call(cmd)
    except Exception as e:
        log(f"game failed to start: {e!r}", here)
        return 1
    log(f"game exited ({code})", here)
    return code


if __name__ == "__main__":
    sys.exit(main())
