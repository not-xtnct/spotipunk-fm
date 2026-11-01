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
