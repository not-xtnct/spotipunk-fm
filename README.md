# SpotiPunk FM

**Your Spotify playlists as a radio station in Cyberpunk 2077 (patch 2.5, PC).**

Tune in to **107.7 SpotiPunk FM** in any car or on the pocket radio, and your own playlist or Liked Songs plays like a vanilla station. Music pauses in cutscenes and menus, gets quieter during dialogue and holocalls, follows the game's car radio / radioport volume sliders, and shows the current track in the radio popup. Skip songs with **1 / 3** or **LB / RB** while the radio list is open. It can also turn any folder of mp3 / flac / wav / ogg files into an extra station.

> **Unofficial fan mod.** Not affiliated with, endorsed by or sponsored by Spotify AB or CD PROJEKT RED. "Spotify" is a trademark of Spotify AB, "Cyberpunk 2077" of CD PROJEKT S.A. It uses Spotify's public Web API and Web Playback SDK with **your own** Spotify developer app and **Premium** account. Nothing is streamed anywhere but your own PC.

> 🆕 **New to modding? Follow the step-by-step [first-time setup guide](SETUP.md).** It takes about 20–30 minutes, and you don't need any coding knowledge.

## What you need

- Cyberpunk 2077 **2.5** on PC
- [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107), [RED4ext](https://www.nexusmods.com/cyberpunk2077/mods/2380) and [RadioExt](https://www.nexusmods.com/cyberpunk2077/mods/4591), all updated for 2.5
- **Spotify Premium** (Spotify only allows playback control for Premium)
- [Python 3.10+](https://www.python.org/downloads/) with "Add python.exe to PATH" ticked
- Microsoft Edge or Google Chrome (Edge comes with Windows)

## Install

1. Download `SpotiPunk-FM-<version>.zip` from [Releases](../../releases).
2. Copy its `bin` folder into your `Cyberpunk 2077` folder and merge. It only **adds** files, never replaces game files.
3. Create a free Spotify developer app (about 3 minutes), then double-click `spotipunkFM\companion\run_companion.bat` once to log in and pick a playlist.
4. Optional but nice: double-click `steam_launch_option.bat` and paste the line it copies into Steam → Cyberpunk 2077 → Properties → Launch options. After that the companion starts and stops with the game by itself.

Beginner-friendly walkthrough with every click and common issues: **[SETUP.md](SETUP.md)**. Settings reference and more troubleshooting: [package/README.md](package/README.md).

## How it works

```
Cyberpunk 2077
  RadioExt ............. shows the "SpotiPunk FM" station (no audio files of its own)
  spotipunkFM (CET mod)  watches the station + game state (scenes, dialogue, calls, menus, volume) ─► state.json
                                                                                                  │
SpotiPunk FM companion (Python, hidden) ◄──────────────────────────────────────────────────────────┘
  ├─ built-in player: Spotify's official Web Playback SDK in a hidden Edge/Chrome window
  ├─ Spotify Web API: play / pause / volume on that player
  └─ now_playing.json ─► the game's radio popup shows the track
```

Spotify's streams are DRM-protected, so they can't be fed into the game's own audio engine. The companion plays them next to the game and keeps them in sync with it.

## Uninstall

Delete `bin\x64\plugins\cyber_engine_tweaks\mods\spotipunkFM` and `...\mods\radioExt\radios\SpotiPunk FM`, and clear the Steam launch option if you set one.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the architecture, the rules that keep the mod from crashing the game, and how to run the tests.

## License

[MIT](LICENSE). Credits: RadioExt by keanuWheeze (not included, install it separately), `GameUI.lua` by psiberx (MIT), `json.lua` by rxi (MIT, tests only).
