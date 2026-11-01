"""
SpotiPunk FM companion app.

Runs next to Cyberpunk 2077 and connects the "SpotiPunk FM" radio station
(RadioExt + the spotipunkFM CET mod) to your Spotify account:

  * tune in to SpotiPunk FM in game  -> your chosen playlist plays on this PC
  * cutscene / locked dialogue     -> Spotify pauses, then resumes
  * walk-and-talk dialogue, calls  -> Spotify volume is lowered, then restored
  * switch station / leave the car -> Spotify pauses
  * the game's radio popup shows the current Spotify track

It also keeps local-music stations in sync: point it at any folder of
mp3/flac/wav/ogg files and it mirrors them into a RadioExt station.

Needs Spotify Premium (playback control through the Web API requires it).

By default music plays through a built-in player (Spotify's official Web
Playback SDK, running in a hidden Microsoft Edge / Chrome window with its own
profile), so the Spotify desktop app is not needed. Set "player": "app" in
config.json to control the Spotify app instead.

Usage:
    python spotipunk_fm.py                 run the bridge (first run walks you through setup)
    python spotipunk_fm.py --setup         redo the Spotify setup and playlist choice
    python spotipunk_fm.py --add-folder "D:\\Music\\Synthwave" --name "Synthwave FM" --fm 89.3
    python spotipunk_fm.py --sync          only sync local-music stations, then exit
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import http.server
import json
import os
import random
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

VERSION = "1.2.0"

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
TOKENS_PATH = HERE / "tokens.json"
LOG_PATH = HERE / "companion.log"
PLAYER_PROFILE_DIR = HERE / ".player-profile"

ACCOUNTS_URL = "https://accounts.spotify.com"
API_URL = "https://api.spotify.com/v1"
SDK_URL = "https://sdk.scdn.co/spotify-player.js"

SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
    # needed by the built-in player (Spotify Web Playback SDK)
    "streaming",
    "user-read-email",
    "user-read-private",
]

PLAYER_NAME = "SpotiPunk FM (Cyberpunk 2077)"
PLAYER_TITLE = "SpotiPunk FM Player"

LIKED_SONGS = "liked"  # pseudo playlist id for the user's Liked Songs

AUDIO_EXTENSIONS = {
    ".mp3", ".mp2", ".flac", ".ogg", ".wav", ".wax", ".wma", ".opus", ".aiff", ".aif", ".aifc",
}

DEFAULT_CONFIG = {
    "client_id": "",
    "redirect_port": 8898,
    "playlist_uri": "",
    "playlist_name": "",
    "shuffle": True,
    # "browser": built-in hidden player, no Spotify app needed (default)
    # "app": control the Spotify desktop app (or another device) instead
    "player": "browser",
    "player_volume": 0.8,          # built-in player's volume at 100% game volume (0.0 - 1.0);
                                   # scaled by the game's Master x Car radio / Radioport sliders
    "show_player_window": False,   # True shows the hidden player window (for troubleshooting)
    "browser_path": "",            # leave empty to find Edge or Chrome automatically
    "device_name": "",
    "resume_where_left_off": True,
    "launch_spotify": True,        # "app" mode only
    "fade_ms": 400,
    "mod_dir": "",
    "local_stations": [],
}

POLL_INTERVAL = 0.25      # how often state.json is checked
STALE_AFTER = 8           # seconds without a game heartbeat before we treat the game as closed
PLAYER_POLL_ACTIVE = 3    # seconds between "what's playing" checks while tuned in
PLAYER_POLL_IDLE = 15     # ... while the game runs but the station isn't tuned
PLAYLIST_REFRESH = 600    # seconds between playlist list refreshes
API_TIMEOUT = 8           # seconds; Spotify normally answers in well under 1 s, and the
                          # bridge loop waits for each call, so a dead network must fail fast
POLL_RETRY_AFTER_ERROR = 30     # seconds before re-reading "what's playing" after a failure
PLAYLIST_RETRY_AFTER_ERROR = 60 # seconds before re-loading playlists after a failure


# ---------------------------------------------------------------------------
# Logging and small file helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d ") + line + "\n")
    except OSError:
        pass


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json_atomic(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    data = read_json(CONFIG_PATH, {})
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    return cfg


def save_config(cfg: dict) -> None:
    write_json_atomic(CONFIG_PATH, cfg)


def find_mod_dir(cfg: dict) -> Path | None:
    """The spotipunkFM CET mod folder, where state.json and now_playing.json live."""
    if cfg.get("mod_dir"):
        p = Path(cfg["mod_dir"]).expanduser()
        return p if (p / "init.lua").exists() else None
    if (HERE.parent / "init.lua").exists():
        return HERE.parent
    return None


# ---------------------------------------------------------------------------
# Spotify Web API client (standard library only)
# ---------------------------------------------------------------------------

class SpotifyError(Exception):
    def __init__(self, status: int, message: str, reason: str = ""):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message
        self.reason = reason


# urllib wraps only connection-setup failures in URLError. A timeout or a dropped
# connection while waiting for the reply comes through raw (TimeoutError,
# RemoteDisconnected, ConnectionResetError, IncompleteRead). v1.1.5 let one of
# those end the companion in the middle of a game session.
NETWORK_ERRORS = (OSError, http.client.HTTPException)


class Spotify:
    def __init__(self, client_id: str, redirect_port: int, tokens_path: Path = TOKENS_PATH,
                 api_url: str = API_URL, accounts_url: str = ACCOUNTS_URL):
        self.client_id = client_id
        self.redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"
        self.redirect_port = redirect_port
        self.tokens_path = tokens_path
        self.api_url = api_url.rstrip("/")
        self.accounts_url = accounts_url.rstrip("/")
        self.tokens = read_json(tokens_path, {}) or {}
        self._lock = threading.RLock()  # the built-in player asks for tokens from another thread

    # --- auth ---------------------------------------------------------------

    def has_login(self) -> bool:
        return bool(self.tokens.get("refresh_token")) and self.tokens.get("client_id") == self.client_id

    def login(self) -> None:
        """Authorization Code with PKCE: no client secret needed on this PC."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": " ".join(SCOPES),
            "state": state,
        }
        url = f"{self.accounts_url}/authorize?" + urllib.parse.urlencode(params)

        result: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if urllib.parse.urlparse(self.path).path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                result["code"] = (q.get("code") or [None])[0]
                result["state"] = (q.get("state") or [None])[0]
                result["error"] = (q.get("error") or [None])[0]
                ok = result["code"] and result["state"] == state
                body = ("<h2>SpotiPunk FM is connected.</h2><p>You can close this tab and go back to the game.</p>"
                        if ok else "<h2>Login failed.</h2><p>Go back to the SpotiPunk FM window for details.</p>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        try:
            server = ExclusiveServer(("127.0.0.1", self.redirect_port), Handler)
        except OSError as e:
            raise SystemExit(f"Port {self.redirect_port} is busy ({e}). Change redirect_port in config.json "
                             f"and the Redirect URI in your Spotify app to match.")
        server.timeout = 1
        log("Opening your browser to log in to Spotify...")
        log("If it doesn't open, paste this link into a browser:\n" + url)
        webbrowser.open(url)
        deadline = time.time() + 300
        while "code" not in result and "error" not in result and time.time() < deadline:
            server.handle_request()
        server.server_close()

        if result.get("error"):
            raise SystemExit(f"Spotify login was refused: {result['error']}")
        if not result.get("code"):
            raise SystemExit("Timed out waiting for the Spotify login.")
        if result.get("state") != state:
            raise SystemExit("Login response did not match the request (state mismatch). Try again.")

        self._token_request({
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
        })
        log("Logged in to Spotify.")

    def _token_request(self, form: dict) -> None:
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(f"{self.accounts_url}/api/token", data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                tok = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise SpotifyError(e.code, f"token request failed: {detail}")
        except urllib.error.URLError as e:
            raise SpotifyError(0, f"network error during login refresh: {e.reason}")
        except NETWORK_ERRORS as e:
            raise SpotifyError(0, f"network error during login refresh: {e!r}")
        except ValueError:
            raise SpotifyError(0, "token request failed: Spotify sent an unreadable reply")
        if not isinstance(tok, dict) or not tok.get("access_token"):
            raise SpotifyError(0, "token request failed: no access token in Spotify's reply")
        self.tokens["access_token"] = tok["access_token"]
        self.tokens["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        if tok.get("refresh_token"):
            self.tokens["refresh_token"] = tok["refresh_token"]
        if tok.get("scope") is not None:
            self.tokens["scope"] = tok["scope"]
        elif form.get("grant_type") == "authorization_code":
            self.tokens["scope"] = " ".join(SCOPES)
        self.tokens["client_id"] = self.client_id
        write_json_atomic(self.tokens_path, self.tokens)

    def has_scopes(self, needed) -> bool:
        granted = set((self.tokens.get("scope") or "").split())
        return set(needed) <= granted

    def access_token(self) -> str:
        """A valid access token, refreshed if it is about to expire (used by the built-in player)."""
        with self._lock:
            if not self.tokens.get("access_token") or time.time() >= self.tokens.get("expires_at", 0):
                self._refresh()
            return self.tokens["access_token"]

    def _refresh(self) -> None:
        with self._lock:
            if not self.tokens.get("refresh_token"):
                raise SpotifyError(401, "not logged in")
            self._token_request({
                "grant_type": "refresh_token",
                "refresh_token": self.tokens["refresh_token"],
                "client_id": self.client_id,
            })

    # --- requests -----------------------------------------------------------

    def request(self, method: str, path: str, params: dict | None = None, body=None, _retry: bool = True):
        token = self.access_token()
        url = self.api_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else (b"" if method in ("PUT", "POST") else None)
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                raw = resp.read()
                if resp.status == 204 or not raw:
                    return None
                try:
                    return json.loads(raw.decode())
                except ValueError:
                    return None
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            if e.code == 401 and _retry:
                self._refresh()
                return self.request(method, path, params, body, _retry=False)
            if e.code == 429 and _retry:
                wait = min(int(e.headers.get("Retry-After", "2") or 2), 10)
                time.sleep(wait)
                return self.request(method, path, params, body, _retry=False)
            message, reason = raw, ""
            try:
                err = json.loads(raw).get("error", {})
                if isinstance(err, dict):
                    message, reason = err.get("message", raw), err.get("reason", "")
            except ValueError:
                pass
            raise SpotifyError(e.code, message, reason)
        except urllib.error.URLError as e:
            raise SpotifyError(0, f"network error: {e.reason}")
        except NETWORK_ERRORS as e:
            raise SpotifyError(0, f"network error: {e!r}")

    # --- endpoints ----------------------------------------------------------

    def me(self):
        return self.request("GET", "/me")

    def devices(self) -> list:
        return (self.request("GET", "/me/player/devices") or {}).get("devices", [])

    def player(self):
        return self.request("GET", "/me/player")

    def playlists(self) -> list:
        out, offset = [], 0
        while True:
            page = self.request("GET", "/me/playlists", {"limit": 50, "offset": offset}) or {}
            items = page.get("items") or []
            for p in items:
                if not p:
                    continue
                # Feb 2026 API change renamed "tracks" to "items" on playlist objects.
                count = (p.get("items") or p.get("tracks") or {}).get("total")
                out.append({"name": p.get("name") or "(untitled)", "uri": p.get("uri"), "total": count})
            if not page.get("next") or not items or len(out) >= 500:
                return out
            offset += len(items)

    def liked_track_uris(self, limit: int = 100) -> list:
        uris, offset = [], 0
        while len(uris) < limit:
            page = self.request("GET", "/me/tracks", {"limit": 50, "offset": offset}) or {}
            items = page.get("items") or []
            uris += [i["track"]["uri"] for i in items if i.get("track") and i["track"].get("uri")]
            if not page.get("next") or not items:
                break
            offset += len(items)
        return uris[:limit]

    def play(self, device_id: str | None, context_uri: str | None = None, uris: list | None = None,
             offset: int | None = None) -> None:
        body = {}
        if context_uri:
            body["context_uri"] = context_uri
        if uris:
            body["uris"] = uris
        if offset is not None:
            body["offset"] = {"position": offset}
        self.request("PUT", "/me/player/play", {"device_id": device_id}, body or None)

    def pause(self, device_id: str | None = None) -> None:
        self.request("PUT", "/me/player/pause", {"device_id": device_id})

    def next(self, device_id: str | None = None) -> None:
        self.request("POST", "/me/player/next", {"device_id": device_id})

    def previous(self, device_id: str | None = None) -> None:
        self.request("POST", "/me/player/previous", {"device_id": device_id})

    def shuffle(self, state: bool, device_id: str | None = None) -> None:
        self.request("PUT", "/me/player/shuffle", {"state": "true" if state else "false", "device_id": device_id})

    def volume(self, percent: int, device_id: str | None = None) -> None:
        self.request("PUT", "/me/player/volume", {"volume_percent": max(0, min(100, int(percent))),
                                                  "device_id": device_id})


# ---------------------------------------------------------------------------
# Volume ducking: Windows mixer (smooth fades) with Web API fallback
# ---------------------------------------------------------------------------

class WindowsMixer:
    """Changes only Spotify's own volume in the Windows mixer, so other apps and
    Spotify's in-app volume slider are left alone. Needs the optional pycaw package."""

    def __init__(self):
        self.ok = False
        try:
            from pycaw.pycaw import AudioUtilities  # type: ignore
            self._utils = AudioUtilities
            self.ok = sys.platform == "win32"
        except Exception:
            self.ok = False

    def _sessions(self):
        out = []
        try:
            for s in self._utils.GetAllSessions():
                proc = getattr(s, "Process", None)
                if proc and proc.name().lower() == "spotify.exe":
                    out.append(s.SimpleAudioVolume)
        except Exception:
            pass
        return out

    def get(self) -> float | None:
        sessions = self._sessions()
        if not sessions:
            return None
        try:
            return float(sessions[0].GetMasterVolume())
        except Exception:
            return None

    def set(self, level: float) -> bool:
        sessions = self._sessions()
        for s in sessions:
            try:
                s.SetMasterVolume(max(0.0, min(1.0, level)), None)
            except Exception:
                pass
        return bool(sessions)

    def fade(self, start: float, end: float, ms: int) -> bool:
        steps = max(1, int(ms / 40))
        for i in range(1, steps + 1):
            if not self.set(start + (end - start) * i / steps):
                return False
            time.sleep(ms / 1000 / steps)
        return True


# ---------------------------------------------------------------------------
# Built-in player: Spotify's official Web Playback SDK in a hidden browser
# window, so the Spotify desktop app is not needed.
# ---------------------------------------------------------------------------

PLAYER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>body{font:14px system-ui,sans-serif;background:#111;color:#eee;margin:16px}#s{color:#9f9}</style>
</head><body>
<b>SpotiPunk FM player</b> - keep this running while you play. It is normally hidden.
<p id="s">starting...</p>
<button id="b" style="font-size:16px;padding:8px 16px">Start sound</button>
<script>
const K = new URLSearchParams(location.search).get("k");
const show = t => { document.getElementById("s").textContent = t; };
const report = o => fetch("/state?k=" + K, {method: "POST", body: JSON.stringify(o)}).catch(() => {});
async function token() {
  const r = await fetch("/token?k=" + K);
  if (!r.ok) throw new Error("token " + r.status);
  return (await r.json()).access_token;
}
let retryTimer = null;
function retryLater(why) {
  show("problem: " + why + " (retrying)");
  report({ready: false, error: why});
  if (!retryTimer) retryTimer = setTimeout(() => location.reload(), 15000);
}
// Can this browser play DRM-protected audio (needed by Spotify)?
let drm = "unknown";
if (navigator.requestMediaKeySystemAccess) {
  navigator.requestMediaKeySystemAccess("com.widevine.alpha", [{initDataTypes: ["cenc"],
      audioCapabilities: [{contentType: 'audio/mp4; codecs="mp4a.40.2"'}]}])
    .then(() => { drm = "ok"; }, e => { drm = "missing (" + e.name + ")"; });
} else { drm = "missing (no EME)"; }

window.onSpotifyWebPlaybackSDKReady = () => {
  const player = new Spotify.Player({
    name: __NAME__,
    getOAuthToken: cb => token().then(cb).catch(e => retryLater(String(e))),
    volume: __VOL__,
  });
  let current = __VOL__, target = __VOL__, lastErr = "";
  const apply = () => player.setVolume(Math.max(0.0001, Math.min(1, current))).catch(() => {});
  player.addListener("ready", ({device_id}) => { show("ready"); report({ready: true, device_id, error: ""}); });
  player.addListener("not_ready", ({device_id}) => { show("offline"); report({ready: false, device_id}); });
  player.addListener("initialization_error", ({message}) => retryLater("init: " + message));
  player.addListener("authentication_error", ({message}) => retryLater("auth: " + message));
  player.addListener("account_error", ({message}) => report({ready: false, error: "account: " + message}));
  player.addListener("playback_error", ({message}) => { lastErr = message; report({error: "playback: " + message}); });
  player.addListener("autoplay_failed", () => { lastErr = "autoplay blocked"; report({error: "autoplay blocked"}); });
  // A new track creates new audio inside the SDK: re-apply our volume every time.
  player.addListener("player_state_changed", () => apply());
  player.connect().then(ok => { if (!ok) retryLater("could not connect"); });
  // Browsers may block sound until the page is clicked once; this button counts as that click.
  document.getElementById("b").onclick = async () => {
    try { await player.activateElement(); } catch (e) {}
    try { await player.resume(); } catch (e) {}
    report({clicked: true});
  };
  try { player.activateElement(); } catch (e) {}

  // Volume: the companion says what it wants (normal / ducked); fade towards it here,
  // and re-apply it every second so it sticks even if the SDK reset it.
  setInterval(async () => {
    try { target = (await (await fetch("/control?k=" + K)).json()).volume; } catch (e) {}
  }, 250);
  let ticks = 0;
  setInterval(() => {
    const diff = target - current;
    if (Math.abs(diff) >= 0.005) {
      current += Math.sign(diff) * Math.min(Math.abs(diff), __STEP__);
      apply();
    } else if (++ticks % 25 === 0) {
      apply();
    }
  }, 40);

  // Status report so problems show up in companion.log.
  setInterval(async () => {
    let st = null, vol = null;
    try { st = await player.getCurrentState(); } catch (e) {}
    try { vol = await player.getVolume(); } catch (e) {}
    report({diag: {active: !!st, paused: st ? st.paused : null, position: st ? st.position : null,
                   volume: vol, target, drm, error: lastErr, visible: document.visibilityState}});
  }, 5000);
};
const sdk = document.createElement("script");
sdk.src = "__SDK__";
sdk.onerror = () => retryLater("could not load Spotify's player script (offline?)");
document.head.appendChild(sdk);
</script></body></html>
"""


class ExclusiveServer(http.server.ThreadingHTTPServer):
    """Refuses to share its port. Python's HTTP servers set SO_REUSEADDR, which on
    Windows lets a second program bind the same port: that let two companions run
    at once. This makes the second one fail instead."""
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

    def server_bind(self):
        if sys.platform == "win32":
            import socket as _s
            if hasattr(_s, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(_s.SOL_SOCKET, _s.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def profile_processes(profile_dir: Path) -> list:
    """Browser processes that use the player's own profile folder (never the
    user's normal browser, which uses a different folder)."""
    try:
        import psutil  # installed with the companion's requirements
    except ImportError:
        return []
    marker = str(profile_dir).lower()
    found = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or []).lower()
        except Exception:
            continue
        if marker and ("--user-data-dir=" + marker) in cmd:
            found.append(p)
    return found


def kill_profile_processes(profile_dir: Path) -> int:
    procs = profile_processes(profile_dir)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    if procs:
        try:
            import psutil
            psutil.wait_procs(procs, timeout=5)
        except Exception:
            pass
    return len(procs)


def game_process_alive() -> bool | None:
    """True/False whether Cyberpunk2077.exe is running; None if psutil is missing."""
    try:
        import psutil
    except ImportError:
        return None
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() == "cyberpunk2077.exe":
                return True
        except Exception:
            continue
    return False


class GameWatch:
    """--background (started with the game by start_with_game.pyw): tells the
    bridge loop when to exit by itself. The game counts as alive while its
    heartbeat is fresh OR its process runs, so long loading screens (heartbeat
    gaps) don't end the session."""
    CHECK_EVERY = 5          # seconds between process checks
    GRACE = 20               # seconds without the game before exiting
    GRACE_NO_PSUTIL = 90     # heartbeat only: must outlast loading-screen gaps
    NEVER_STARTED = 600      # the game never showed up: give up

    def __init__(self, bridge, clock=time.time, process_alive=game_process_alive):
        self.bridge, self.clock, self.process_alive = bridge, clock, process_alive
        self.started = self.last_alive = clock()
        self.last_check = -1e9
        self.proc = None
        self.seen = False
        self.reason = ""

    def __call__(self) -> bool:
        now = self.clock()
        if now - self.last_check >= self.CHECK_EVERY:
            self.last_check = now
            try:
                self.proc = self.process_alive()
            except Exception:
                self.proc = None
        if self.bridge.game_running or self.proc:
            self.seen, self.last_alive = True, now
            return False
        grace = self.GRACE if self.proc is not None else self.GRACE_NO_PSUTIL
        if self.seen and now - self.last_alive >= grace:
            self.reason = "the game has closed"
            return True
        if not self.seen and now - self.started >= self.NEVER_STARTED:
            self.reason = "the game didn't start within 10 minutes"
            return True
        return False


def needs_interactive_setup(cfg: dict) -> str:
    """Why the companion can't run without asking questions ('' = it can)."""
    if not cfg.get("client_id"):
        return "no Spotify Client ID yet"
    sp = Spotify(cfg["client_id"], int(cfg["redirect_port"]), tokens_path=TOKENS_PATH)
    if not sp.has_login():
        return "not logged in to Spotify"
    if not sp.has_scopes(SCOPES):
        return "Spotify needs to approve the built-in player once"
    if not cfg.get("playlist_uri"):
        return "no playlist chosen"
    return ""


def notify_user(text: str) -> None:
    """A plain message box (background mode has no console). Not topmost, so it
    doesn't pull you out of a full-screen game; it's there when you alt-tab."""
    log("Message for the user: " + text)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "SpotiPunk FM", 0x40)  # MB_ICONINFORMATION
        except Exception:
            pass


def install_close_handler(cleanup) -> bool:
    """Closing the console window with X, logging off or shutting down ends Python
    without running any finally block, which left the hidden player running.
    Windows gives a console program about 5 s after these events, so run the
    cleanup then. Ctrl+C / Ctrl+Break pass through to Python (KeyboardInterrupt)."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    close_events = (2, 5, 6)  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT

    def handler(event):
        if event in close_events:
            try:
                cleanup()
            except Exception:
                pass
        return False  # let the next handler / Windows carry on as usual

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(handler)
    install_close_handler.keep_alive = callback  # must outlive this call
    return bool(ctypes.windll.kernel32.SetConsoleCtrlHandler(callback, True))


def find_browser(cfg: dict) -> str | None:
    if cfg.get("browser_path"):
        p = Path(cfg["browser_path"]).expanduser()
        return str(p) if p.exists() else None
    env = os.environ.get
    candidates = [
        Path(env("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(env("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(env("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(env("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(env("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def hide_windows_titled(title: str) -> int:
    """Moves top-level windows with this exact title far off-screen and removes
    them from the taskbar and Alt+Tab, WITHOUT hiding them: the browser must
    still consider the page visible, because it refuses to start new audio in
    hidden pages (that is what made the player silent). Windows only.
    Returns how many windows were changed."""
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
    SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x4, 0x10, 0x20
    SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
    OFF = -32000
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n == len(title):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == title:
                found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    changed = 0
    for hwnd in found:
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        tucked = (ex & WS_EX_TOOLWINDOW) and not (ex & WS_EX_APPWINDOW) and rect.left <= OFF + 1000
        if tucked and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            continue  # already out of sight and still "visible" to the browser
        # The taskbar only notices a style change while the window is hidden, so
        # hide it for an instant, change the style, move it away, show it again.
        user32.ShowWindow(hwnd, SW_HIDE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        user32.SetWindowPos(hwnd, None, OFF, OFF, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        changed += 1
    return changed


class BrowserPlayer:
    """Runs the player page on http://127.0.0.1:<port>/player and keeps a hidden
    browser window open on it while the game is running."""

    def __init__(self, cfg: dict, sp: Spotify, port: int, launch=None, hide=hide_windows_titled,
                 clock=time.time):
        self.cfg = cfg
        self.sp = sp
        self.port = port
        self.key = secrets.token_urlsafe(24)  # only our own page can read the token
        self.clock = clock
        self._launch = launch or self._launch_browser
        self._hide = hide
        self.lock = threading.Lock()
        self.device_id: str | None = None
        self.ready = False
        self.error = ""
        self.target_volume = 0.0
        self.last_seen = 0.0
        self.proc = None
        self.started_at = 0.0
        self.launches: list[float] = []   # times the browser was launched
        self.gave_up = False
        self.last_hide = 0.0
        self.diag: dict = {}
        self.last_diag = None
        self.last_diag_log = 0.0
        self.last_pos = None
        self.server = None
        self._start_server()

    # --- local web server -----------------------------------------------------

    def _start_server(self) -> None:
        player = self
        step = max(0.01, 1.0 / max(1, int(self.cfg.get("fade_ms", 400)) / 40))
        page = (PLAYER_HTML.replace("__TITLE__", PLAYER_TITLE)
                .replace("__NAME__", json.dumps(PLAYER_NAME))
                .replace("__SDK__", SDK_URL)
                .replace("__VOL__", f"{self._base_volume():.3f}")
                .replace("__STEP__", f"{step:.3f}")).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                return secrets.compare_digest((q.get("k") or [""])[0], player.key)

            def do_GET(self):  # noqa: N802
                path = urllib.parse.urlparse(self.path).path
                if not self._authorized():
                    return self._send(403, b'{"error":"forbidden"}')
                if path == "/player":
                    return self._send(200, page, "text/html; charset=utf-8")
                if path == "/token":
                    try:
                        tok = player.sp.access_token()
                    except Exception as e:
                        return self._send(503, json.dumps({"error": str(e)}).encode())
                    return self._send(200, json.dumps({"access_token": tok}).encode())
                if path == "/control":
                    with player.lock:
                        player.last_seen = player.clock()
                        vol = player.target_volume
                    return self._send(200, json.dumps({"volume": round(vol, 3)}).encode())
                return self._send(404, b"{}")

            def do_POST(self):  # noqa: N802
                if not self._authorized() or urllib.parse.urlparse(self.path).path != "/state":
                    return self._send(403, b"{}")
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                except ValueError:
                    return self._send(400, b"{}")
                player.on_report(data)
                return self._send(204)

        try:
            self.server = ExclusiveServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            raise SystemExit(f"Port {self.port} is busy ({e}). Is another SpotiPunk FM companion window open?")
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/player?k={self.key}"

    def _base_volume(self) -> float:
        try:
            return max(0.05, min(1.0, float(self.cfg.get("player_volume", 0.8))))
        except (TypeError, ValueError):
            return 0.8

    def _log_diag(self, d: dict) -> None:
        """Logs the page's status when something meaningful changes (or once a minute)."""
        vol = d.get("volume")
        summary = (bool(d.get("active")), d.get("paused"),
                   round(vol, 2) if isinstance(vol, (int, float)) else vol,
                   d.get("drm"), d.get("error"), d.get("visible"))
        now = self.clock()
        pos = d.get("position")
        moving = None
        if isinstance(pos, (int, float)) and isinstance(self.last_pos, (int, float)):
            moving = pos != self.last_pos
        self.last_pos = pos
        if summary != self.last_diag or now - self.last_diag_log >= 60:
            self.last_diag, self.last_diag_log = summary, now
            log("Player status: " + ", ".join([
                "playing" if d.get("active") and d.get("paused") is False else
                ("paused" if d.get("active") else "no track loaded"),
                f"volume {vol:.2f}" if isinstance(vol, (int, float)) else f"volume {vol}",
                f"target {d.get('target')}",
                f"DRM {d.get('drm')}",
                f"position {'moving' if moving else 'stuck' if moving is False else '?'}",
                f"page {d.get('visible')}",
            ] + ([f"error: {d.get('error')}"] if d.get("error") else [])))

    def on_report(self, data: dict) -> None:
        if data.get("clicked"):
            log("Player window: 'Start sound' was clicked.")
        if isinstance(data.get("diag"), dict):
            self.diag = data["diag"]
            self._log_diag(self.diag)
        with self.lock:
            self.last_seen = self.clock()
            if "ready" in data:
                self.ready = bool(data["ready"])
            if data.get("device_id") and data.get("ready"):
                if data["device_id"] != self.device_id:
                    log("Built-in player is ready.")
                self.device_id = data["device_id"]
            if "error" in data:
                err = str(data.get("error") or "")
                if err and err != self.error:
                    log("Built-in player: " + err)
                self.error = err

    # --- browser window ---------------------------------------------------------

    def _launch_browser(self, url: str):
        import subprocess
        exe = find_browser(self.cfg)
        if not exe:
            raise RuntimeError("Microsoft Edge or Google Chrome is needed for the built-in player. "
                               "Install one, or set \"player\": \"app\" in config.json to use the Spotify app.")
        PLAYER_PROFILE_DIR.mkdir(exist_ok=True)
        args = [
            exe,
            f"--app={url}",
            f"--user-data-dir={PLAYER_PROFILE_DIR}",  # separate profile: your own browser is untouched
            "--no-first-run",
            "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--hide-crash-restore-bubble",
            "--disable-session-crashed-bubble",
            "--disable-background-media-suspend",
            "--disable-features=CalculateNativeWinOcclusion",
            "--window-size=520,220",
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(args, creationflags=flags, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    MAX_LAUNCHES = 3            # per LAUNCH_WINDOW seconds; then give up until the companion restarts
    LAUNCH_WINDOW = 600
    MIN_RELAUNCH_GAP = 30       # never relaunch sooner than this
    PAGE_TIMEOUT = 20           # page silent this long = player gone

    def page_alive(self) -> bool:
        with self.lock:
            return self.last_seen > 0 and self.clock() - self.last_seen < self.PAGE_TIMEOUT

    def running(self) -> bool:
        """The player counts as running if our browser process is alive OR the page
        is still talking to us. (A browser may hand the window to an already-open
        copy and exit at once; the page then lives on in that copy.)"""
        if self.proc is not None and self.proc.poll() is None:
            return True
        return self.page_alive()

    def start(self) -> None:
        if self.running() or self.gave_up:
            return
        now = self.clock()
        self.launches = [t for t in self.launches if now - t < self.LAUNCH_WINDOW]
        if self.launches and now - self.launches[-1] < self.MIN_RELAUNCH_GAP:
            return  # too soon; maintain() will try again later
        if len(self.launches) >= self.MAX_LAUNCHES:
            self.gave_up = True
            with self.lock:
                self.error = "the player window kept closing, so it was stopped. Restart the companion to try again."
            log("Built-in player: " + self.error)
            return
        # Close leftovers from an earlier companion run that use the same profile;
        # otherwise the new browser hands over to them and exits immediately.
        leftovers = kill_profile_processes(PLAYER_PROFILE_DIR)
        if leftovers:
            log(f"Closed {leftovers} leftover player process(es) from an earlier run.")
        log("Starting the built-in player (hidden)...")
        with self.lock:
            self.ready, self.device_id, self.error = False, None, ""
            self.target_volume = 0.0
            self.last_seen = 0.0
        self.launches.append(now)
        self.proc = self._launch(self.url)
        self.started_at = now

    def stop(self) -> None:
        had = self.proc is not None
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=5)
                    except Exception:
                        self.proc.kill()
            except Exception:
                pass
        killed = kill_profile_processes(PLAYER_PROFILE_DIR)
        if had or killed:
            log("Built-in player closed.")
        self.proc = None
        with self.lock:
            self.ready, self.device_id = False, None
            self.last_seen = 0.0

    def close_now(self) -> None:
        """Fast cleanup when Windows is closing the console (about 5 s allowed)."""
        n = kill_profile_processes(PLAYER_PROFILE_DIR)
        log(f"Window closed: stopped the built-in player ({n} process(es)).")

    def shutdown(self) -> None:
        self.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def maintain(self) -> None:
        """Called every tick: keep the window out of sight; reopen the player if it
        really went away (rate-limited, and only a few times)."""
        now = self.clock()
        if self.launches and not self.running():
            # Give a fresh launch time to load before judging it.
            if now - self.started_at >= self.PAGE_TIMEOUT:
                if not self.gave_up:
                    log("Built-in player stopped responding; reopening it.")
                self.proc = None
                self.start()
            return
        with self.lock:
            ready = self.ready
        # Tuck away only once the page has loaded on screen.
        if self.running() and not self.cfg.get("show_player_window") and (ready or now - self.started_at > 20):
            interval = 0.5 if now - self.started_at < 30 else 10
            if now - self.last_hide >= interval:
                self.last_hide = now
                try:
                    self._hide(PLAYER_TITLE)
                except Exception:
                    pass

    def set_volume(self, level: float) -> None:
        with self.lock:
            self.target_volume = max(0.0, min(1.0, level))


# ---------------------------------------------------------------------------
# Local music stations (RadioExt only reads files inside its own folder)
# ---------------------------------------------------------------------------

def station_metadata(display_name: str, fm: float) -> dict:
    return {
        "displayName": display_name,
        "fm": fm,
        "volume": 1.0,
        "icon": "default",
        "customIcon": {"inkAtlasPath": "", "inkAtlasPart": "", "useCustom": False},
        "streamInfo": {"streamURL": "", "isStream": False},
        "order": [],
    }


def sync_local_station(radios_dir: Path, station: dict) -> None:
    name = str(station.get("name") or "").strip()
    source = Path(str(station.get("source") or "")).expanduser()
    fm = float(station.get("fm") or 88.0)
    if not name or any(c in name for c in '\\/:*?"<>|'):
        log(f"Local station skipped: invalid name {name!r}")
        return
    if not source.is_dir():
        log(f"Local station '{name}': source folder not found: {source}")
        return

    dest = radios_dir / name
    dest.mkdir(parents=True, exist_ok=True)
    meta_path = dest / "metadata.json"
    if not meta_path.exists():
        display = f"{fm:g} {name}" if not name[:1].isdigit() else name
        write_json_atomic(meta_path, station_metadata(display, fm))

    manifest_path = dest / ".spotipunkfm_sync.json"
    manifest = read_json(manifest_path, {}) or {}

    wanted: dict[str, Path] = {}
    for root, _dirs, files in os.walk(source):
        for fname in sorted(files):
            src = Path(root) / fname
            if src.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            rel = src.relative_to(source)
            flat = " - ".join(rel.parts)  # RadioExt reads only the top level, so flatten subfolders
            flat = flat.replace("\\", "-")
            wanted[flat] = src

    linked = copied = kept = removed = 0
    for flat, src in wanted.items():
        dst = dest / flat
        entry = manifest.get(flat)
        try:
            src_size = src.stat().st_size
        except OSError:
            continue
        if dst.exists():
            if entry and entry.get("source") == str(src) and dst.stat().st_size == src_size:
                kept += 1
                continue
            if not entry:
                kept += 1  # a file the user put there themselves; leave it alone
                continue
            dst.unlink()
        try:
            os.link(src, dst)  # same drive: no extra disk space used
            linked += 1
        except OSError:
            shutil.copy2(src, dst)  # different drive: copy
            copied += 1
        manifest[flat] = {"source": str(src)}

    for flat in list(manifest):
        if flat not in wanted:
            try:
                (dest / flat).unlink()
            except OSError:
                pass
            del manifest[flat]
            removed += 1

    write_json_atomic(manifest_path, manifest)
    log(f"Local station '{name}': {len(wanted)} songs ({linked} linked, {copied} copied, "
        f"{kept} unchanged, {removed} removed). Restart the game to pick up changes.")


def sync_all_local(cfg: dict, mod_dir: Path | None) -> None:
    stations = cfg.get("local_stations") or []
    if not stations:
        return
    if not mod_dir:
        log("Can't sync local stations: spotipunkFM mod folder not found (set mod_dir in config.json).")
        return
    radios_dir = mod_dir.parent / "radioExt" / "radios"
    if not radios_dir.is_dir():
        log(f"Can't sync local stations: RadioExt's radios folder not found at {radios_dir}. Is RadioExt installed?")
        return
    for st in stations:
        try:
            sync_local_station(radios_dir, st)
        except Exception as e:  # keep going with the other stations
            log(f"Local station '{st.get('name')}' failed to sync: {e}")


# ---------------------------------------------------------------------------
# The bridge: follows the game's state.json and drives Spotify
# ---------------------------------------------------------------------------

class Bridge:
    def __init__(self, cfg: dict, sp: Spotify, mod_dir: Path, mixer: WindowsMixer | None = None,
                 clock=time.time, sleep=time.sleep, launcher=None, player: BrowserPlayer | None = None):
        self.cfg = cfg
        self.sp = sp
        self.player = player  # built-in player; None means "control the Spotify app"
        self.mod_dir = mod_dir
        self.state_path = mod_dir / "state.json"
        self.np_path = mod_dir / "now_playing.json"
        self.mixer = mixer or WindowsMixer()
        self.clock = clock
        self.sleep = sleep
        self.launcher = launcher if launcher is not None else self._launch_spotify

        self.mode = "off"
        self.reason = "waiting for game"
        self.game_running = False
        self.we_control = False      # True once we started/resumed playback for the station
        self.ducked = False
        self.duck_level = 1.0        # factor applied while ducked
        self.game_volume = 1.0       # game's radio volume from state.json (built-in player only)
        self.base_level: float | None = None   # mixer level before ducking
        self.base_percent: int | None = None   # Web API volume before ducking (fallback)
        self.retry_at: float | None = None
        self.error = ""
        self.device: dict | None = None
        self.player_info: dict | None = None
        self.playlists: list = []
        self.user_id: str | None = None
        self.session = None
        self.last_seq = 0
        self.last_player_poll = 0.0
        self.last_playlist_poll = 0.0
        self.last_np_write = 0.0
        self.last_launch = 0.0

    # --- helpers -------------------------------------------------------------

    def _launch_spotify(self) -> None:
        if sys.platform == "win32":
            try:
                os.startfile("spotify:")  # type: ignore[attr-defined]
            except OSError as e:
                log(f"Couldn't start the Spotify app: {e}")

    def set_error(self, msg: str) -> None:
        if msg != self.error and msg:
            log("Problem: " + msg)
        self.error = msg

    def unexpected_error(self, e: Exception) -> None:
        """Log a bug's traceback to companion.log (the console may be closed by
        the time anyone looks) and carry on. Repeats are logged once a minute."""
        key = f"{type(e).__name__}: {e}"
        now = self.clock()
        seen = getattr(self, "_unexpected", {})
        self._unexpected = seen
        count, last = seen.get(key, (0, -1e9))
        seen[key] = (count + 1, last)
        if now - last >= 60:
            more = f" (happened {count + 1} times)" if count else ""
            log(f"Unexpected error, still running{more}: {key}\n" + traceback.format_exc().rstrip())
            seen[key] = (0, now)
        self.set_error(f"internal error: {key}")

    def pick_device(self, devices: list) -> dict | None:
        usable = [d for d in devices if not d.get("is_restricted")]
        want = (self.cfg.get("device_name") or "").strip().lower()
        if want:
            for d in usable:
                if (d.get("name") or "").lower() == want:
                    return d
            return None
        host = socket.gethostname().lower()
        for d in usable:
            if (d.get("name") or "").lower() == host:
                return d
        for d in usable:
            if (d.get("type") or "").lower() == "computer":
                return d
        for d in usable:
            if d.get("is_active"):
                return d
        return None

    def player_device(self, wait: bool = True) -> dict | None:
        p = self.player
        if not p.running():
            try:
                p.start()
            except RuntimeError as e:
                self.set_error(str(e))
                return None
        deadline = self.clock() + (45 if wait else 0)
        while True:
            p.maintain()
            with p.lock:
                ready, did = p.ready, p.device_id
            if ready and did:
                self.device = {"id": did, "name": PLAYER_NAME}
                return self.device
            if self.clock() >= deadline:
                if p.error:
                    self.set_error("built-in player: " + p.error)
                else:
                    self.set_error("built-in player is still starting")
                return None
            self.sleep(0.5)

    def find_device(self, wait: bool = True) -> dict | None:
        if self.player:
            return self.player_device(wait)
        dev = self.pick_device(self.sp.devices())
        if dev or not wait:
            self.device = dev or self.device
            return dev
        if self.cfg.get("launch_spotify", True) and self.clock() - self.last_launch > 60:
            self.last_launch = self.clock()
            log("Spotify app not found on this PC; starting it...")
            self.launcher()
            for _ in range(20):
                self.sleep(1)
                dev = self.pick_device(self.sp.devices())
                if dev:
                    self.device = dev
                    return dev
        return None

    def selected_name(self) -> str:
        uri = self.cfg.get("playlist_uri") or ""
        if uri == LIKED_SONGS:
            return "Liked Songs"
        for p in self.playlists:
            if p["uri"] == uri:
                return p["name"]
        return self.cfg.get("playlist_name") or ""

    def refresh_playlists(self) -> None:
        try:
            self.playlists = self.sp.playlists()
            self.last_playlist_poll = self.clock()
        except SpotifyError as e:
            self.set_error(f"couldn't load playlists ({e.message})")
            # Don't retry every tick: each try can block the loop for API_TIMEOUT.
            self.last_playlist_poll = self.clock() - PLAYLIST_REFRESH + PLAYLIST_RETRY_AFTER_ERROR

    def poll_player(self) -> None:
        try:
            self.player_info = self.sp.player()
            self.last_player_poll = self.clock()
        except SpotifyError as e:
            self.set_error(f"couldn't read what's playing ({e.message})")
            # Back off (this is only for the now-playing label); play/pause/duck
            # commands keep their own 5 s retry in apply().
            self.last_player_poll = self.clock() + POLL_RETRY_AFTER_ERROR - PLAYER_POLL_ACTIVE

    # --- playback actions ----------------------------------------------------

    def start_selected(self, device_id: str) -> None:
        uri = self.cfg.get("playlist_uri") or ""
        shuffle = bool(self.cfg.get("shuffle", True))
        if uri == LIKED_SONGS:
            if not self.user_id:
                self.user_id = (self.sp.me() or {}).get("id")
            try:
                self.sp.play(device_id, context_uri=f"spotify:user:{self.user_id}:collection")
            except SpotifyError:
                uris = self.sp.liked_track_uris(100)
                if not uris:
                    raise SpotifyError(404, "your Liked Songs list is empty")
                if shuffle:
                    random.shuffle(uris)
                self.sp.play(device_id, uris=uris)
        else:
            offset = None
            if shuffle:
                total = next((p.get("total") for p in self.playlists if p["uri"] == uri), None)
                if isinstance(total, int) and total > 1:
                    offset = random.randrange(total)
            self.sp.play(device_id, context_uri=uri, offset=offset)
        try:
            self.sp.shuffle(shuffle, device_id)
        except SpotifyError:
            pass  # shuffle is a nice-to-have
        log(f"Started '{self.selected_name()}' on {self.device.get('name') if self.device else 'Spotify'}")

    def ensure_playing(self) -> bool:
        uri = self.cfg.get("playlist_uri") or ""
        dev = self.find_device()
        if not dev:
            if not self.player:
                self.set_error("Spotify app isn't running on this PC")
            return False
        did = dev["id"]
        p = self.sp.player()
        self.player_info = p
        ctx = ((p or {}).get("context") or {}).get("uri") or ""
        same_device = bool(p) and (p.get("device") or {}).get("id") == did
        liked_ctx = uri == LIKED_SONGS and ctx.endswith(":collection")
        on_selected = (not uri) or ctx == uri or liked_ctx

        if p and p.get("is_playing") and same_device and on_selected:
            pass  # already right
        elif p and on_selected and (self.cfg.get("resume_where_left_off", True) or not uri):
            try:
                self.sp.play(did)  # resume where it was
            except SpotifyError:
                if not uri:
                    raise
                self.start_selected(did)
        elif uri:
            self.start_selected(did)
        else:
            self.sp.play(did)
        self.we_control = True
        self.set_error("" if uri else "no playlist chosen; resumed whatever Spotify had")
        self.last_player_poll = 0  # refresh now-playing soon
        if self.player and not self.ducked:
            self.player.set_volume(self.base_volume())  # fades in
        return True

    def base_volume(self) -> float:
        """Built-in player volume while playing normally: player_volume x the game's
        radio volume sliders (sent by the CET mod; 1.0 when an older mod doesn't)."""
        try:
            own = max(0.0, min(1.0, float(self.cfg.get("player_volume", 0.8))))
        except (TypeError, ValueError):
            own = 0.8
        return own * self.game_volume

    @staticmethod
    def parse_game_volume(state: dict) -> float:
        v = state.get("volume")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            return 1.0  # field missing (older CET mod / setting off) or junk: full volume
        return max(0.0, min(1.0, float(v)))

    def set_game_volume(self, v: float, apply_now: bool = True) -> None:
        if abs(v - self.game_volume) < 0.004:
            return
        log(f"Game radio volume: {round(v * 100)}%")
        self.game_volume = v
        # Apply now if music is meant to be audible; otherwise the next play/duck picks it up.
        if apply_now and self.player and self.mode in ("play", "duck") and self.we_control:
            self.player.set_volume(self.base_volume() * (self.duck_level if self.ducked else 1.0))

    def pause(self) -> None:
        if not self.we_control:
            return
        try:
            p = self.sp.player()
            self.player_info = p
            if p and p.get("is_playing"):
                self.sp.pause((p.get("device") or {}).get("id"))
        except SpotifyError as e:
            if e.status not in (403, 404):  # 403/404 = already paused / no device
                raise

    def duck(self, level: float) -> None:
        if self.ducked:
            return
        if self.player:
            self.duck_level = level
            self.player.set_volume(self.base_volume() * level)  # the page fades it
            self.ducked = True
            return
        if self.mixer.ok:
            current = self.mixer.get()
            if current is not None:
                self.base_level = current
                self.mixer.fade(current, current * level, int(self.cfg.get("fade_ms", 400)))
                self.ducked = True
                return
        p = self.player_info or self.sp.player() or {}
        percent = (p.get("device") or {}).get("volume_percent")
        if isinstance(percent, int):
            self.base_percent = percent
            self.sp.volume(round(percent * level), (p.get("device") or {}).get("id"))
            self.ducked = True

    def unduck(self) -> None:
        if not self.ducked:
            return
        self.ducked = False
        if self.player:
            if self.mode in ("play", "duck"):
                self.player.set_volume(self.base_volume())
            return
        if self.base_level is not None and self.mixer.ok:
            current = self.mixer.get()
            if current is not None:
                self.mixer.fade(current, self.base_level, int(self.cfg.get("fade_ms", 400)))
            self.base_level = None
            return
        if self.base_percent is not None:
            dev = (self.player_info or {}).get("device") or self.device or {}
            try:
                self.sp.volume(self.base_percent, dev.get("id"))
            except SpotifyError:
                pass
            self.base_percent = None

    def apply(self, mode: str, duck_level: float) -> None:
        try:
            if mode == "play":
                if self.ensure_playing():
                    self.unduck()
                    self.retry_at = None
                else:
                    self.retry_at = self.clock() + 5
            elif mode == "duck":
                if self.ensure_playing():
                    self.duck(duck_level)
                    self.retry_at = None
                else:
                    self.retry_at = self.clock() + 5
            else:  # pause / off
                self.pause()
                self.unduck()  # after pausing, so the volume jump isn't heard
                if self.player:
                    self.player.set_volume(0.0)  # next resume fades in
                self.retry_at = None
        except SpotifyError as e:
            if e.reason == "PREMIUM_REQUIRED" or (e.status == 403 and "premium" in e.message.lower()):
                self.set_error("Spotify Premium is required for playback control")
            else:
                self.set_error(f"Spotify said: {e.message}")
            self.retry_at = self.clock() + 5

    # --- commands from the in-game overlay -------------------------------------

    def handle_commands(self, state: dict) -> None:
        session = state.get("session")
        cmds = [c for c in (state.get("commands") or []) if isinstance(c, dict)]
        newest = max((int(c.get("seq") or 0) for c in cmds), default=0)
        if session != self.session:
            # New game session (or we just started): don't replay old button presses.
            self.session = session
            self.last_seq = newest
            return
        for c in sorted(cmds, key=lambda c: int(c.get("seq") or 0)):
            seq = int(c.get("seq") or 0)
            if seq <= self.last_seq:
                continue
            self.last_seq = seq
            try:
                self.run_command(c.get("action"), c.get("arg"))
            except SpotifyError as e:
                self.set_error(f"'{c.get('action')}' failed: {e.message}")

    def run_command(self, action, arg) -> None:
        dev_id = ((self.player_info or {}).get("device") or self.device or {}).get("id")
        active = self.mode in ("play", "duck")
        if action == "next" and active:
            self.sp.next(dev_id)
        elif action == "previous" and active:
            self.sp.previous(dev_id)
        elif action == "set_playlist" and isinstance(arg, str) and arg:
            self.cfg["playlist_uri"] = arg
            self.cfg["playlist_name"] = self.selected_name()
            save_config(self.cfg)
            log(f"Playlist changed to '{self.cfg['playlist_name']}'")
            if active:
                dev = self.find_device()
                if dev:
                    self.start_selected(dev["id"])
                    self.we_control = True
        elif action == "set_shuffle":
            self.cfg["shuffle"] = bool(arg)
            save_config(self.cfg)
            if active and dev_id:
                self.sp.shuffle(bool(arg), dev_id)
        elif action == "refresh_playlists":
            self.refresh_playlists()
        self.last_player_poll = 0

    # --- main loop ------------------------------------------------------------

    def read_state(self) -> dict | None:
        try:
            mtime = self.state_path.stat().st_mtime
        except OSError:
            return None
        data = read_json(self.state_path)
        if not isinstance(data, dict):
            return None  # caught the file mid-write; try again next tick
        data["_fresh"] = max(float(data.get("ts") or 0), mtime) >= self.clock() - STALE_AFTER
        return data

    def write_now_playing(self) -> None:
        p = self.player_info or {}
        item = p.get("item") or {}
        ctx = (p.get("context") or {}).get("uri") or ""
        playlist_name = ""
        if ctx.endswith(":collection"):
            playlist_name = "Liked Songs"
        for pl in self.playlists:
            if pl["uri"] == ctx:
                playlist_name = pl["name"]
        artists = ", ".join(a.get("name", "") for a in (item.get("artists") or []) if a)
        if not artists and item.get("show"):
            artists = item["show"].get("name", "")
        data = {
            "v": 1,
            "ts": int(self.clock()),
            "companion": VERSION,
            "device": (p.get("device") or self.device or {}).get("name", ""),
            "is_playing": bool(p.get("is_playing")),
            "track": item.get("name", ""),
            "artist": artists,
            "album": (item.get("album") or {}).get("name", ""),
            "playlist_name": playlist_name,
            "shuffle": bool(p.get("shuffle_state", self.cfg.get("shuffle"))),
            "selected_uri": self.cfg.get("playlist_uri") or "",
            "selected_name": self.selected_name(),
            "playlists": [{"name": "Liked Songs", "uri": LIKED_SONGS}]
                         + [{"name": x["name"], "uri": x["uri"]} for x in self.playlists[:300]],
            "error": self.error,
        }
        try:
            write_json_atomic(self.np_path, data)
            self.np_write_failures = 0
        except OSError as e:
            # The game may be reading the file at this exact moment; try again next second.
            self.np_write_failures = getattr(self, "np_write_failures", 0) + 1
            if self.np_write_failures == 10:
                log(f"Couldn't write now_playing.json for a while: {e}")
        self.last_np_write = self.clock()

    def tick(self) -> None:
        state = self.read_state()
        now = self.clock()

        if state and state["_fresh"]:
            if not self.game_running:
                log("Game detected.")
                self.game_running = True
                if not self.playlists or now - self.last_playlist_poll > PLAYLIST_REFRESH:
                    self.refresh_playlists()
                if self.player:
                    try:
                        self.player.start()  # warm up now so tuning in is instant
                    except RuntimeError as e:
                        self.set_error(str(e))
            new_mode = state.get("mode") if state.get("mode") in ("play", "duck", "pause", "off") else "off"
            new_reason = str(state.get("reason") or "")
            duck_level = float(state.get("duckVolume") or 0.3)
            if self.player:
                # Same mode: follow the slider now. On a mode change apply() below
                # picks up the new value through base_volume().
                self.set_game_volume(self.parse_game_volume(state), apply_now=new_mode == self.mode)
            self.handle_commands(state)
        else:
            if self.game_running:
                log("Game closed (no heartbeat).")
                self.game_running = False
                self.session = None
            new_mode, new_reason, duck_level = "off", "game not running", 0.3

        if new_mode != self.mode:
            log(f"Game: {self.mode} -> {new_mode}" + (f" ({new_reason})" if new_reason else ""))
            self.mode, self.reason = new_mode, new_reason
            self.apply(new_mode, duck_level)
        elif self.retry_at and now >= self.retry_at:
            self.apply(self.mode, duck_level)

        if self.player:
            # The player stays open (hidden) for as long as the companion runs:
            # starting it while a full-screen game is up leaves it silent.
            self.player.maintain()

        if self.game_running:
            interval = PLAYER_POLL_ACTIVE if self.mode in ("play", "duck") else PLAYER_POLL_IDLE
            if now - self.last_player_poll >= interval:
                self.poll_player()
            if now - self.last_playlist_poll >= PLAYLIST_REFRESH:
                self.refresh_playlists()
            if now - self.last_np_write >= 1.0:
                self.write_now_playing()

    def run(self, stop_check=None) -> None:
        """Main loop. stop_check (background mode): called every tick; returning
        True ends the loop cleanly (pause, close the player)."""
        log(f"SpotiPunk FM companion {VERSION} running. Watching {self.state_path}")
        if stop_check is None:
            log("Leave this window open while you play. Press Ctrl+C to quit.")
        if self.player:
            log("Player: built-in (hidden). The Spotify app is not needed.")
            try:
                self.player.start()
                print("A small 'SpotiPunk FM Player' window may flash for a second; it hides itself.")
            except RuntimeError as e:
                self.set_error(str(e))
        elif self.mixer.ok:
            log("Player: Spotify app. Volume ducking: Windows mixer (smooth fades).")
        else:
            log("Player: Spotify app. Volume ducking: Spotify volume setting.")
        try:
            while True:
                try:
                    self.tick()
                except SpotifyError as e:
                    self.set_error(f"Spotify said: {e.message}")
                    self.sleep(2)
                except Exception as e:  # never let one bad tick end the session
                    self.unexpected_error(e)
                    self.sleep(2)
                if stop_check is not None and stop_check():
                    log("Stopping: " + str(getattr(stop_check, "reason", "done")) + ".")
                    break
                self.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log("Stopping.")
        finally:
            try:
                self.unduck()
            except Exception:
                pass
            if self.player:
                try:
                    self.pause()
                except Exception:
                    pass
                self.player.shutdown()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

SETUP_HELP = """
One-time Spotify setup (about 3 minutes)
----------------------------------------
1. Go to https://developer.spotify.com/dashboard and log in with your Spotify account.
2. Click "Create app". Any name and description is fine (e.g. "SpotiPunk FM").
   Redirect URI:  http://127.0.0.1:{port}/callback
   Under "Which API/SDKs are you planning to use?" tick "Web API" and "Web Playback SDK". Save.
3. Open the app's Settings, copy the "Client ID", and paste it below.
   (Your account is already allowed to use its own app. A Client Secret is not needed.)
"""


def choose_playlist(cfg: dict, sp: Spotify) -> None:
    playlists = sp.playlists()
    print("\nWhich playlist should SpotiPunk FM play?")
    print("   0. Liked Songs")
    for i, p in enumerate(playlists, 1):
        total = f" ({p['total']} tracks)" if isinstance(p.get("total"), int) else ""
        print(f"  {i:>2}. {p['name']}{total}")
    while True:
        choice = input("Number (Enter = Liked Songs): ").strip() or "0"
        if choice.isdigit() and 0 <= int(choice) <= len(playlists):
            break
        print("Please type one of the numbers above.")
    n = int(choice)
    if n == 0:
        cfg["playlist_uri"], cfg["playlist_name"] = LIKED_SONGS, "Liked Songs"
    else:
        cfg["playlist_uri"], cfg["playlist_name"] = playlists[n - 1]["uri"], playlists[n - 1]["name"]
    save_config(cfg)
    print(f"SpotiPunk FM will play: {cfg['playlist_name']}. You can change it in game from the CET overlay.")


def setup(cfg: dict, force: bool) -> Spotify:
    if force or not cfg.get("client_id"):
        print(SETUP_HELP.format(port=cfg["redirect_port"]))
        while True:
            cid = input("Client ID: ").strip()
            if len(cid) == 32 and all(c in "0123456789abcdef" for c in cid.lower()):
                break
            print("That doesn't look like a Client ID (32 letters and numbers). Try again.")
        cfg["client_id"] = cid
        save_config(cfg)

    sp = Spotify(cfg["client_id"], int(cfg["redirect_port"]))
    if force or not sp.has_login():
        sp.login()
    elif not sp.has_scopes(SCOPES):
        print("\nSpotiPunk FM can now play music by itself, without the Spotify app.\n"
              "Spotify needs to approve that once: your browser will open, click Agree.\n"
              "(If Spotify shows an error, open your app at developer.spotify.com/dashboard,\n"
              " go to Settings > Edit, tick \"Web Playback SDK\" under APIs used, save, and run this again.)\n")
        sp.login()
    try:
        me = sp.me() or {}
    except SpotifyError as e:  # e.g. network not up yet at game start: carry on, the bridge retries
        log(f"Couldn't check the Spotify account yet ({e.message}); continuing.")
        me = {}
    product = me.get("product")
    log(f"Spotify account: {me.get('display_name') or me.get('id')} ({product or 'unknown plan'})")
    if product and product != "premium":
        log("Warning: this account isn't Premium. Spotify only allows playback control for Premium accounts.")
    if force or not cfg.get("playlist_uri"):
        choose_playlist(cfg, sp)
    return sp


def ask(question: str) -> bool:
    while True:
        a = input(question + " (y/n): ").strip().lower()
        if a in ("y", "yes", "n", "no"):
            log(f"Sound test: {question} -> {a}")
            return a.startswith("y")


def sound_test(cfg: dict, sp: Spotify) -> int:
    """Starts the built-in player exactly like during the game (out of sight),
    plays the chosen playlist, tests pause/resume, and records what the user
    hears. No game needed."""
    log(f"Sound test (companion {VERSION})")
    if not find_browser(cfg):
        print("Microsoft Edge or Google Chrome wasn't found.")
        return 1
    test_cfg = dict(cfg, show_player_window=True)
    player = BrowserPlayer(test_cfg, sp, int(cfg["redirect_port"]))
    install_close_handler(player.close_now)
    bridge = Bridge(test_cfg, sp, Path(find_mod_dir(cfg) or HERE), player=player)
    try:
        print("\nStarting the player the same way as during the game:\n"
              "a small window flashes, then moves out of sight.")
        bridge.refresh_playlists()
        if not bridge.player_device(wait=True):
            print("The player didn't get ready: " + (player.error or bridge.error or "unknown reason"))
            return 1
        player.cfg["show_player_window"] = False
        player.started_at = time.time()
        for _ in range(6):
            player.maintain()
            time.sleep(0.5)
        bridge.mode = "play"
        try:
            bridge.ensure_playing()
        except SpotifyError as e:
            print("Spotify refused to play: " + e.message)
            log("Sound test: play failed: " + e.message)
            return 1
        print("Playing your music now, with the player out of sight. Give it 10 seconds.")
        time.sleep(10)
        heard = ask("Do you hear music?")
        if heard:
            print("\nTesting pause and resume (like a cutscene). Wait 10 seconds.")
            bridge.mode = "pause"; bridge.apply("pause", 0.3)
            time.sleep(3)
            bridge.mode = "play"; bridge.apply("play", 0.3)
            time.sleep(7)
            ask("Did the music stop briefly and then come back?")
        else:
            print("\nBringing the player window back on screen. Wait 10 seconds.")
            player.cfg["show_player_window"] = True
            if sys.platform == "win32":
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = user32.FindWindowW(None, PLAYER_TITLE)
                if hwnd:
                    user32.SetWindowPos(hwnd, None, 200, 200, 0, 0, 0x1 | 0x4)
            bridge.apply("play", 0.3)
            time.sleep(10)
            if not ask("Do you hear music now?"):
                print("\nClick the 'Start sound' button in the SpotiPunk FM Player window, wait 5 seconds.")
                input("Press Enter here after clicking... ")
                time.sleep(5)
                ask("Do you hear music now?")
        print("\nThanks! The answers are saved in companion.log.")
        return 0
    finally:
        try:
            bridge.pause()
        except Exception:
            pass
        player.shutdown()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SpotiPunk FM companion for Cyberpunk 2077")
    ap.add_argument("--setup", action="store_true", help="redo Spotify login and playlist choice")
    ap.add_argument("--sync", action="store_true", help="sync local-music stations and exit")
    ap.add_argument("--add-folder", metavar="PATH", help="add a folder of music as a new radio station")
    ap.add_argument("--name", help="station name for --add-folder (default: folder name + ' FM')")
    ap.add_argument("--fm", type=float, help="FM number for --add-folder (sets its place in the list)")
    ap.add_argument("--test-player", action="store_true",
                    help="play your music on the built-in player for a quick sound check (no game needed)")
    ap.add_argument("--background", action="store_true",
                    help="no questions, exit by itself after the game closes (used by start_with_game.pyw)")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not CONFIG_PATH.exists():
        save_config(cfg)

    mod_dir = find_mod_dir(cfg)
    if not mod_dir:
        print("Couldn't find the spotipunkFM mod folder.\nIt is normally the folder above this one:\n"
              "  Cyberpunk 2077\\bin\\x64\\plugins\\cyber_engine_tweaks\\mods\\spotipunkFM")
        entered = input("Paste the full path to that folder: ").strip().strip('"')
        if not (Path(entered) / "init.lua").exists():
            print("init.lua isn't in that folder. Check the path and try again.")
            return 1
        cfg["mod_dir"] = entered
        save_config(cfg)
        mod_dir = Path(entered)

    if args.add_folder:
        src = Path(args.add_folder).expanduser()
        if not src.is_dir():
            print(f"Folder not found: {src}")
            return 1
        name = args.name or f"{src.name} FM"
        fm = args.fm if args.fm is not None else round(random.uniform(88.1, 106.9), 1)
        stations = [s for s in cfg.get("local_stations", []) if s.get("name") != name]
        stations.append({"name": name, "fm": fm, "source": str(src)})
        cfg["local_stations"] = stations
        save_config(cfg)
        log(f"Added local station '{name}' at {fm:g} FM from {src}")
        sync_all_local(cfg, mod_dir)
        return 0

    if args.background:
        log(f"Companion {VERSION} started in the background with the game.")
        why = needs_interactive_setup(cfg)
        if why:
            notify_user(f"SpotiPunk FM can't start by itself yet ({why}).\n\n"
                        "Double-click run_companion.bat once, finish the setup, then close it. "
                        "After that it starts with the game again.")
            return 2

    sync_all_local(cfg, mod_dir)
    if args.sync:
        return 0

    sp = setup(cfg, force=args.setup)
    if args.test_player:
        return sound_test(cfg, sp)
    player = None
    if str(cfg.get("player", "browser")).lower() != "app":
        if not find_browser(cfg):
            log("Microsoft Edge or Google Chrome wasn't found, so the built-in player can't run. "
                "Using the Spotify app instead.")
        else:
            try:
                player = BrowserPlayer(cfg, sp, int(cfg["redirect_port"]))
            except SystemExit as e:
                if args.background:  # a companion window is already open: it does the job
                    log("Another SpotiPunk FM companion is already running; this one exits.")
                    return 0
                raise
            install_close_handler(player.close_now)
    bridge = Bridge(cfg, sp, mod_dir, player=player)
    bridge.run(stop_check=GameWatch(bridge) if args.background else None)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        log(f"Companion stopped by an error: {type(e).__name__}: {e}\n" + traceback.format_exc().rstrip())
        raise
