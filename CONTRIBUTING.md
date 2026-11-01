# Contributing to SpotiPunk FM

Thanks for helping. This file covers how the mod is put together, the rules that keep it from breaking people's games, and how to run the tests.

## Layout

`package/` mirrors the Cyberpunk 2077 game folder exactly. Installing = copying `package/bin` over the game folder.

| Path | What it is |
|---|---|
| `package/bin/x64/plugins/cyber_engine_tweaks/mods/spotipunkFM/init.lua` | CET mod: game-state detection, overlay, hotkeys, `state.json`, now-playing label, game volume |
| `.../spotipunkFM/companion/spotipunk_fm.py` | Companion: Spotify Web API (PKCE), bridge, built-in player, local stations, sound test, background mode |
| `.../spotipunkFM/companion/start_with_game.pyw` | Steam launch option wrapper: starts the companion hidden, then the game |
| `.../spotipunkFM/companion/*.bat`, `requirements.txt` | Launchers (**CRLF** line endings) and optional deps (`pycaw`, `psutil`) |
| `.../mods/radioExt/radios/SpotiPunk FM/metadata.json` | The RadioExt station definition |
| `package/README.md` | User guide (also shipped as `spotipunkFM/README.md`, keep both identical) |
| `tests/`, `tools/` | Tests and a crash-dump reader |

Bump `VERSION` in `init.lua` and/or `spotipunk_fm.py` with every change.

Don't modify or redistribute third-party code: RadioExt (its license forbids it; we only use `GetMod("radioExt")` at runtime), CET, RED4ext. `modules/GameUI.lua` is vendored unmodified from psiberx/cp2077-cet-kit (MIT).

## How it works

```
Cyberpunk 2077 + CET + RED4ext + RadioExt
 └─ mods/spotipunkFM/init.lua ── writes state.json (≤1 s) ──►  companion/spotipunk_fm.py
                               ◄── reads now_playing.json (1 s) ─┤
                                                                  ├─ Spotify Web API
                                                                  └─ built-in player: Web Playback SDK page on
                                                                     127.0.0.1:8898, in Edge/Chrome --app with
                                                                     its own profile (companion/.player-profile)
```

### File protocol (keep it backwards compatible)

`state.json` (CET mod → companion):
```json
{"v":1,"mod":"1.0.3","session":<os.time at init>,"ts":<unix s>,"hb":<counter>,
 "mode":"play|duck|pause|off","reason":"scene|dialogue|holocall|menu|...","duckVolume":0.3,
 "volume":0.12,
 "commands":[{"seq":N,"action":"next|previous|set_playlist|set_shuffle|refresh_playlists","arg":...}]}
```
- `volume` is optional: `MasterVolume/100 × CarRadioVolume/100` in a vehicle, `× RadioportVolume/100` on foot (the game's `/audio/volume` settings, ints 0-100). Missing or junk means 1.0.
- Commands are de-duplicated by `seq` and reset when `session` changes.

`now_playing.json` (companion → CET mod): `ts, companion, device, is_playing, track, artist, album, playlist_name, shuffle, selected_uri, selected_name, playlists[{name,uri}], error`.

The companion treats the game as closed if `state.json` is more than 8 s old. The CET mod treats the companion as offline after 10 s.

### Modes (`computeMode` in init.lua)

`off`: station not tuned, or car radio off. `pause`: menu, loading, braindance, photo mode, cyberspace, scene tier ≥ 3, flashback, combat (optional). `duck`: holocall, scene tier 2, dialogue choices on screen. Otherwise `play`. Debounced (0.25 s scene/holocall, 0.4 s dialogue, 0.6 s otherwise).

## Rules that each came from a real failure

1. **CET doesn't type-check native calls.** Passing `nil` or a plain Lua value where the game expects an object crashes the game, and `pcall` can't catch it. Every value going into a native call in `init.lua` passes `isNative()`. Blackboard lookups go through `blackboardField()`. Every detection runs inside `probe()`, and returns `NOT_AVAILABLE` when its game data doesn't exist, which switches it off for good.
2. **Never relaunch a process without a rate limit.** A browser started on an already-open profile hands over and exits at once, which looks like "it closed". The player is judged alive by its page heartbeat. Relaunches wait ≥ 30 s, at most 3 per 10 minutes. Leftover profile processes are killed before a launch.
3. **Single instance:** `ExclusiveServer` uses `SO_EXCLUSIVEADDRUSE` on Windows.
4. **Browsers won't start new audio in a hidden page.** The player window loads on screen, then moves to (-32000, -32000) as a `WS_EX_TOOLWINDOW`, still "shown", so the page stays `visible`. Never `SW_HIDE` it. That's also why the Steam launcher waits for the player before starting the game.
5. **Full-screen games make pages "occluded".** Launch with `--disable-features=CalculateNativeWinOcclusion` and `--disable-background-media-suspend`.
6. **`urllib` doesn't wrap every network error.** Read timeouts and dropped connections come through as raw `TimeoutError`/`RemoteDisconnected`/... All Spotify HTTP calls map `NETWORK_ERRORS` to `SpotifyError`. The bridge loop also catches anything else, logs the traceback to `companion.log` and keeps going. Failed polls back off (30 s / 60 s).
7. **Tracebacks must reach `companion.log`.** The console may be gone, and background mode has none.
8. **The game must always start.** `start_with_game.pyw` uses the stdlib only, never imports the companion, and wraps every companion step.
9. **Verify deployments** (size/hash) after copying into a game folder. Don't copy `__pycache__/`, which the tests create next to the companion.

## Tests

The CET mod runs against a mocked game. The companion runs against a fake Spotify Web API, and for browser tests a real Chromium with a stand-in SDK.

```bash
C=package/bin/x64/plugins/cyber_engine_tweaks/mods/spotipunkFM/companion/spotipunk_fm.py
M=package/bin/x64/plugins/cyber_engine_tweaks/mods/spotipunkFM
export NO_PROXY=127.0.0.1,localhost

# CET mod (LuaJIT 2.1, like CET). Run from an empty temp dir: it writes state.json etc. into the cwd.
luajit tests/lua/run_cet_mock.lua $M tests/lua/json.lua

python -I -B tests/python/test_bridge.py   $C   # bridge, network errors, game volume, background mode
python -I -B tests/python/test_login.py    $C   # PKCE login flow
python -I -B tests/python/test_launcher.py $(dirname $C)/start_with_game.pyw   # Steam launcher
python -I -B tests/python/test_closehandler.py $C    # Windows only: closing the console still cleans up
CHROME=/path/to/chromium
python -I -B tests/python/test_browser.py   $C "$CHROME"   # built-in player end to end
python -I -B tests/python/test_soundtest.py $C "$CHROME"
python -I -B tests/python/test_runaway.py   $C "$CHROME"   # relaunch storm, leftovers, single instance
```

No LuaJIT on Windows? `pip install lupa` (it bundles LuaJIT 2.1) in a throwaway venv. Then set `arg = {modDir, jsonLua}`, add `modDir` to `package.path`, and `dofile` the harness.

The fake SDK can't reproduce Widevine DRM or real window behaviour. Changes to the player need a real sound test (`sound_test.bat`) and a real game session.

## Logs when something goes wrong

`companion/companion.log`, `companion/launcher.log`, `mods/spotipunkFM/spotipunkFM.log` (first-run trace of each detection), `cyber_engine_tweaks.log`, `red4ext/logs/`. Game crash reports are in `%LOCALAPPDATA%\REDEngine\ReportQueue\`. `tools/read_crash_dump.py <dump>` prints the faulting module and offset.
