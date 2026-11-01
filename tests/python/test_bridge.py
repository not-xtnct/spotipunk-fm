"""End-to-end test of the companion against a fake Spotify Web API."""
import http.server
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

COMPANION = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("spotify_fm", COMPANION)
fm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fm)

# ---------------------------------------------------------------- fake Spotify
S = {
    "devices": [],
    "player": None,  # dict or None
    "calls": [],
    "volume": 80,
    "premium_error": False,
    "token_calls": 0,
}
PLAYLISTS = [{"name": "Night Drive", "uri": "spotify:playlist:aaa", "items": {"total": 40}},
             {"name": "Gym", "uri": "spotify:playlist:bbb", "tracks": {"total": 10}}]


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if u.path == "/api/token":
            S["token_calls"] += 1
            return self._send(200, {"access_token": "AT%d" % S["token_calls"], "expires_in": 3600})
        if self.headers.get("Authorization", "").startswith("Bearer AT") is False:
            return self._send(401, {"error": {"status": 401, "message": "expired"}})
        body = json.loads(raw) if raw else None
        S["calls"].append((method, u.path, q, body))
        p = u.path.replace("/v1", "", 1)
        if p == "/me":
            return self._send(200, {"id": "user1", "product": "premium"})
        if p == "/me/player/devices":
            return self._send(200, {"devices": S["devices"]})
        if p == "/me/player" and method == "GET":
            if not S["player"]:
                return self._send(204)
            pl = dict(S["player"])
            pl["device"] = dict(pl["device"], volume_percent=S["volume"])
            return self._send(200, pl)
        if p == "/me/playlists":
            return self._send(200, {"items": PLAYLISTS, "next": None})
        if p == "/me/player/play":
            if S["premium_error"]:
                return self._send(403, {"error": {"status": 403, "message": "Player command failed: Premium required", "reason": "PREMIUM_REQUIRED"}})
            dev = next(d for d in S["devices"] if d["id"] == q.get("device_id")) if q.get("device_id") else S["player"]["device"]
            ctx = (body or {}).get("context_uri") or ((S["player"] or {}).get("context") or {}).get("uri")
            S["player"] = {"is_playing": True, "device": dev, "context": {"uri": ctx} if ctx else None,
                           "shuffle_state": False,
                           "item": {"name": "Resonance", "artists": [{"name": "HOME"}], "album": {"name": "Odyssey"}}}
            return self._send(204)
        if p == "/me/player/pause":
            if not S["player"] or not S["player"]["is_playing"]:
                return self._send(403, {"error": {"status": 403, "message": "Restriction violated"}})
            S["player"]["is_playing"] = False
            return self._send(204)
        if p == "/me/player/volume":
            S["volume"] = int(q["volume_percent"])
            return self._send(204)
        if p in ("/me/player/next", "/me/player/previous", "/me/player/shuffle"):
            if p.endswith("shuffle") and S["player"]:
                S["player"]["shuffle_state"] = q["state"] == "true"
            return self._send(204)
        return self._send(404, {"error": {"status": 404, "message": "not found " + p}})

    def do_GET(self): self._route("GET")
    def do_PUT(self): self._route("PUT")
    def do_POST(self): self._route("POST")


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]

# ---------------------------------------------------------------- setup
tmp = Path(tempfile.mkdtemp())
mod_dir = tmp / "mods" / "spotifyFM"
mod_dir.mkdir(parents=True)
(mod_dir / "init.lua").write_text("-- stub")
fm.CONFIG_PATH = tmp / "config.json"
fm.LOG_PATH = tmp / "companion.log"
tokens = tmp / "tokens.json"
tokens.write_text(json.dumps({"access_token": "stale", "expires_at": 0, "refresh_token": "R", "client_id": "c" * 32}))

cfg = fm.load_config()
cfg.update(player="app", client_id="c" * 32, playlist_uri="spotify:playlist:aaa", playlist_name="Night Drive", shuffle=True)
sp = fm.Spotify(cfg["client_id"], 8898, tokens_path=tokens, api_url=base + "/v1", accounts_url=base)


class Clock:
    t = 1_900_000_000.0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s


clock = Clock()
launched = []
def launcher():
    launched.append(1)
    S["devices"] = [{"id": "pc1", "name": "MY-PC", "type": "Computer", "is_active": False, "is_restricted": False}]

mixer = fm.WindowsMixer()
mixer.ok = False
bridge = fm.Bridge(cfg, sp, mod_dir, mixer=mixer, clock=clock, sleep=clock.sleep, launcher=launcher)

fails = 0
def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1

session = 111
seq = 0
commands = []
def game(mode, reason="", fresh=True, cmds=None, sess=None):
    st = {"v": 1, "session": sess or session, "ts": int(clock.t if fresh else clock.t - 60), "hb": 1,
          "mode": mode, "reason": reason, "duckVolume": 0.3, "commands": cmds if cmds is not None else commands}
    (mod_dir / "state.json").write_text(json.dumps(st))
    os.utime(mod_dir / "state.json", (clock.t if fresh else clock.t - 60,) * 2)
    bridge.tick()

def calls_since(n):
    return [(m, p.replace("/v1", "")) for m, p, q, b in S["calls"][n:]]

# ---------------------------------------------------------------- scenarios
game("off", "not tuned")
check(bridge.game_running and len(bridge.playlists) == 2, "game detected, playlists loaded")
check(S["token_calls"] == 1, "expired token refreshed automatically")

n = len(S["calls"])
game("play")
c = [x for x in S["calls"][n:] if x[1].endswith("/play")]
check(launched == [1], "Spotify launched when no device was found")
check(c and c[0][3]["context_uri"] == "spotify:playlist:aaa" and c[0][2]["device_id"] == "pc1", "tune in starts chosen playlist on this PC")
check(0 <= c[0][3]["offset"]["position"] < 40, "shuffle starts at a random track")
check(("PUT", "/me/player/shuffle") in calls_since(n), "shuffle enabled")
check(S["player"]["is_playing"] and bridge.we_control, "playing and controlled")

game("duck", "dialogue")
check(S["volume"] == 24, "dialogue ducks volume 80 -> 24 (got %d)" % S["volume"])
game("play")
check(S["volume"] == 80, "volume restored after dialogue")

game("pause", "scene")
check(not S["player"]["is_playing"], "cutscene pauses")
n = len(S["calls"])
game("play")
plays = [x for x in S["calls"][n:] if x[1].endswith("/play")]
check(plays and plays[0][3] is None, "after cutscene: plain resume, playlist not restarted")
check(S["player"]["is_playing"], "playing again")

game("duck", "holocall")
game("pause", "scene")
check(not S["player"]["is_playing"] and S["volume"] == 80, "duck -> pause: paused and volume restored")
game("play")

# commands
commands = [{"seq": 1, "action": "next"}]
n = len(S["calls"])
game("play")
check(("POST", "/me/player/next") in calls_since(n), "next-track command from game")
game("play")
check(calls_since(n).count(("POST", "/me/player/next")) == 1, "command not repeated")
commands = [{"seq": 1, "action": "next"}, {"seq": 2, "action": "set_playlist", "arg": "spotify:playlist:bbb"}]
n = len(S["calls"])
game("play")
plays = [x for x in S["calls"][n:] if x[1].endswith("/play")]
check(plays and plays[0][3]["context_uri"] == "spotify:playlist:bbb", "playlist switch from overlay starts new playlist")
check(json.loads(fm.CONFIG_PATH.read_text())["playlist_uri"] == "spotify:playlist:bbb", "playlist choice saved")

# new game session must not replay old commands
n = len(S["calls"])
game("play", cmds=[{"seq": 5, "action": "next"}], sess=222)
check(("POST", "/me/player/next") not in calls_since(n), "old commands ignored on new session")

# now playing file
bridge.last_np_write = 0
game("play")
np = json.loads((mod_dir / "now_playing.json").read_text())
check(np["track"] == "Resonance" and np["artist"] == "HOME" and np["playlist_name"] == "Gym", "now_playing.json has track and playlist")
check(np["playlists"][0]["uri"] == "liked" and len(np["playlists"]) == 3, "playlist list includes Liked Songs")

# game closes -> pause
game("play", fresh=False)
check(not bridge.game_running and not S["player"]["is_playing"], "game closed (stale heartbeat) pauses Spotify")

# user plays Spotify themselves while station is off: we don't interfere
S["player"]["is_playing"] = True
n = len(S["calls"])
game("off", "not tuned", sess=333)
check(("PUT", "/me/player/pause") not in calls_since(n), "no pause when already 'off' (user's own listening untouched)")

# Premium error is reported clearly and retried later
S["premium_error"] = True
S["player"]["context"] = {"uri": "spotify:playlist:zzz"}
game("play", sess=333)
check("Premium" in bridge.error, "Premium error reported: " + bridge.error)
check(bridge.retry_at is not None, "retry scheduled")
S["premium_error"] = False
clock.t += 6
game("play", sess=333)
check(S["player"]["is_playing"] and bridge.retry_at is None, "retry succeeds once allowed")

# ---------------------------------------------------------------- network failures (v1.1.6 regression)
# v1.1.5 died in game when a reply timed out: urllib only wraps connection-setup
# errors in URLError, so TimeoutError / RemoteDisconnected escaped the main loop.
import socket as _socket
import urllib.error as _uerr

def raises(fn):
    try:
        fn()
    except Exception as e:
        return e
    return None

# a server that accepts the connection and hangs up without answering -> real RemoteDisconnected
dead = _socket.socket()
dead.bind(("127.0.0.1", 0))
dead.listen(8)
def hang_up():
    while True:
        try:
            c, _ = dead.accept()
        except OSError:
            return
        try:
            c.recv(65536)
        except OSError:
            pass
        c.close()
threading.Thread(target=hang_up, daemon=True).start()
sp_dead = fm.Spotify(cfg["client_id"], 8898, tokens_path=tokens,
                     api_url="http://127.0.0.1:%d/v1" % dead.getsockname()[1], accounts_url=base)
sp_dead.tokens.update(access_token="AT1", expires_at=clock.t + 10 ** 9)
e = raises(lambda: sp_dead.request("GET", "/me/player"))
check(isinstance(e, fm.SpotifyError) and e.status == 0 and "network error" in e.message,
      "dropped connection becomes SpotifyError (got %r)" % (e,))

real_urlopen = fm.urllib.request.urlopen
def failing(exc):
    def urlopen(*a, **k):
        raise exc
    return urlopen

fm.urllib.request.urlopen = failing(TimeoutError("timed out"))
e = raises(lambda: sp.request("GET", "/me/player"))
check(isinstance(e, fm.SpotifyError) and "timed out" in e.message, "read timeout becomes SpotifyError (got %r)" % (e,))
sp.tokens["expires_at"] = 0
fm.urllib.request.urlopen = failing(_uerr.URLError("no internet"))
e = raises(sp.access_token)
check(isinstance(e, fm.SpotifyError) and "network error" in e.message, "token refresh offline becomes SpotifyError (got %r)" % (e,))

# the bridge rides out a timeout during play and recovers when the network is back
S["player"]["is_playing"] = False
bridge.mode = "off"
fm.urllib.request.urlopen = failing(TimeoutError("timed out"))
e = raises(lambda: game("play", sess=333))
check(e is None, "tick survives a Spotify timeout (got %r)" % (e,))
check("network error" in bridge.error and bridge.retry_at is not None, "timeout reported and retry scheduled: " + bridge.error)

# v1.1.8: while the network stays down, polls back off instead of retrying every tick
# (each try can block the loop for API_TIMEOUT seconds)
attempts = []
def counting_timeout(req, *a, **k):
    attempts.append(req.full_url if hasattr(req, "full_url") else str(req))
    raise TimeoutError("timed out")
fm.urllib.request.urlopen = counting_timeout
sp.tokens.update(access_token="AT1", expires_at=clock.t + 10 ** 6)
bridge.retry_at = None
bridge.last_player_poll = 0      # both polls due right now
bridge.last_playlist_poll = 0
for _ in range(80):  # 20 s of 0.25 s ticks, mode unchanged
    game("play", sess=333)
    clock.t += 0.25
polls = [u for u in attempts if u.split("?")[0].endswith("/me/player")]
lists = [u for u in attempts if "/me/playlists" in u]
check(len(polls) == 1, "now-playing poll tried once, then backs off for 30 s (%d tries in 20 s)" % len(polls))
check(len(lists) == 1, "playlist refresh tried once, then backs off for 60 s (%d tries in 20 s)" % len(lists))
check(fm.API_TIMEOUT <= 10, "API timeout is short (%ss)" % fm.API_TIMEOUT)
bridge.retry_at = clock.t  # in real use apply()'s 5 s play retry is still pending (cleared above to count polls)

fm.urllib.request.urlopen = real_urlopen
clock.t += 6
game("play", sess=333)
check(S["player"]["is_playing"], "playback recovers once the network is back")

# run(): an unexpected bug in one tick is logged with its traceback and the loop keeps going
ticks = []
def flaky_tick():
    ticks.append(1)
    if len(ticks) == 1:
        raise ValueError("boom in tick")
    if len(ticks) == 3:
        raise KeyboardInterrupt
bridge.tick = flaky_tick
e = raises(bridge.run)
del bridge.tick
logtext = fm.LOG_PATH.read_text(encoding="utf-8")
check(e is None and len(ticks) == 3, "run loop continued after an unexpected error (ticks=%d, %r)" % (len(ticks), e))
check("Unexpected error, still running: ValueError: boom in tick" in logtext and "Traceback" in logtext,
      "unexpected error and traceback written to companion.log")

# ---------------------------------------------------------------- game volume sliders (v1.1.7)
# The built-in player plays outside the game's mixer, so the companion scales it by
# the volume the CET mod reads from Master x Car radio / Radioport.
class StubPlayer:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready, self.device_id, self.error = True, "sdk1", ""
        self.targets = []
    def running(self): return True
    def start(self): pass
    def maintain(self): pass
    def shutdown(self): pass
    def set_volume(self, v): self.targets.append(round(v, 4))
    @property
    def target(self): return self.targets[-1] if self.targets else None

S["devices"].append({"id": "sdk1", "name": "Spotify FM (Cyberpunk 2077)", "type": "Computer",
                     "is_active": False, "is_restricted": False})
stub = StubPlayer()
cfg_b = dict(cfg, player="browser", player_volume=0.8)
bridge = fm.Bridge(cfg_b, sp, mod_dir, mixer=mixer, clock=clock, sleep=clock.sleep, player=stub)

def game_v(mode, volume=None, reason="", sess=444):
    st = {"v": 1, "session": sess, "ts": int(clock.t), "hb": 1, "mode": mode, "reason": reason,
          "duckVolume": 0.3, "commands": []}
    if volume is not None:
        st["volume"] = volume
    (mod_dir / "state.json").write_text(json.dumps(st))
    os.utime(mod_dir / "state.json", (clock.t,) * 2)
    bridge.tick()
    clock.t += 0.5

def near(a, b): return a is not None and abs(a - b) < 1e-3

game_v("off", 0.12)
game_v("play", 0.12)
check(near(stub.target, 0.096), "tune in at car radio 20%% x master 60%%: 0.8 x 0.12 = 0.096 (got %s)" % stub.target)
game_v("play", 0.24)
check(near(stub.target, 0.192), "slider moved while playing applies at once (got %s)" % stub.target)
game_v("duck", 0.24, "holocall")
check(near(stub.target, 0.192 * 0.3), "holocall ducks relative to game volume (got %s)" % stub.target)
game_v("duck", 0.12, "holocall")
check(near(stub.target, 0.096 * 0.3), "slider moved while ducked keeps ducking (got %s)" % stub.target)
game_v("play", 0.12)
check(near(stub.target, 0.096), "call over: back to game volume, not 0.8 (got %s)" % stub.target)
game_v("play", 0.0)
check(near(stub.target, 0.0), "slider at 0 mutes Spotify FM (got %s)" % stub.target)
game_v("off", 0.0, "not tuned")
n_targets = len(stub.targets)
game_v("off", 0.5, "not tuned")
check(len(stub.targets) == n_targets, "slider moved while station is off: nothing played")
game_v("play", 0.5)
check(near(stub.target, 0.4), "next tune-in uses the latest slider value (got %s)" % stub.target)
game_v("play")  # older CET mod / followGameVolume off: no volume field
check(near(stub.target, 0.8), "no volume field = old behaviour, 0.8 (got %s)" % stub.target)
for junk in ("loud", True, -3, 7):
    v = fm.Bridge.parse_game_volume({"volume": junk})
    check(v == {"loud": 1.0, True: 1.0, -3: 0.0, 7: 1.0}[junk], "junk volume %r handled safely (got %s)" % (junk, v))

# ---------------------------------------------------------------- local folder sync
src = tmp / "MyMusic"
(src / "Album A").mkdir(parents=True)
(src / "loose.mp3").write_bytes(b"x" * 10)
(src / "Album A" / "01 Song.flac").write_bytes(b"y" * 20)
(src / "Album A" / "cover.jpg").write_bytes(b"z")
radios = tmp / "mods" / "radioExt" / "radios"
radios.mkdir(parents=True)
cfg["local_stations"] = [{"name": "My Music FM", "fm": 88.8, "source": str(src)}]
fm.sync_all_local(cfg, mod_dir)
st = radios / "My Music FM"
files = sorted(p.name for p in st.iterdir())
check(files == [".spotifyfm_sync.json", "Album A - 01 Song.flac", "loose.mp3", "metadata.json"], "station folder built: %s" % files)
check(os.stat(st / "loose.mp3").st_ino == os.stat(src / "loose.mp3").st_ino, "same-drive files are hard links")
meta = json.loads((st / "metadata.json").read_text())
check(meta["displayName"] == "88.8 My Music FM" and meta["fm"] == 88.8, "metadata.json written for RadioExt")
(st / "mine.mp3").write_bytes(b"m")
(src / "loose.mp3").unlink()
fm.sync_all_local(cfg, mod_dir)
files = sorted(p.name for p in st.iterdir())
check("loose.mp3" not in files and "mine.mp3" in files, "deleted source removed, user's own file kept")

print("\n%d failure(s)" % fails)
srv.shutdown()
sys.exit(1 if fails else 0)
