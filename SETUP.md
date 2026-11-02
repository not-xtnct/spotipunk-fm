# SpotiPunk FM: first-time setup guide

This guide takes you from nothing to **your Spotify music playing on 107.7 SpotiPunk FM in Night City**. Plan on **20–30 minutes** the first time. After that you just press Play in Steam.

You don't need any coding knowledge. You'll mostly download files, copy folders, and click a few buttons.

> **Unofficial fan mod.** Not affiliated with Spotify or CD PROJEKT RED.

---

## Before you start

| You need | Notes |
|---|---|
| Cyberpunk 2077 **patch 2.5** on **PC** | Steam is easiest (auto-start, step 7). GOG/Epic work too, but you start the companion yourself. Consoles can't run this kind of mod. |
| **Spotify Premium** | Spotify only lets apps play music for Premium accounts. Without Premium you can still use the [local music stations](package/README.md#local-music-stations). |
| Windows 10 or 11 with **Microsoft Edge** | Edge comes with Windows. You don't have to use it as your browser. The mod uses it quietly in the background. Google Chrome also works. |
| About 30 minutes | Mostly one-time steps. |

> **Tip:** Windows marks downloaded zips as "from the internet" and then asks about every file inside. To skip that: **right-click the zip → Properties → tick "Unblock" → OK**, *then* extract it.

---

## Step 1: Install the three mod frameworks

SpotiPunk FM builds on three popular, free modding tools. If you already use other Cyberpunk mods, you may have them already. Just make sure they're the versions for **2.5**.

Find your game folder first: in Steam, **right-click Cyberpunk 2077 → Manage → Browse local files**. It's the folder that contains `bin`, `archive` and `r6`. In this guide it's called **the game folder**.

1. **RED4ext** (version **1.31.0 or newer**): [Nexus Mods](https://www.nexusmods.com/cyberpunk2077/mods/2380) or [GitHub releases](https://github.com/wopss/RED4ext/releases). Extract the zip **into the game folder** (it adds `red4ext\` and `bin\x64\winmm.dll`).
2. **Cyber Engine Tweaks (CET)** (version **1.38.1 or newer**): [Nexus Mods](https://www.nexusmods.com/cyberpunk2077/mods/107) or [GitHub releases](https://github.com/maximegmd/CyberEngineTweaks/releases). Extract **into the game folder** (it adds files to `bin\x64\`).
3. **RadioExt**, a build made for **2.5**:
   - **First choice:** [RadioExt on Nexus Mods](https://www.nexusmods.com/cyberpunk2077/mods/4591). Use it if the newest file there is dated **25 Oct 2026 or later**.
   - **If Nexus only has older files:** the author's own automatic build of the 2.5 update is on GitHub: [RadioExt build for 2.5](https://github.com/justarandomguyintheinternet/CP77_radioExt/actions/runs/40232265573). Log in to GitHub (free), scroll down to **Artifacts**, and download **radioExtLatest**. (GitHub keeps it until **23 Jan 2027**. Older RadioExt releases on GitHub don't support 2.5.)
   - Extract it **into the game folder**. It adds `bin\x64\plugins\cyber_engine_tweaks\mods\radioExt\` and `red4ext\plugins\RadioExt\`.

**Check it works:** start the game once. CET asks you to choose an **overlay key** the first time. Pick one you'll remember (for example `Home` or `F1`). Get into the game, press that key, and you should see the CET overlay. Quit the game.

<!-- screenshot: CET first-launch "choose overlay key" popup -->

---

## Step 2: Install Python

The SpotiPunk FM companion is a small Python program that talks to Spotify.

1. Download Python from [python.org/downloads](https://www.python.org/downloads/). Any version **3.10 or newer** works.
2. Run the installer. On its first screen, **tick "Add python.exe to PATH"** at the bottom. This step matters.
3. Click **Install Now** and wait until it says "Setup was successful".

<!-- screenshot: Python installer with "Add python.exe to PATH" ticked -->

That's all. You won't need to open Python yourself.

---

## Step 3: Install SpotiPunk FM

1. Download **`SpotiPunk-FM-<version>.zip`** from the [Releases page](https://github.com/not-xtnct/spotipunk-fm/releases) (unblock it as in the tip above, then extract it).
2. Copy the **`bin`** folder from the zip **into the game folder**. When Windows asks, choose to **merge** folders. SpotiPunk FM only **adds** files. It never replaces anything of the game's.
3. Check you now have these:
   ```
   <game folder>\bin\x64\plugins\cyber_engine_tweaks\mods\spotipunkFM\init.lua
   <game folder>\bin\x64\plugins\cyber_engine_tweaks\mods\spotipunkFM\companion\run_companion.bat
   <game folder>\bin\x64\plugins\cyber_engine_tweaks\mods\radioExt\radios\SpotiPunk FM\metadata.json
   ```

The folder `...\spotipunkFM\companion` is called **the companion folder** from here on. Tip: pin it in File Explorer (right-click → **Pin to Quick access**).

---

## Step 4: Create your free Spotify "developer app" (about 5 minutes)

> ### Why do I need this?
> Spotify requires every program that plays music for you to have its own **Client ID**, a kind of name tag. A shared Client ID would only work for 5 people, because Spotify limits small apps to 5 users. So everyone makes their own, in a few clicks on Spotify's official site. It's **free**, it doesn't make you a "developer" in any meaningful way, and nobody else can use your app. SpotiPunk FM never sees your Spotify password. You log in on Spotify's own page.

1. Open **[developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)** and log in with your normal Spotify account (the Premium one). Accept the developer terms if asked.
2. Click **Create app** and fill in:

   | Field | What to enter |
   |---|---|
   | App name | `SpotiPunk FM` (anything works) |
   | App description | `Radio station in Cyberpunk 2077` (anything works) |
   | Website | leave empty |
   | **Redirect URIs** | `http://127.0.0.1:8898/callback`. Type it **exactly**, then click **Add**. |
   | **Which API/SDKs are you planning to use?** | tick **Web API** and **Web Playback SDK** |

3. Tick the terms checkbox and click **Save**.
4. On the app's page, click **Settings**. Under *Basic Information* you'll see **Client ID**, a string of 32 letters and numbers. **Copy it.** (You don't need the "Client secret". Leave it alone.)

<!-- screenshot: Spotify "Create app" form with the Redirect URI and the two APIs ticked -->
<!-- screenshot: app Settings page with the Client ID highlighted -->

---

## Step 5: First run of the companion

1. In the companion folder, double-click **`run_companion.bat`**. A black window opens. Leave it open.
   - If Windows shows *"Windows protected your PC"*, click **More info → Run anyway**. If it shows *"Open File – Security Warning"*, click **Run**. (This happens for files from the internet. The unblock tip avoids it.)
2. The first time, it prepares itself (*"First run: preparing Python environment..."*). This takes **1–2 minutes**.
3. It asks for your **Client ID**. Paste it (right-click in the window pastes) and press **Enter**.
4. **Your browser opens a Spotify page.** Log in if asked and click **Agree**. The page then says *"SpotiPunk FM is connected"*. Close that tab.
5. Back in the black window, **choose a playlist**: type its number and press Enter (**0 = Liked Songs**). You can change it later in game.
6. It says *"SpotiPunk FM companion ... running"*. A small "SpotiPunk FM Player" window may **flash for a second and hide itself**. That's the built-in player, and it's supposed to happen.

<!-- screenshot: companion window after a successful first run -->

**Optional sound check (no game needed):** close the window, then double-click **`sound_test.bat`**. It plays your music for 10 seconds and asks if you hear it. Answer `y` or `n`.

---

## Step 6: Play!

1. Start the companion if it isn't running (`run_companion.bat`), then start Cyberpunk 2077.
2. In a car, open the radio and pick **107.7 SpotiPunk FM**. On foot, use the pocket radio.
3. Your music starts within a few seconds. The radio popup shows the song and artist.
4. To skip a song, keep the radio list open and press **3** (keyboard) or **RB** (controller) for the next one, **1** or **LB** for the previous one.

Press your CET overlay key to see the **SpotiPunk FM** window: current track, next/previous, playlist picker, shuffle, and switches for each pause/volume rule.

The music follows the game's **Car radio** and **Radioport** volume sliders (and the volume control in the car radio).

---

## Step 7: Never open the companion again (Steam only, optional)

1. In the companion folder, double-click **`steam_launch_option.bat`**. It copies one line to your clipboard and shows it.
2. In Steam: **right-click Cyberpunk 2077 → Properties → General → Launch options**. Click into the box and press **Ctrl+V**.
3. Close the Properties window.

From now on just press **Play** in Steam. The companion starts hidden, with no window, a few seconds before the game, and closes itself about 20 seconds after you quit. It's a normal Steam setting and changes no game files. To turn it off, empty that box.

> If you moved the mod or reinstalled it to a different folder, run `steam_launch_option.bat` again and paste the new line.

---

## It worked if...

- [ ] **107.7 SpotiPunk FM** is in the car radio list
- [ ] Tuning in plays **your** music, and the popup shows the song name
- [ ] The music **pauses in cutscenes** and gets **quieter during calls and conversations**
- [ ] Moving the **Car radio volume** slider changes the music volume
- [ ] The CET overlay's SpotiPunk FM window says *"Companion: connected"*

---

## Common issues

**The audio is choppy, cuts out, or stays silent the first time.**
This is normal on the first run or two. The built-in player is setting itself up in the background, including Spotify's copy-protection module. **Close the game and the companion (or simply restart your PC) and start again.** That fixes it in almost every case. If the sound *still* stutters every few seconds and YouTube stutters too, it's your PC's audio, not the mod. Bluetooth headphones are the usual cause: restart the PC, disconnect the headphones from your phone, and pick the "Headphones" output, not "Headset".

**107.7 SpotiPunk FM isn't in the radio list.**
RadioExt isn't loaded or is too old for 2.5. Re-check step 1. The CET console (overlay key → *Console*) shows `[RadioExt]` errors if something's wrong. A line saying the station *"has no song files"* is expected and fine.

**The station plays nothing and its name says "Start the SpotiPunk FM companion app".**
The companion isn't running. Start `run_companion.bat`, or set up step 7.

**"INVALID_CLIENT: Invalid redirect URI" when logging in.**
The Redirect URI in your Spotify app must be exactly `http://127.0.0.1:8898/callback`. Fix it on the dashboard (your app → Settings → Edit) and try again.

**"Spotify Premium is required".**
That's Spotify's rule. Check you logged in with your Premium account.

**A "SpotiPunk FM" message box says it can't start by itself.**
Your Spotify login expired or the setup isn't finished. Double-click `run_companion.bat` once, follow the prompts, close it. Auto-start works again after that.

**"Python 3.10 or newer is required".**
Python isn't installed, or "Add python.exe to PATH" wasn't ticked. Run the Python installer again, choose **Modify**, and make sure **"Add Python to environment variables"** is ticked.

**Music starts on my phone or another device instead.**
Spotify plays on one device at a time. Opening Spotify elsewhere can take the music over. Re-tune the station, or pick *"SpotiPunk FM (Cyberpunk 2077)"* under Spotify's device list.

**Steam's Play button doesn't start the companion.**
Open `launcher.log` in the companion folder. If it says something is missing, run `run_companion.bat` once. If the file doesn't exist, the launch option isn't set. Redo step 7. The game always starts, even when the companion can't.

**The game is installed under `C:\Program Files` and the companion can't create files.**
Copy the whole companion folder to e.g. `Documents\SpotiPunkFM`, run `run_companion.bat` there, and paste the path to `...\mods\spotipunkFM` when it asks.

**Something else?** Look in `companion.log` (companion folder) for lines starting with *Problem:*, and open an [issue](https://github.com/not-xtnct/spotipunk-fm/issues) with that log. **Never share `tokens.json`**, because it's your Spotify login.

---

## What the mod does with your data

- Your Spotify login stays on your PC (`companion\tokens.json`). It's only sent to Spotify.
- The companion only talks to Spotify's servers and to the game, through two small files in the mod folder.
- Nothing is uploaded anywhere else, and there's no tracking.

## Uninstall

Delete `<game folder>\bin\x64\plugins\cyber_engine_tweaks\mods\spotipunkFM` and `...\mods\radioExt\radios\SpotiPunk FM`, and clear the Steam launch option if you set one. You can delete your Spotify developer app on the dashboard.
