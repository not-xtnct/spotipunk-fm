# SpotiPunk FM for Cyberpunk 2077 (patch 2.5)

> **Unofficial fan mod.** SpotiPunk FM is not affiliated with, endorsed by or sponsored by Spotify AB or CD PROJEKT RED. "Spotify" is a trademark of Spotify AB, "Cyberpunk 2077" of CD PROJEKT S.A. It uses Spotify's public Web API and Web Playback SDK with **your own** Spotify developer app and Premium account, and it streams nothing anywhere but your own PC.

Adds two kinds of station to the in-game radio, on foot and in vehicles:

- **107.7 SpotiPunk FM** plays your own Spotify playlist (or Liked Songs) on this PC.
- **Local music stations** play any folder of mp3 / flac / wav / ogg files on your drive.

Both behave like the vanilla stations. SpotiPunk FM also follows what's happening in the game:

| In game | Spotify |
|---|---|
| You tune in to SpotiPunk FM (car or pocket radio) | Your playlist starts, or resumes where it left off |
| Cutscene, locked dialogue, braindance, photo mode, loading | Pauses, then resumes afterwards |
| Walk-and-talk dialogue, dialogue choices on screen, holocalls | Volume drops, then comes back |
| Pause menu / inventory | Pauses |
| You switch station, turn the radio off, or a quest kills the car radio | Pauses |
| Game closes | Pauses |
| Track changes | The vanilla radio popup shows the Spotify track and artist |

Each of these can be switched on or off in the CET overlay.

## How it fits together

```
Cyberpunk 2077
  RadioExt ............ shows the "SpotiPunk FM" station (it has no audio files of its own)
  spotipunkFM (CET mod) . watches the station and the game state  ──►  state.json
                                                                       │
SpotiPunk FM companion (Python, runs in a window next to the game) ◄────┘
  ├─ built-in player: Spotify's official Web Playback SDK in a hidden Edge window
  ├─ Spotify Web API ─► plays / pauses / changes volume on that player
  └─ writes now_playing.json ─► shown in the game's radio popup
```

The music comes from the companion's **built-in player**, not the game engine. The built-in player is Spotify's official Web Playback SDK running in a hidden Microsoft Edge (or Chrome) window with its own separate profile. Your normal browser is untouched, and **the Spotify app is not needed**. Spotify's streams are DRM-protected, so no mod can feed them into the game's own audio. In practice this means:

- **SpotiPunk FM follows the game's volume sliders**, the same ones the game's radio uses: **Master × Car radio** in a vehicle, **Master × Radioport** on foot (Settings → Sound, or the volume control in the car radio). The companion copies them over twice a second. At 100% sliders SpotiPunk FM plays at `player_volume` (0.8). Ducking for calls and dialogue applies on top. This works with the built-in player only. In app mode, set the level in Spotify or the Windows volume mixer.
- There's no in-car muffling when you look out of the window. The music is the same on foot and in the car.
- The hidden player starts with the companion (a small window flashes for a second, then hides) and closes when you close the companion. Start the companion before the game. In Spotify it shows up as the device "SpotiPunk FM (Cyberpunk 2077)".
- Prefer the Spotify app? Set `"player": "app"` in `companion\config.json`. The companion then controls the Spotify app instead, and starts it if needed.

Local music stations are ordinary RadioExt stations. They play inside the game and use the game's volume slider.

> **First time?** The step-by-step guide with every click and common issues is `SETUP.md` in the download, or online at https://github.com/not-xtnct/spotipunk-fm/blob/main/SETUP.md

## Requirements

- Cyberpunk 2077 **2.5** on PC (this cannot work on consoles)
- [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107), [RED4ext](https://www.nexusmods.com/cyberpunk2077/mods/2380) and [RadioExt](https://www.nexusmods.com/cyberpunk2077/mods/4591), all updated for 2.5 (RadioExt's 2.5 update is from 25 Oct 2026)
- **Spotify Premium**: Spotify only allows playback control for Premium accounts
- [Python 3.10 or newer](https://www.python.org/downloads/) with "Add python.exe to PATH" ticked during install

## Install

1. Install CET, RED4ext and RadioExt first. Start the game once to check they load.
2. Copy this package's `bin` folder into your `Cyberpunk 2077` folder (the one that already has `bin`, `archive` and `r6`). Merge folders if Windows asks.
3. You should now have:
   ```
   Cyberpunk 2077\bin\x64\plugins\cyber_engine_tweaks\mods\
       radioExt\radios\SpotiPunk FM\metadata.json
       spotipunkFM\init.lua
       spotipunkFM\companion\run_companion.bat
   ```

## One-time Spotify setup (about 3 minutes)

1. Open https://developer.spotify.com/dashboard and log in with your Spotify account.
2. Click **Create app**. Any name works (e.g. "SpotiPunk FM").
   - **Redirect URI:** `http://127.0.0.1:8898/callback` (type it exactly, then click Add)
   - **APIs used:** tick **Web API** and **Web Playback SDK**
3. Save. Open the app's **Settings** and copy the **Client ID**.
4. Double-click `spotipunkFM\companion\run_companion.bat`. Paste the Client ID when asked.
5. Your browser opens. Log in and click **Agree**.
6. Pick a playlist from the numbered list (0 = Liked Songs).

Your login is saved in `companion\tokens.json`, so later runs go straight to the bridge. Don't share that file.

## Every time you play

**Easiest: let Steam start it with the game (one-time setup).**
1. Double-click `steam_launch_option.bat` in the companion folder. It copies one line to the clipboard.
2. In Steam, right-click Cyberpunk 2077 → **Properties** → **General** → click into **Launch options** → Ctrl+V.
3. From now on just press Play in Steam. The companion starts hidden (no window), and the game starts a few seconds later. The companion closes itself about 20 seconds after you quit. To turn this off, clear the Launch options box.

This is a normal Steam setting and doesn't change any game files. The game always starts, even if the companion can't (see `launcher.log`). If Spotify needs you to log in again, a "SpotiPunk FM" message box tells you to run `run_companion.bat` once.

**Or by hand:**
1. Double-click `run_companion.bat` and leave its window open. You don't need to open Spotify.
2. Start the game. Tune in to **107.7 SpotiPunk FM** in a car or with the pocket radio.

If a companion window is already open when Steam starts the game, the hidden one steps aside.

Open the CET overlay to see the current track, skip tracks, change playlist, toggle shuffle, and change the pause/duck behaviour. Under CET's **Bindings** you can set hotkeys for *SpotiPunk FM: next track* and *previous track*.

## Local music stations

You don't need Spotify for these. Double-click `add_music_folder.bat`, drag your music folder into the window, give the station a name and an FM number. Restart the game and the station appears in the list.

Or from a command prompt in the companion folder:

```
run_companion.bat --add-folder "D:\Music\Synthwave" --name "Synthwave FM" --fm 89.3
```

What it does: RadioExt can only play files inside its own `radios\<station>` folder. The companion mirrors your folder there. Files on the same drive as the game are **hard-linked** (no extra disk space). Files on another drive are copied. Subfolders are flattened into one list. The companion re-syncs every time it starts, so new songs show up after you restart the game. Songs you delete from your folder are removed from the station. Files you placed in the station folder yourself are never touched.

Song titles in game come from the file names, so `Artist - Title.mp3` naming looks best. Very large folders (thousands of songs) slow RadioExt down at game start.

## Settings

**In game (CET overlay → SpotiPunk FM → Behaviour)**, saved to `spotipunkFM\settings.json`:

| Setting | Default |
|---|---|
| Pause in cutscenes / locked dialogue | on |
| Lower volume during dialogue | on |
| Lower volume during holocalls | on |
| Pause in combat | off (vanilla radio keeps playing in combat) |
| Stop when a quest turns the car radio off | on |
| Show radio popup on track change | on |
| Ducked volume | 0.30 (a share of the current volume) |
| Follow the game's radio volume sliders | on |

**Companion (`companion\config.json`)**:

| Key | Meaning |
|---|---|
| `playlist_uri` | What SpotiPunk FM plays. Easiest to change from the overlay, or run `run_companion.bat --setup` |
| `shuffle` | Start at a random track and shuffle |
| `resume_where_left_off` | `true`: re-tuning continues the playlist. `false`: start fresh each time |
| `player` | `"browser"` (default): built-in hidden player, no Spotify app needed. `"app"`: control the Spotify app |
| `player_volume` | Built-in player volume at 100% game sliders, 0.0 to 1.0 (default 0.8). The game's sliders scale it down from there |
| `show_player_window` | `true` shows the hidden player window, for troubleshooting |
| `browser_path` | Leave empty to find Edge or Chrome automatically |
| `device_name` | App mode only: leave empty for this PC's Spotify app, or name another Spotify device |
| `launch_spotify` | App mode only: start the Spotify app if it isn't running |
| `fade_ms` | Length of volume fades when ducking |
| `mod_dir` | Only needed if you move the companion folder (see below) |
| `local_stations` | The folders added with `--add-folder` |

Run `run_companion.bat --setup` to change Client ID, log in again, or pick a different playlist.

## Troubleshooting

- **Audio choppy, cutting out or silent the first time:** normal on the first run or two, while the built-in player sets itself up. Close the game and the companion (or restart the PC) and start again. That almost always fixes it.

- **Station not in the list:** RadioExt isn't loaded. Check the CET console for `[RadioExt]` errors and update RadioExt / RED4ext for 2.5. The console also shows `Station "107.7 SpotiPunk FM" is not a stream and has no song files`. That warning is expected, because the station deliberately has no audio.
- **Overlay says "Companion: not running":** start `run_companion.bat`.
- **"built-in player: init: ..." or no sound on first run:** a fresh player profile downloads Edge's DRM module (Widevine) during its first minute. The player retries by itself every 15 seconds. If it keeps failing, set `"show_player_window": true` to see the player page, or switch to `"player": "app"`.
- **"Spotify app isn't running on this PC"** (app mode): open Spotify and play any song once, so it registers as a device.
- **"Spotify Premium is required":** Spotify's rule, not the mod's.
- **`INVALID_CLIENT: Invalid redirect URI` when logging in:** the Redirect URI in your Spotify app must be exactly `http://127.0.0.1:8898/callback`.
- **Game under `Program Files` (Epic, some installs) and the companion can't create files:** copy the `companion` folder somewhere like `Documents\SpotiPunkFM`. Run it there, and when asked, paste the path to `...\mods\spotipunkFM`. It's saved as `mod_dir` in `config.json`.
- **A pause/duck rule misfires:** switch it off in the overlay. If the overlay reports that a detection was "switched off after errors", a game update changed something it relies on. Use the other rules and report it.
- **Logs:** `companion\companion.log` and the CET console (`[SpotiPunkFM]` lines).

## Uninstall

Delete `mods\spotipunkFM` and `mods\radioExt\radios\SpotiPunk FM` (plus any local stations you added). Delete the app in the Spotify developer dashboard if you like.

## Credits

- RadioExt by keanuWheeze. SpotiPunk FM only reads RadioExt's public mod table and ships no RadioExt code.
- `GameUI.lua` by psiberx (MIT license, see `spotipunkFM\modules\GameUI.LICENSE.txt`).

## License

MIT. See `LICENSE` at https://github.com/not-xtnct/spotipunk-fm. Third-party parts keep their own licenses (above).
