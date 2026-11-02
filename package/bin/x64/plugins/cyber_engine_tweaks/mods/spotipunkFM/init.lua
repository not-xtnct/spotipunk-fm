--------------------------------------------------------------------------------
-- SpotiPunk FM — in-game bridge between RadioExt and your Spotify account
--
-- How it works:
--   * RadioExt shows a station called "SpotiPunk FM" (folder radios/SpotiPunk FM).
--     It has no audio files, so tuning in plays nothing from the game itself.
--   * This mod watches whether that station is playing and what the game is
--     doing (cutscene, dialogue, holocall, menu, loading ...) and writes the
--     result to state.json in this folder.
--   * The companion app (companion/spotipunk_fm.py) reads state.json and plays,
--     pauses or ducks Spotify to match, and writes now_playing.json back so the
--     in-game radio popup shows the real track name.
--
-- Talks to RadioExt only through its public mod table (GetMod("radioExt")).
-- No RadioExt code is copied or modified.
--
-- GameUI.lua: Copyright (c) 2021 psiberx, MIT license (modules/GameUI.LICENSE.txt)
--------------------------------------------------------------------------------

local GameUI = require("modules/GameUI")

local VERSION = "1.0.6"
local STATE_FILE = "state.json"
local NOW_PLAYING_FILE = "now_playing.json"
local SETTINGS_FILE = "settings.json"

local EVAL_INTERVAL = 0.2      -- seconds between game-state checks
local HEARTBEAT_INTERVAL = 1.0 -- state.json is rewritten at least this often
local READ_INTERVAL = 1.0      -- now_playing.json is read this often
local COMPANION_TIMEOUT = 10   -- seconds without an update before we call it offline
local VOLUME_INTERVAL = 0.5    -- seconds between reads of the game's volume sliders
local AUDIO_GROUP = "/audio/volume"

-- How long a new mode must hold before it is sent, so short flickers
-- (entering a car, a one-frame scene change) don't pause and resume Spotify.
local DEBOUNCE = {
    scene = 0.25,
    holocall = 0.25,
    dialogue = 0.4,
    default = 0.6,
}

local defaultSettings = {
    stationFolder = "SpotiPunk FM",   -- folder name inside radioExt/radios
    pauseInScenes = true,           -- cutscenes and locked dialogue (scene tier 3+)
    duckInDialogue = true,          -- walk-and-talk dialogue, dialogue choices on screen
    duckOnCalls = true,             -- holocalls
    pauseInCombat = false,          -- vanilla radio keeps playing in combat
    respectVehicleRadioState = true,-- stop when a quest switches the car radio off
    duckVolume = 0.30,              -- volume while ducked (0.0 - 1.0)
    showNowPlayingPopup = true,     -- show the radio popup when the track changes
    followGameVolume = true,        -- scale Spotify by Master x Car radio / Radioport volume
    skipButtons = true,             -- 1/3 or LB/RB in the radio station list skip tracks
    silenceCarRadio = true,         -- switch off a vanilla station left playing under SpotiPunk FM in a car
}

local settings = {}

local rt = {
    session = 0,
    radio = nil,
    lookupTimer = 99,
    lookupWarned = false,
    evalTimer = 0,
    writeTimer = 0,
    readTimer = 99,
    mode = "off",
    reason = "starting",
    pendingMode = nil,
    pendingReason = nil,
    pendingTime = 0,
    lastTuned = false,
    hb = 0,
    forceWrite = true,
    commands = {},
    cmdSeq = 0,
    np = nil,
    npRaw = nil,
    lastLabel = nil,
    probesEnabled = { call = true, dialogue = true, combat = true, vehicle = true, tier = true, volume = true, vanilla = true },
    volume = nil,        -- game radio volume as 0.0 - 1.0 (nil = unknown, companion uses 1.0)
    volumeSource = nil,  -- "car radio" or "radioport", for the overlay
    volumeTimer = 99,
    probeErrors = {},
    overlayOpen = false,
    ready = false,
    car = {
        inCar = false,       -- mounted in a vehicle at the last check
        tries = 0,           -- switch-off attempts this ride
        lastTry = nil,       -- os.clock() of the last attempt
        gaveUp = false,
        pendingFlagCheck = false,
        timer = 99,
        lines = 0,
    },
    input = {
        popupOpen = false,   -- the radio station list (car or pocket radio) is on screen
        popupTimer = 0,      -- seconds since it opened (safety reset if the close is missed)
        lines = 0,           -- input log lines written this session
        hooks = {},          -- hook name -> "ok" or the error text
        skips = 0,           -- skips sent from game buttons this session
        lastSkip = nil,      -- os.clock() of the last one
    },
}

--------------------------------------------------------------------------------
-- Small helpers
--------------------------------------------------------------------------------

local function log(msg)
    print("[SpotiPunkFM] " .. tostring(msg))
end

local function readFile(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local txt = f:read("*a")
    f:close()
    return txt
end

local function writeFile(path, txt)
    local f = io.open(path, "w")
    if not f then return false end
    f:write(txt)
    f:close()
    return true
end

local function loadSettings()
    for k, v in pairs(defaultSettings) do settings[k] = v end
    local txt = readFile(SETTINGS_FILE)
    if txt then
        local ok, data = pcall(json.decode, txt)
        if ok and type(data) == "table" then
            for k, v in pairs(data) do
                if defaultSettings[k] ~= nil and type(v) == type(defaultSettings[k]) then
                    settings[k] = v
                end
            end
        end
    end
end

local function saveSettings()
    writeFile(SETTINGS_FILE, json.encode(settings))
    rt.forceWrite = true
end

-- IMPORTANT: CET does not type-check arguments passed to native game
-- functions. Passing nil (or a plain Lua value) where the game expects an
-- object crashes the whole game, and pcall cannot catch that. So every value
-- that goes into a native call is checked with isNative() first, and any
-- check whose inputs are missing is switched off for good.
local function isNative(x)
    return type(x) == "userdata"
end

local NOT_AVAILABLE = {} -- sentinel: a check's game data doesn't exist in this patch

local function disableProbe(name, why)
    if rt.probesEnabled[name] then
        rt.probesEnabled[name] = false
        log("Disabled '" .. name .. "' detection: " .. tostring(why))
    end
end

-- Blackboard variants may arrive as a Variant object or already converted,
-- depending on the CET version. Only real Variant objects go to FromVariant.
local function unwrap(v)
    if v == nil then return nil end
    if isNative(v) and FromVariant then
        local ok, x = pcall(FromVariant, v)
        if ok then return x end
        return nil
    end
    return v
end

local function toInt(e)
    if type(e) == "number" then return e end
    if isNative(e) then
        local ok, n = pcall(EnumInt, e)
        if ok then return tonumber(n) end
    end
    return tonumber(tostring(e))
end

-- Runs one optional game-state check. A check that returns NOT_AVAILABLE is
-- switched off at once; one that keeps raising Lua errors is switched off
-- after a while. Either way it is reported in the overlay.
local traced = {}
local function trace(step)
    if traced[step] then return end
    traced[step] = true
    if spdlog and spdlog.info then pcall(spdlog.info, "[trace] " .. step) end
end

local function probe(name, fn)
    if not rt.probesEnabled[name] then return false end
    trace(name .. " check: first run")
    local ok, res = pcall(fn)
    trace(name .. " check: survived first run")
    if ok then
        if res == NOT_AVAILABLE then
            disableProbe(name, "game data not found in this game version")
            return false
        end
        rt.probeErrors[name] = 0
        return res == true
    end
    rt.probeErrors[name] = (rt.probeErrors[name] or 0) + 1
    if rt.probeErrors[name] >= 25 then
        disableProbe(name, "repeated errors: " .. tostring(res))
    end
    return false
end

-- Looks up a blackboard and one of its fields, refusing anything that isn't
-- a real game object. Returns bb, field or nil.
local function blackboardField(defName, fieldName, localInstancedFor)
    local defs = GetAllBlackboardDefs()
    if not isNative(defs) then return nil end
    local def = defs[defName]
    if not isNative(def) then return nil end
    local field = def[fieldName]
    if not isNative(field) then return nil end
    local system = Game.GetBlackboardSystem()
    if not isNative(system) then return nil end
    local bb
    if localInstancedFor then
        local id = localInstancedFor:GetEntityID()
        if not isNative(id) then return nil end
        bb = system:GetLocalInstanced(id, def)
    else
        bb = system:Get(def)
    end
    if not isNative(bb) then return nil end
    return bb, field
end

--------------------------------------------------------------------------------
-- Finding the SpotiPunk FM station inside RadioExt
--------------------------------------------------------------------------------

local function findStation()
    local radioMod = GetMod("radioExt")
    if not radioMod or not radioMod.radioManager or not radioMod.radioManager.radios then
        return nil, "RadioExt not loaded"
    end
    for _, r in pairs(radioMod.radioManager.radios) do
        if r.path == settings.stationFolder then
            return r
        end
    end
    return nil, "station folder '" .. settings.stationFolder .. "' not found in radioExt/radios"
end

--------------------------------------------------------------------------------
-- Game state
--------------------------------------------------------------------------------

local function getSceneTier(player)
    local tier = 1
    probe("tier", function()
        local bb, field = blackboardField("PlayerStateMachine", "SceneTier", player)
        if not bb then return NOT_AVAILABLE end
        local v = bb:GetInt(field)
        if type(v) == "number" then tier = v end
        return true
    end)
    return tier or 1
end

local function isCarRadioOff(player)
    return probe("vehicle", function()
        local veh = GetMountedVehicle(player)
        if not isNative(veh) then return false end
        local defs = GetAllBlackboardDefs()
        local def = isNative(defs) and defs.Vehicle or nil
        local field = isNative(def) and def.VehRadioState or nil
        if not isNative(field) then return NOT_AVAILABLE end
        local bb = veh:GetBlackboard()
        if not isNative(bb) then return false end
        return bb:GetBool(field) == false
    end)
end

local function isInCombat(player)
    return probe("combat", function()
        local bb, field = blackboardField("PlayerStateMachine", "Combat", player)
        if not bb then return NOT_AVAILABLE end
        return bb:GetInt(field) == 1 -- gamePSMCombat.InCombat
    end)
end

local function isInHolocall()
    return probe("call", function()
        local bb, field = blackboardField("UI_ComDevice", "PhoneCallInformation")
        if not bb then return NOT_AVAILABLE end
        local info = unwrap(bb:GetVariant(field))
        if info == nil then return false end
        if type(info) ~= "table" and not isNative(info) then return NOT_AVAILABLE end
        return toInt(info.callPhase) == 2 -- questPhoneCallPhase.StartCall
    end)
end

local function hasDialogueChoices()
    return probe("dialogue", function()
        local bb, field = blackboardField("UIInteractions", "DialogChoiceHubs")
        if not bb then return NOT_AVAILABLE end
        local data = unwrap(bb:GetVariant(field))
        if data == nil then return false end
        if type(data) ~= "table" and not isNative(data) then return NOT_AVAILABLE end
        local hubs = data.choiceHubs
        if type(hubs) ~= "table" then return false end
        return #hubs > 0
    end)
end

-- The game's volume sliders (Settings > Sound, and the volume control in the
-- car radio). Spotify plays outside the game's mixer, so we scale it ourselves
-- the same way RadioExt scales its stations: Master x Car radio (when in a
-- vehicle) or Master x Radioport (on foot). All are ints 0-100 in 2.5
-- (r6/config/settings/options.json). Group and var names go in as strings,
-- which CET converts to CName (RadioExt reads the same vars this way).
-- Returns volume 0.0-1.0 and its source, or nil when not known yet.
local function readGameVolume()
    local volume, source
    probe("volume", function()
        local ss = Game.GetSettingsSystem()
        if not isNative(ss) then return false end -- not ready yet; try again later
        local function get(name)
            if not ss:HasVar(AUDIO_GROUP, name) then return nil end
            local var = ss:GetVar(AUDIO_GROUP, name)
            if not isNative(var) then return nil end
            local v = var:GetValue()
            if type(v) ~= "number" or v ~= v then return nil end
            return math.max(0, math.min(100, v))
        end
        local master = get("MasterVolume")
        local player = Game.GetPlayer()
        local inCar = isNative(player) and isNative(GetMountedVehicle(player))
        local radio = get(inCar and "CarRadioVolume" or "RadioportVolume")
        if master == nil and radio == nil then return NOT_AVAILABLE end
        volume = (master or 100) / 100 * (radio or 100) / 100
        source = inCar and "car radio" or "radioport"
        return true
    end)
    return volume, source
end

-- Returns mode ("play" | "duck" | "pause" | "off") and a short reason.
local function computeMode()
    local r = rt.radio
    if not r or not r.channels then return "off", "station not found" end

    if GameUI.IsDetached() then rt.lastTuned = false return "off", "main menu" end
    if GameUI.IsLoading() or GameUI.IsFastTravel() then
        return rt.lastTuned and "pause" or "off", "loading"
    end
    -- RadioExt stops its channel while a menu is open, so remember whether we
    -- were tuned before the menu opened.
    if GameUI.IsMenu() then return rt.lastTuned and "pause" or "off", "menu" end

    rt.lastTuned = r.channels[-1] == true
    if not rt.lastTuned then return "off", "not tuned" end

    if GameUI.IsBraindance() then return "pause", "braindance" end
    if GameUI.IsPhoto() then return "pause", "photo mode" end
    if GameUI.IsCyberspace() then return "pause", "cyberspace" end

    local player = Game.GetPlayer()
    if not isNative(player) then return "pause", "no player" end
    local tier = getSceneTier(player)

    if settings.pauseInScenes and (tier >= 3 or GameUI.IsFlashback()) then return "pause", "scene" end
    if settings.respectVehicleRadioState and isCarRadioOff(player) then return "off", "car radio off" end
    if settings.pauseInCombat and isInCombat(player) then return "pause", "combat" end
    if settings.duckOnCalls and isInHolocall() then return "duck", "holocall" end
    if settings.duckInDialogue and (tier == 2 or hasDialogueChoices()) then return "duck", "dialogue" end

    return "play", ""
end

--------------------------------------------------------------------------------
-- Talking to the companion app
--------------------------------------------------------------------------------

local function companionOnline()
    return rt.np ~= nil and type(rt.np.ts) == "number" and (os.time() - rt.np.ts) <= COMPANION_TIMEOUT
end

local function sendCommand(action, arg)
    rt.cmdSeq = rt.cmdSeq + 1
    table.insert(rt.commands, { seq = rt.cmdSeq, action = action, arg = arg })
    while #rt.commands > 8 do table.remove(rt.commands, 1) end
    rt.forceWrite = true
end

local function writeState()
    rt.hb = rt.hb + 1
    local state = {
        v = 1,
        mod = VERSION,
        session = rt.session,
        ts = os.time(),
        hb = rt.hb,
        mode = rt.mode,
        reason = rt.reason,
        duckVolume = settings.duckVolume,
        commands = rt.commands,
    }
    -- Optional field: an older companion ignores it; a missing value means "full volume".
    if settings.followGameVolume and rt.volume ~= nil then
        state.volume = rt.volume
    end
    writeFile(STATE_FILE, json.encode(state))
end

local function readNowPlaying()
    local txt = readFile(NOW_PLAYING_FILE)
    if not txt or txt == rt.npRaw then return end
    local ok, data = pcall(json.decode, txt)
    if ok and type(data) == "table" then
        rt.np = data
        rt.npRaw = txt
    end
end

-- Puts the current Spotify track into RadioExt's "now playing" slot, so the
-- vanilla radio popup and station list show it.
local function updateStationLabel()
    local r = rt.radio
    if not r then return end

    local label
    if not companionOnline() then
        label = "Start the SpotiPunk FM companion app"
    elseif rt.np.track and rt.np.track ~= "" then
        label = rt.np.track
        if rt.np.artist and rt.np.artist ~= "" then label = label .. " - " .. rt.np.artist end
    elseif rt.np.error and rt.np.error ~= "" then
        label = rt.np.error
    else
        label = "SpotiPunk FM"
    end
    label = label:gsub("\\", "/"):gsub("[\r\n]", " ")
    if #label > 90 then label = label:sub(1, 87) .. "..." end

    if label == rt.lastLabel then return end
    rt.lastLabel = label
    -- RadioExt strips the extension when showing the name; add one so titles
    -- containing dots ("Mr. Brightside") survive.
    r.currentSong = { path = label .. ".mp3", length = 0 }

    if settings.showNowPlayingPopup and r.channels and r.channels[-1] then
        pcall(function() r:tryUpdateUI() end)
    end
end

--------------------------------------------------------------------------------
-- Skipping tracks with game buttons (1.0.5)
--
-- While the radio station list is open (car radio or pocket radio, both use
-- VehicleRadioPopupGameController) and SpotiPunk FM is the station playing:
--   previous = 1 (keyboard) / LB (controller)   -> game action "prior_menu"
--   next     = 3 (keyboard) / RB (controller)   -> game action "next_menu"
-- Measured in game on 2 Nov 2026 (init.lua 1.0.4 input test): those two actions
-- fire for 1/3 in the list and never for A/D, which change the radio volume.
-- The similar popup_prior/popup_next DO fire for A/D, even while steering a
-- car with the list closed, so they are not used. Nothing happens while the
-- list is closed: there 1/3 select weapons and LB/RB do other things.
--------------------------------------------------------------------------------

local INPUT_LOG_LIMIT = 200        -- [input] lines per game session in spotipunkFM.log
local POPUP_TIMEOUT = 120          -- seconds; forget "list is open" if we never saw it close
local SKIP_COOLDOWN = 0.25         -- seconds; one press can never skip twice
local SKIP_ACTIONS = { prior_menu = "previous", next_menu = "next" }
local LOGGED_ACTIONS = {           -- presses worth a log line while the list is open
    prior_menu = true, next_menu = true, popup_prior = true, popup_next = true,
    radio_volume_down = true, radio_volume_up = true,
}

local function inputLog(msg)
    local inp = rt.input
    if inp.lines >= INPUT_LOG_LIMIT then return end
    inp.lines = inp.lines + 1
    if inp.lines == INPUT_LOG_LIMIT then msg = msg .. "  (input log limit reached, nothing more this session)" end
    if spdlog and spdlog.info then pcall(spdlog.info, "[input] " .. msg) end
end

-- CName -> Lua string. Only real game objects are passed on (rule: isNative).
local function nameToString(n)
    if type(n) == "string" then return n end
    if not isNative(n) then return nil end
    local ok, s = pcall(function() return n.value end)
    if ok and type(s) == "string" and s ~= "" then return s end
    if Game and Game.NameToString then
        ok, s = pcall(Game.NameToString, n)
        if ok and type(s) == "string" and s ~= "" then return s end
    end
    return nil
end

local function stationPlaying()
    local r = rt.radio
    return r ~= nil and type(r.channels) == "table" and r.channels[-1] == true
end

local function onPlayerAction(_, action)
    local inp = rt.input
    -- OnAction fires for every mouse move; return at once unless the list is open.
    if not rt.ready or not inp.popupOpen then return end
    if not isNative(action) then return end
    local ok, pressed = pcall(function() return action:IsButtonJustPressed() end)
    if not ok or pressed ~= true then return end
    local okName, cname = pcall(function() return action:GetName() end)
    local name = okName and nameToString(cname) or nil
    if not name or not LOGGED_ACTIONS[name] then return end

    local cmd = SKIP_ACTIONS[name]
    if not cmd then
        inputLog("press " .. name)
        return
    end
    if not settings.skipButtons then
        inputLog("press " .. name .. ": ignored, skip buttons are off")
    elseif not stationPlaying() then
        inputLog("press " .. name .. ": ignored, SpotiPunk FM isn't the station playing")
    elseif os.clock() - (inp.lastSkip or -1e9) < SKIP_COOLDOWN then
        inputLog("press " .. name .. ": ignored, too soon after the last skip")
    else
        inp.lastSkip = os.clock()
        sendCommand(cmd)
        inp.skips = (inp.skips or 0) + 1
        inputLog("press " .. name .. ": sent " .. cmd)
    end
end

local function hook(kind, class, method, fn)
    local key = class .. ":" .. method
    local register = (kind == "after") and ObserveAfter or Observe
    if type(register) ~= "function" then
        rt.input.hooks[key] = "not available"
        return
    end
    local ok, err = pcall(register, class, method, fn)
    rt.input.hooks[key] = ok and "ok" or tostring(err)
    if not ok then log("Could not hook " .. key .. ": " .. tostring(err)) end
end

local function installInputHooks()
    hook("before", "PlayerPuppet", "OnAction", onPlayerAction)
    -- RadioExt hooks SetupData too, so it exists in 2.5. Same popup for car and pocket radio.
    hook("after", "VehicleRadioPopupGameController", "SetupData", function()
        rt.input.popupOpen, rt.input.popupTimer = true, 0
        inputLog("radio list opened, SpotiPunk FM " .. (stationPlaying() and "playing" or "not playing"))
    end)
    hook("before", "VehicleRadioPopupGameController", "OnClose", function()
        if rt.input.popupOpen then inputLog("radio list closed") end
        rt.input.popupOpen = false
    end)
end

--------------------------------------------------------------------------------
-- Silencing the car's own station (1.0.6)
--
-- Bug seen 2 Nov 2026: SpotiPunk FM on the pocket radio, get into a car, and a
-- vanilla station plays quietly underneath. RadioExt hands the station over on
-- entry by sending the car a "radio off" event, but the game also restores the
-- car's last vanilla station, and sometimes that happens afterwards. Picking
-- "no station" and then SpotiPunk FM again fixed it, because RadioExt switches
-- the car's receiver off properly on a manual pick.
--
-- Fix: while in a car with SpotiPunk FM playing, if the car's own receiver is
-- on, switch it off the way RadioExt does (ToggleRadioReceiver(false)). Never
-- touches the car radio when any other station is playing. At most
-- VANILLA_MAX_TRIES per car ride, VANILLA_RETRY seconds apart (rule 2).
--------------------------------------------------------------------------------

local VANILLA_INTERVAL = 0.5       -- seconds between checks while in a car
local VANILLA_RETRY = 1.5          -- seconds between two switch-off attempts
local VANILLA_MAX_TRIES = 3        -- per car ride
local CAR_LOG_LIMIT = 100          -- [car] lines per game session

local function carLog(msg)
    local c = rt.car
    if c.lines >= CAR_LOG_LIMIT then return end
    c.lines = c.lines + 1
    if spdlog and spdlog.info then pcall(spdlog.info, "[car] " .. msg) end
end

-- Puts the car's "radio on" flag back if switching the receiver off cleared
-- it, so neither RadioExt nor our own "car radio off" rule stops SpotiPunk FM.
-- RadioExt sets the same flag the same way on entry.
local function keepCarRadioFlagOn(veh)
    local defs = GetAllBlackboardDefs()
    local def = isNative(defs) and defs.Vehicle or nil
    local field = isNative(def) and def.VehRadioState or nil
    if not isNative(field) then return end
    local bb = veh:GetBlackboard()
    if not isNative(bb) then return end
    if bb:GetBool(field) == false then
        bb:SetBool(field, true)
        carLog("car radio flag was cleared, set it back on")
    end
end

local function checkVanillaInCar()
    local c = rt.car
    probe("vanilla", function()
        local player = Game.GetPlayer()
        if not isNative(player) then return false end
        local veh = GetMountedVehicle(player)
        if not isNative(veh) then
            c.inCar = false
            return false
        end
        local playing = stationPlaying()
        if not c.inCar then
            c.inCar, c.tries, c.lastTry, c.gaveUp = true, 0, nil, false
            local active = veh:IsRadioReceiverActive() == true
            carLog(("entered a car: SpotiPunk FM %s, car's own radio %s%s"):format(
                playing and "playing" or "not playing", active and "ON" or "off",
                active and (" (" .. tostring(nameToString(veh:GetRadioReceiverStationName()) or "?") .. ")") or ""))
        end
        if c.pendingFlagCheck then
            c.pendingFlagCheck = false
            keepCarRadioFlagOn(veh)
        end
        if not settings.silenceCarRadio or not playing or c.gaveUp then return true end
        if veh:IsRadioReceiverActive() ~= true then return true end

        local now = os.clock()
        if c.lastTry and now - c.lastTry < VANILLA_RETRY then return true end
        if c.tries >= VANILLA_MAX_TRIES then
            c.gaveUp = true
            carLog("car's own radio is still on after " .. VANILLA_MAX_TRIES .. " tries, giving up for this ride")
            return true
        end
        c.tries, c.lastTry = c.tries + 1, now
        carLog(("car's own radio (%s) is playing under SpotiPunk FM, switching it off (try %d)"):format(
            tostring(nameToString(veh:GetRadioReceiverStationName()) or "?"), c.tries))
        veh:ToggleRadioReceiver(false)
        keepCarRadioFlagOn(veh)
        c.pendingFlagCheck = true   -- and again next check, in case the flag clears later
        return true
    end)
end

--------------------------------------------------------------------------------
-- Overlay (visible while the CET overlay is open)
--------------------------------------------------------------------------------

local function drawOverlay()
    if not ImGui.Begin("SpotiPunk FM", ImGuiWindowFlags.AlwaysAutoResize) then
        ImGui.End()
        return
    end

    if not rt.radio then
        ImGui.Text("Station: not found")
        ImGui.TextWrapped("Check that RadioExt is installed and that the folder\nradioExt/radios/" .. settings.stationFolder .. " exists.")
    else
        ImGui.Text("Station: " .. tostring(rt.radio.name))
    end

    ImGui.Text("Game says: " .. rt.mode .. (rt.reason ~= "" and (" (" .. rt.reason .. ")") or ""))
    if not settings.followGameVolume then
        ImGui.Text("Game volume: not followed")
    elseif rt.volume ~= nil then
        ImGui.Text(("Game volume: %d%% (master x %s)"):format(math.floor(rt.volume * 100 + 0.5), tostring(rt.volumeSource)))
    elseif not rt.probesEnabled.volume then
        ImGui.Text("Game volume: not readable, using full volume")
    end

    if companionOnline() then
        local np = rt.np
        ImGui.Text("Companion: connected" .. (np.device and np.device ~= "" and (" - " .. np.device) or ""))
        if np.track and np.track ~= "" then
            ImGui.Separator()
            ImGui.Text((np.is_playing and "Playing: " or "Paused: ") .. np.track)
            ImGui.Text("by " .. tostring(np.artist or ""))
            if np.playlist_name and np.playlist_name ~= "" then ImGui.Text("from " .. np.playlist_name) end
        end
        if np.error and np.error ~= "" then
            ImGui.Separator()
            ImGui.TextWrapped("Companion: " .. np.error)
        end

        ImGui.Separator()
        if ImGui.Button("Previous") then sendCommand("previous") end
        ImGui.SameLine()
        if ImGui.Button("Next") then sendCommand("next") end
        ImGui.SameLine()
        if ImGui.Button("Refresh playlists") then sendCommand("refresh_playlists") end

        local playlists = np.playlists or {}
        local preview = np.selected_name or "Choose a playlist"
        if ImGui.BeginCombo("Playlist", preview) then
            for _, p in ipairs(playlists) do
                if ImGui.Selectable(p.name, p.uri == np.selected_uri) then
                    sendCommand("set_playlist", p.uri)
                end
            end
            ImGui.EndCombo()
        end

        local shuffle, pressed = ImGui.Checkbox("Shuffle", np.shuffle == true)
        if pressed then sendCommand("set_shuffle", shuffle) end
    else
        ImGui.TextWrapped("Companion: not running.\nStart run_companion.bat in the spotipunkFM\\companion folder.")
    end

    ImGui.Separator()
    if ImGui.CollapsingHeader("Behaviour") then
        local changed = false
        local v, p
        v, p = ImGui.Checkbox("Pause in cutscenes / locked dialogue", settings.pauseInScenes); if p then settings.pauseInScenes = v changed = true end
        v, p = ImGui.Checkbox("Lower volume during dialogue", settings.duckInDialogue); if p then settings.duckInDialogue = v changed = true end
        v, p = ImGui.Checkbox("Lower volume during holocalls", settings.duckOnCalls); if p then settings.duckOnCalls = v changed = true end
        v, p = ImGui.Checkbox("Pause in combat", settings.pauseInCombat); if p then settings.pauseInCombat = v changed = true end
        v, p = ImGui.Checkbox("Stop when a quest turns the car radio off", settings.respectVehicleRadioState); if p then settings.respectVehicleRadioState = v changed = true end
        v, p = ImGui.Checkbox("Show radio popup on track change", settings.showNowPlayingPopup); if p then settings.showNowPlayingPopup = v changed = true end
        v, p = ImGui.Checkbox("Follow the game's radio volume sliders", settings.followGameVolume); if p then settings.followGameVolume = v changed = true end
        v, p = ImGui.Checkbox("Skip tracks with 1 / 3 (LB / RB) in the radio list", settings.skipButtons); if p then settings.skipButtons = v changed = true end
        v, p = ImGui.Checkbox("Silence the car's own station under SpotiPunk FM", settings.silenceCarRadio); if p then settings.silenceCarRadio = v changed = true end
        v, p = ImGui.SliderFloat("Ducked volume", settings.duckVolume, 0.0, 1.0, "%.2f"); if p then settings.duckVolume = v changed = true end
        if changed then saveSettings() end

        local off = {}
        for name, enabled in pairs(rt.probesEnabled) do if not enabled then table.insert(off, name) end end
        if #off > 0 then
            ImGui.TextWrapped("Detection switched off after errors: " .. table.concat(off, ", ") .. ". Reload mods to retry.")
        end
    end

    if ImGui.CollapsingHeader("Skip buttons") then
        ImGui.TextWrapped("Open the radio list with SpotiPunk FM playing, then press 1 / 3 (keyboard) or LB / RB (controller).")
        ImGui.Text("Radio list: " .. (rt.input.popupOpen and "open" or "closed"))
        ImGui.Text(("Skips this session: %d"):format(rt.input.skips))
        for key, status in pairs(rt.input.hooks) do
            ImGui.Text(key .. ": " .. status)
        end
    end

    ImGui.Text("v" .. VERSION)
    ImGui.End()
end

--------------------------------------------------------------------------------
-- Events
--------------------------------------------------------------------------------

registerHotkey("spotipunkfm_next", "SpotiPunk FM: next track", function() sendCommand("next") end)
registerHotkey("spotipunkfm_previous", "SpotiPunk FM: previous track", function() sendCommand("previous") end)

registerForEvent("onInit", function()
    loadSettings()
    rt.session = os.time()
    -- Starts GameUI's observers; we only use its getters afterwards.
    GameUI.Observe(function() end)
    installInputHooks()
    rt.ready = true
    writeState()
    log("v" .. VERSION .. " loaded")
end)

registerForEvent("onOverlayOpen", function() rt.overlayOpen = true end)
registerForEvent("onOverlayClose", function() rt.overlayOpen = false end)

registerForEvent("onDraw", function()
    if rt.ready and rt.overlayOpen then drawOverlay() end
end)

registerForEvent("onUpdate", function(delta)
    if not rt.ready then return end

    -- RadioExt may initialise after us, so keep looking until the station shows up.
    if not rt.radio then
        rt.lookupTimer = rt.lookupTimer + delta
        if rt.lookupTimer >= 1.0 then
            rt.lookupTimer = 0
            local r, why = findStation()
            rt.radio = r
            if r then
                log("Found station '" .. tostring(r.name) .. "'")
                rt.lastLabel = nil
            elseif not rt.lookupWarned and not GameUI.IsDetached() then
                rt.lookupWarned = true
                log("Waiting for station: " .. tostring(why))
            end
        end
    end

    rt.evalTimer = rt.evalTimer + delta
    if rt.evalTimer >= EVAL_INTERVAL then
        local elapsed = rt.evalTimer
        rt.evalTimer = 0

        local ok, mode, reason = pcall(computeMode)
        if not ok then
            if not rt.loggedEvalError then
                rt.loggedEvalError = true
                log("State check failed: " .. tostring(mode))
            end
            mode, reason = "pause", "error"
        end

        if mode == rt.mode and reason == rt.reason then
            rt.pendingMode = nil
        elseif mode ~= rt.pendingMode or reason ~= rt.pendingReason then
            rt.pendingMode, rt.pendingReason, rt.pendingTime = mode, reason, 0
        else
            rt.pendingTime = rt.pendingTime + elapsed
            if rt.pendingTime >= (DEBOUNCE[reason] or DEBOUNCE.default) then
                rt.mode, rt.reason = mode, reason
                rt.pendingMode = nil
                rt.forceWrite = true
            end
        end
    end

    if rt.input.popupOpen then
        rt.input.popupTimer = rt.input.popupTimer + delta
        if rt.input.popupTimer >= POPUP_TIMEOUT or GameUI.IsDetached() then
            rt.input.popupOpen = false
            inputLog("radio list assumed closed (no close seen)")
        end
    end

    rt.car.timer = rt.car.timer + delta
    if rt.car.timer >= VANILLA_INTERVAL then
        rt.car.timer = 0
        checkVanillaInCar()
    end

    rt.volumeTimer = rt.volumeTimer + delta
    if rt.volumeTimer >= VOLUME_INTERVAL then
        rt.volumeTimer = 0
        local v, source = readGameVolume()
        if v ~= nil then
            v = math.floor(v * 1000 + 0.5) / 1000
            if rt.volume == nil or math.abs(v - rt.volume) >= 0.005 then
                rt.forceWrite = true
            end
            rt.volume, rt.volumeSource = v, source
        end
    end

    rt.writeTimer = rt.writeTimer + delta
    if rt.forceWrite or rt.writeTimer >= HEARTBEAT_INTERVAL then
        rt.writeTimer = 0
        rt.forceWrite = false
        writeState()
    end

    rt.readTimer = rt.readTimer + delta
    if rt.readTimer >= READ_INTERVAL then
        rt.readTimer = 0
        readNowPlaying()
        updateStationLabel()
    end
end)

registerForEvent("onShutdown", function()
    if not rt.ready then return end
    rt.mode, rt.reason = "off", "game closed"
    writeState()
end)

return { version = VERSION }
