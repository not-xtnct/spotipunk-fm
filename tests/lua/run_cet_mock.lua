-- Test harness: runs spotifyFM/init.lua with mocked CET / game globals.
local MOD = arg[1]
local JSON = arg[2]
json = dofile(JSON)

local events, hotkeys = {}, {}
function registerForEvent(name, fn) events[name] = fn end
function registerHotkey(id, label, fn) hotkeys[id] = fn end

-- Fake game -----------------------------------------------------------------
local G = {
    detached = false, loading = false, menu = false, scene = false,
    tier = 1, combat = 0, callPhase = 0, hubs = {}, mounted = false, vehRadio = true,
    callThrows = false,
    settingsReady = false,                                   -- settings system not up yet at load
    audio = { MasterVolume = 60, CarRadioVolume = 20, RadioportVolume = 50 }, -- owner's real values
    settingsNameErrors = 0,
}
local gameUI = {
    Observe = function() end,
    IsDetached = function() return G.detached end,
    IsLoading = function() return G.loading end,
    IsFastTravel = function() return false end,
    IsMenu = function() return G.menu end,
    IsBraindance = function() return false end,
    IsPhoto = function() return false end,
    IsCyberspace = function() return false end,
    IsFlashback = function() return false end,
}
package.preload["modules/GameUI"] = function() return gameUI end

local popups = 0
local station = {
    path = "Spotify FM", name = "107.7 Spotify FM", channels = { [-1] = false },
    currentSong = { path = "Spotify FM\\No songs.mp3", length = 0 },
}
function station:tryUpdateUI() popups = popups + 1 end
local radioExtLoaded = false
function GetMod(name)
    if name == "radioExt" and radioExtLoaded then
        return { radioManager = { radios = { { path = "Other FM" }, station } } }
    end
    return nil
end

-- Native objects in CET are userdata; mock them with newproxy so isNative() accepts them.
local function obj(t)
    local u = newproxy(true)
    getmetatable(u).__index = t or {}
    return u
end
NATIVE_CALLS_WITH_NIL = 0
local function guard(...)
    for i = 1, select("#", ...) do
        local a = select(i, ...)
        if type(a) ~= "userdata" then NATIVE_CALLS_WITH_NIL = NATIVE_CALLS_WITH_NIL + 1 end
    end
end
local F = { SceneTier = obj(), Combat = obj(), VehRadioState = obj(), PCI = obj(), DCH = obj() }
local defsT = {
    PlayerStateMachine = obj({ SceneTier = F.SceneTier, Combat = F.Combat }),
    Vehicle = obj({ VehRadioState = F.VehRadioState }),
    UI_ComDevice = obj({ PhoneCallInformation = F.PCI }),
    UIInteractions = obj({ DialogChoiceHubs = F.DCH }),
}
local defs = obj(defsT)
function GetAllBlackboardDefs() return defs end
local player = obj({ GetEntityID = function() return obj() end })
local psm = obj({ GetInt = function(_, k) guard(k); if k == F.SceneTier then return G.tier else return G.combat end end })
local bbs = {
    [defsT.UI_ComDevice] = obj({ GetVariant = function(_, k)
        guard(k)
        if G.callThrows then error("no such property") end
        return obj({ __v = { callPhase = G.callPhase } }) end }),
    [defsT.UIInteractions] = obj({ GetVariant = function(_, k) guard(k); return obj({ __v = { choiceHubs = G.hubs } }) end }),
}
function FromVariant(v) guard(v); return v.__v end
-- Settings system: group/var names must be strings (CET converts them to CName).
local function settingsName(...)
    for i = 1, select("#", ...) do
        if type(select(i, ...)) ~= "string" then G.settingsNameErrors = G.settingsNameErrors + 1 end
    end
end
local settingsSystem = obj({
    HasVar = function(_, group, name) settingsName(group, name); return group == "/audio/volume" and G.audio[name] ~= nil end,
    GetVar = function(_, group, name)
        settingsName(group, name)
        if group ~= "/audio/volume" or G.audio[name] == nil then return nil end
        return obj({ GetValue = function() return G.audio[name] end })
    end,
})
Game = {
    GetSettingsSystem = function() if G.settingsReady then return settingsSystem end return nil end,
    GetPlayer = function() return player end,
    GetBlackboardSystem = function() return obj({
        GetLocalInstanced = function(_, id, d) guard(id, d); return psm end,
        Get = function(_, d) guard(d); return bbs[d] end,
    }) end,
}
function GetMountedVehicle()
    if not G.mounted then return nil end
    return obj({ GetBlackboard = function() return obj({ GetBool = function(_, k) guard(k); return G.vehRadio end }) end })
end
function EnumInt(e) guard(e); return e end
spdlog = { info = function(m) TRACE = (TRACE or "") .. m .. "\n" end }

-- ImGui mock: every call is recorded; returns harmless values
local imguiCalls = {}
ImGuiWindowFlags = { AlwaysAutoResize = 1 }
ImGui = setmetatable({}, { __index = function(_, k)
    return function(...)
        imguiCalls[k] = (imguiCalls[k] or 0) + 1
        if k == "Begin" or k == "CollapsingHeader" or k == "BeginCombo" then return true end
        if k == "Checkbox" then local a = { ... }; return a[2], false end
        if k == "SliderFloat" then local a = { ... }; return a[2], false end
        return false
    end
end })

-- Helpers -------------------------------------------------------------------
local fails, passes = 0, 0
local function check(cond, msg)
    if cond then passes = passes + 1 else fails = fails + 1; print("FAIL: " .. msg) end
end
local function readState()
    local f = io.open("state.json"); local t = f:read("*a"); f:close(); return json.decode(t)
end
local function step(seconds)
    local t = 0
    while t < seconds do events.onUpdate(0.05); t = t + 0.05 end
end
local function expect(mode, reason, label)
    step(1.5)
    local s = readState()
    check(s.mode == mode and s.reason == reason,
        label .. ": expected " .. mode .. "/" .. reason .. ", got " .. tostring(s.mode) .. "/" .. tostring(s.reason))
end

-- Load the mod ----------------------------------------------------------------
dofile(MOD .. "/init.lua")
check(events.onInit and events.onUpdate and events.onDraw, "events registered")
check(hotkeys.spotifyfm_next and hotkeys.spotifyfm_previous, "hotkeys registered")
events.onInit()

expect("off", "station not found", "no RadioExt yet")
check(readState().volume == nil, "no volume field while the settings system isn't ready")
G.settingsReady = true
radioExtLoaded = true
expect("off", "not tuned", "station found, not tuned")

station.channels[-1] = true
expect("play", "", "tuned in, free roam")

-- Game volume sliders: Master x Radioport on foot, Master x Car radio in a car
check(readState().volume == 0.3, "on foot: 60% master x 50% radioport = 0.3, got " .. tostring(readState().volume))
G.mounted = true; step(1.0)
check(readState().volume == 0.12, "in car: 60% master x 20% car radio = 0.12, got " .. tostring(readState().volume))
G.audio.CarRadioVolume = 40; step(0.6)
check(readState().volume == 0.24, "car radio slider change sent within 0.6 s, got " .. tostring(readState().volume))
G.audio.MasterVolume = 100; G.audio.CarRadioVolume = 100; step(0.6)
check(readState().volume == 1, "full sliders = 1.0, got " .. tostring(readState().volume))
G.mounted = false; G.audio.MasterVolume = 60; G.audio.CarRadioVolume = 20; step(1.0)
check(readState().volume == 0.3 and readState().mode == "play", "back on foot")
check(G.settingsNameErrors == 0, "settings group/var names passed as strings")

-- debounce: a 0.2s flicker into a scene must not change the mode
G.tier = 3; events.onUpdate(0.2); G.tier = 1; step(1.0)
check(readState().mode == "play", "short scene flicker ignored")

G.tier = 3; expect("pause", "scene", "cutscene")
G.tier = 2; expect("duck", "dialogue", "walk-and-talk")
G.tier = 1; G.hubs = { {} }; expect("duck", "dialogue", "dialogue choices")
G.hubs = {}; G.callPhase = 2; expect("duck", "holocall", "holocall")
G.callPhase = 3; expect("play", "", "call ended")

-- menu: RadioExt stops its channel while a menu is open
G.menu = true; station.channels[-1] = false; expect("pause", "menu", "pause menu")
G.menu = false; station.channels[-1] = true; expect("play", "", "back from menu")

G.mounted = true; G.vehRadio = false; expect("off", "car radio off", "quest turned car radio off")
G.vehRadio = true; expect("play", "", "car radio back on")
G.mounted = false

G.combat = 1; expect("play", "", "combat ignored by default")
G.combat = 0

station.channels[-1] = false; expect("off", "not tuned", "switched station")
G.detached = true; expect("off", "main menu", "main menu")
G.detached = false; station.channels[-1] = true; expect("play", "", "loaded save, tuned")

-- Probe that keeps failing is switched off, and mode still works
G.callThrows = true; step(6)
check(readState().mode == "play", "failing call probe doesn't break mode")
G.callThrows = false; G.callPhase = 2; step(1.5)
check(readState().mode == "play", "call probe stays disabled after errors")
G.callPhase = 0

-- Now playing label --------------------------------------------------------------
local function writeNP(t)
    local f = io.open("now_playing.json", "w"); f:write(json.encode(t)); f:close()
end
step(1.5)
check(station.currentSong.path == "Start the Spotify FM companion app.mp3", "offline label, got " .. station.currentSong.path)
local before = popups
writeNP({ ts = os.time(), track = "Mr. Brightside", artist = "The Killers", is_playing = true,
          playlists = { { name = "Drive", uri = "spotify:playlist:1" } }, selected_uri = "spotify:playlist:1", selected_name = "Drive" })
step(1.5)
check(station.currentSong.path == "Mr. Brightside - The Killers.mp3", "track label, got " .. station.currentSong.path)
check(popups == before + 1, "popup fired once on track change")
-- RadioExt's own name extraction keeps the dots
local shown = station.currentSong.path:match("(.+)%..+$")
check(shown == "Mr. Brightside - The Killers", "RadioExt would display: " .. tostring(shown))
step(3)
check(popups == before + 1, "no repeated popups for the same track")
writeNP({ ts = os.time() - 60, track = "Old" })
step(1.5)
check(station.currentSong.path == "Start the Spotify FM companion app.mp3", "stale companion detected")

-- Commands ------------------------------------------------------------------------
hotkeys.spotifyfm_next(); step(0.1)
local s = readState()
check(#s.commands == 1 and s.commands[1].action == "next" and s.commands[1].seq == 1, "next command queued")
for _ = 1, 12 do hotkeys.spotifyfm_previous() end; step(0.1)
s = readState()
check(#s.commands == 8 and s.commands[8].seq == 13, "command list capped at 8, newest kept")
check(type(s.session) == "number" and s.hb > 0, "session + heartbeat present")

-- Overlay draws in both states ------------------------------------------------------
events.onOverlayOpen()
local ok, err = pcall(events.onDraw)
check(ok, "overlay draws while companion offline: " .. tostring(err))
writeNP({ ts = os.time(), track = "Song", artist = "A", device = "PC", playlists = { { name = "P", uri = "u" } } })
step(1.5)
ok, err = pcall(events.onDraw)
check(ok, "overlay draws while connected: " .. tostring(err))
check((imguiCalls.BeginCombo or 0) >= 1, "playlist combo drawn")

-- Settings persist
local f = io.open("settings.json", "w"); f:write('{"pauseInCombat": true, "duckVolume": 0.5, "followGameVolume": false, "bogus": 1}'); f:close()
events.onInit()
G.combat = 1; expect("pause", "combat", "pauseInCombat setting loaded")
check(readState().duckVolume == 0.5, "duckVolume setting loaded")
check(readState().volume == nil, "followGameVolume=false: no volume field sent")

-- The 2.5 crash scenario: a blackboard field is missing (nil). Must not reach native code.
defsT.UI_ComDevice = obj({})          -- PhoneCallInformation gone
defsT.UIInteractions = nil            -- whole blackboard gone
G.tier = 1; G.hubs = {}; G.callPhase = 0; G.combat = 0
settings_reload = io.open("settings.json", "w"); settings_reload:write("{}"); settings_reload:close()
events.onInit()
local before = NATIVE_CALLS_WITH_NIL
expect("play", "", "missing blackboard fields: still plays")
check(readState().volume == 0.3, "followGameVolume back on by default")
-- Volume vars gone (a future patch renames them): detection switches off, music keeps playing
G.audio = {}
expect("play", "", "volume vars missing: still plays")
step(1.0)
check(readState().volume == 0.3, "last known volume kept after volume vars disappear")
check(NATIVE_CALLS_WITH_NIL == 0, "no nil/non-native value ever passed to a native call (" .. NATIVE_CALLS_WITH_NIL .. ")")
check(TRACE and TRACE:find("call check: first run") and TRACE:find("dialogue check: survived first run"), "trace breadcrumbs written")

events.onShutdown()
check(readState().mode == "off" and readState().reason == "game closed", "shutdown writes off")

print(("passed %d, failed %d"):format(passes, fails))
os.exit(fails == 0 and 0 or 1)
