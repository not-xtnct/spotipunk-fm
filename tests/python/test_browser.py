"""End-to-end test of the built-in player: real Chromium runs the real player page,
Spotify's SDK script is replaced by a stand-in, Spotify's Web API by a fake server."""
import http.server, importlib.util, json, os, sys, tempfile, threading, time, urllib.parse, urllib.request
from pathlib import Path

COMPANION, CHROME = Path(sys.argv[1]), sys.argv[2]
spec = importlib.util.spec_from_file_location("spotify_fm", COMPANION)
fm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fm)

S = {"player": None, "volumes": [], "sdk_tokens": [], "calls": [], "token_calls": 0}
FAKE_SDK = """
// Behaves like the real SDK in one important way: volume changes made before a
// track is actually playing are IGNORED, and a new track starts at the
// constructor volume.
window.Spotify = { Player: class {
  constructor(o) { this.o = o; this.l = {}; this.started = false; this.vol = o.volume; this.ctorVol = o.volume;
    setInterval(async () => {
      const r = await fetch(FAKE + "/fake/isplaying").then(r => r.json());
      if (r.playing && !this.started) { this.started = true; this.vol = this.ctorVol; this.l.player_state_changed && this.l.player_state_changed({}); }
      if (!r.playing) this.started = false;
      fetch(FAKE + "/fake/vol?v=" + (this.started ? this.vol : -1), {mode: "no-cors"});
    }, 150);
  }
  addListener(e, f) { this.l[e] = f; }
  connect() {
    this.o.getOAuthToken(t => fetch(FAKE + "/fake/sdk-token?t=" + encodeURIComponent(t), {mode: "no-cors"}));
    setTimeout(() => this.l.ready({device_id: "sdk-dev-1"}), 300);
    return Promise.resolve(true);
  }
  setVolume(v) { if (this.started) this.vol = v; return Promise.resolve(); }
  getVolume() { return Promise.resolve(this.vol); }
  getCurrentState() { return Promise.resolve(this.started ? {paused: false, position: Date.now() % 100000} : null); }
}};
window.onSpotifyWebPlaybackSDKReady();
"""


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj=None, ctype="application/json"):
        body = b"" if obj is None else (obj if isinstance(obj, bytes) else json.dumps(obj).encode())
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def route(self, m):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n) if n else b""
        if u.path == "/sdk.js":
            return self._send(200, ("const FAKE='http://127.0.0.1:%d';" % self.server.server_address[1] + FAKE_SDK).encode(), "text/javascript")
        if u.path == "/fake/vol":
            v = float(q["v"])
            if v >= 0: S["volumes"].append(v)
            return self._send(204)
        if u.path == "/fake/isplaying":
            return self._send(200, {"playing": bool(S["player"] and S["player"]["is_playing"])})
        if u.path == "/fake/sdk-token":
            S["sdk_tokens"].append(q["t"]); return self._send(204)
        if u.path == "/api/token":
            S["token_calls"] += 1
            return self._send(200, {"access_token": "AT%d" % S["token_calls"], "expires_in": 3600, "scope": " ".join(fm.SCOPES)})
        p = u.path.replace("/v1", "", 1); body = json.loads(raw) if raw else None
        S["calls"].append((m, p, q, body))
        if p == "/me": return self._send(200, {"id": "u"})
        if p == "/me/playlists": return self._send(200, {"items": [{"name": "Drive", "uri": "spotify:playlist:d", "items": {"total": 5}}], "next": None})
        if p == "/me/player/devices": return self._send(200, {"devices": []})
        if p == "/me/player" and m == "GET":
            return self._send(200, S["player"]) if S["player"] else self._send(204)
        if p == "/me/player/play":
            S["player"] = {"is_playing": True, "device": {"id": q["device_id"], "name": fm.PLAYER_NAME, "volume_percent": 100},
                           "context": {"uri": (body or {}).get("context_uri") or "spotify:playlist:d"},
                           "item": {"name": "Resonance", "artists": [{"name": "HOME"}]}}
            return self._send(204)
        if p == "/me/player/pause":
            S["player"]["is_playing"] = False; return self._send(204)
        return self._send(204)
    def do_GET(self): self.route("GET")
    def do_PUT(self): self.route("PUT")
    def do_POST(self): self.route("POST")


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]
fm.SDK_URL = base + "/sdk.js"

tmp = Path(tempfile.mkdtemp())
fm.LOG_PATH = tmp / "log"; fm.CONFIG_PATH = tmp / "config.json"; fm.PLAYER_PROFILE_DIR = tmp / "profile"
wrapper = tmp / "browser.sh"
wrapper.write_text("#!/bin/sh\nexec %s --headless=new --no-sandbox \"$@\"\n" % CHROME); wrapper.chmod(0o755)
mod_dir = tmp / "spotifyFM"; mod_dir.mkdir(); (mod_dir / "init.lua").write_text("--")
tokens = tmp / "tokens.json"
tokens.write_text(json.dumps({"access_token": "x", "expires_at": 0, "refresh_token": "R", "client_id": "c" * 32, "scope": " ".join(fm.SCOPES)}))

cfg = fm.load_config()
cfg.update(client_id="c" * 32, playlist_uri="spotify:playlist:d", browser_path=str(wrapper), player_volume=0.8, fade_ms=200)
sp = fm.Spotify(cfg["client_id"], 8898, tokens_path=tokens, api_url=base + "/v1", accounts_url=base)
hidden = []
player = fm.BrowserPlayer(cfg, sp, 18899, hide=lambda t: hidden.append(t))
app_launched = []
bridge = fm.Bridge(cfg, sp, mod_dir, player=player, launcher=lambda: app_launched.append(1))

fails = 0
def check(c, m):
    global fails; print(("ok   " if c else "FAIL ") + m); fails += 0 if c else 1

def get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:18899" + path, timeout=5) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, b""

check(get("/token")[0] == 403 and get("/token?k=wrong")[0] == 403, "token endpoint refuses requests without the secret key")
check(get("/player?k=wrong")[0] == 403, "player page refuses wrong key")

session = 1
def game(mode, fresh=True, reason=""):
    st = {"session": session, "ts": int(time.time() if fresh else time.time() - 60), "mode": mode, "reason": reason, "duckVolume": 0.3, "commands": []}
    (mod_dir / "state.json").write_text(json.dumps(st))
    os.utime(mod_dir / "state.json", (time.time() if fresh else time.time() - 60,) * 2)
    bridge.tick()

def settle(sec):
    end = time.time() + sec
    while time.time() < end:
        bridge.tick() if False else player.maintain(); time.sleep(0.1)

game("off", reason="not tuned")
check(player.running(), "game detected -> hidden player started in advance")
for _ in range(100):
    if player.ready: break
    time.sleep(0.1)
check(player.ready and player.device_id == "sdk-dev-1", "player page loaded SDK and reported its device id")
check(S["sdk_tokens"] and S["sdk_tokens"][-1] == sp.tokens["access_token"], "page received a real access token from the companion")
check("--app=" in " ".join(player.proc.args) and str(fm.PLAYER_PROFILE_DIR) in " ".join(player.proc.args), "browser launched in app mode with its own profile")
check(not any(a.startswith("--window-position") for a in player.proc.args), "window opens on screen first (never-visible pages stay silent)")
player.maintain()
check(hidden and hidden[-1] == fm.PLAYER_TITLE, "window hidden once the player reported ready")

game("play")
plays = [c for c in S["calls"] if c[1] == "/me/player/play"]
check(plays and plays[-1][2].get("device_id") == "sdk-dev-1", "tuning in plays on the built-in player, not the Spotify app")
check(not app_launched, "Spotify app was never launched")
settle(1.5)
check(S["volumes"] and abs(S["volumes"][-1] - 0.8) < 0.01, "volume faded in to player_volume 0.8 (last %.3f)" % (S["volumes"][-1] if S["volumes"] else -1))
def wait_log(pred, sec):
    end = time.time() + sec
    while time.time() < end:
        if any(pred(l) for l in open(fm.LOG_PATH)): return True
        player.maintain(); time.sleep(0.2)
    return False
check(wait_log(lambda l: "Player status: playing" in l, 12), "player status report logged")
check(wait_log(lambda l: "DRM ok" in l and "position moving" in l, 70), "status shows DRM check and moving position")

game("duck", reason="dialogue"); settle(1.5)
check(abs(S["volumes"][-1] - 0.24) < 0.01, "dialogue ducks to 0.8*0.3=0.24 (last %.3f)" % S["volumes"][-1])
game("play"); settle(1.5)
check(abs(S["volumes"][-1] - 0.8) < 0.01, "volume back to 0.8 after dialogue")

game("pause", reason="scene"); settle(1.0)
check(S["player"]["is_playing"] is False, "cutscene pauses player")
game("play"); settle(1.5)
check(S["player"]["is_playing"] and abs(S["volumes"][-1] - 0.8) < 0.01, "resumes and fades back in after cutscene")

np = json.loads((mod_dir / "now_playing.json").read_text()) if (mod_dir / "now_playing.json").exists() else {}
bridge.last_np_write = 0; bridge.poll_player(); bridge.write_now_playing()
np = json.loads((mod_dir / "now_playing.json").read_text())
check(np["device"] == fm.PLAYER_NAME and np["track"] == "Resonance", "now_playing shows built-in player and track")

proc = player.proc
game("play", fresh=False)
check(player.running() and proc.poll() is None, "game closed -> player stays ready (hidden) for next session")
check(S["player"]["is_playing"] is False, "music paused when game closed")

# player window closed by someone while the game runs -> reopened
game("off", reason="not tuned")
for _ in range(50):
    if player.ready: break
    time.sleep(0.1)
p1 = player.proc; p1.terminate(); p1.wait()
fm.kill_profile_processes(fm.PLAYER_PROFILE_DIR)
player.maintain()
check(player.proc is p1, "no instant relaunch when the window closes")
end = time.time() + 60
while time.time() < end and not (player.proc is not p1 and player.ready):
    player.maintain(); time.sleep(0.5)
check(player.proc is not p1 and player.ready, "player reopened after a pause once its window was really gone")
player.shutdown()
check(not player.running(), "companion exit closes the player")
print("\n%d failure(s)" % fails); srv.shutdown(); sys.exit(1 if fails else 0)
