-- Test harness: runs spotipunkFM/init.lua with mocked CET / game globals.
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
    path = "SpotiPunk FM", name = "107.7 SpotiPunk FM", channels = { [-1] = false },
    currentSong = { path = "SpotiPunk FM\\No songs.mp3", length = 0 },
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
G.receiver = false; G.receiverToggles = 0      -- the car's own (vanilla) radio receiver
function GetMountedVehicle()
    if not G.mounted then return nil end
    return obj({
        GetBlackboard = function() return obj({
            GetBool = function(_, k) guard(k); return G.vehRadio end,
            SetBool = function(_, k, v) guard(k); G.vehRadio = v end,
        }) end,
        IsRadioReceiverActive = function() return G.receiver end,
        GetRadioReceiverStationName = function() return obj({ value = "Radio Vexelstrom" }) end,
        ToggleRadioReceiver = function(_, on)
            if type(on) ~= "boolean" then NATIVE_CALLS_WITH_NIL = NATIVE_CALLS_WITH_NIL + 1 end
            G.receiverToggles = G.receiverToggles + 1
            if not G.receiverStuck then G.receiver = on end
            if G.toggleClearsFlag then G.vehRadio = false end
        end,
    })
end
function EnumInt(e) guard(e); return e end
spdlog = { info = function(m) TRACE = (TRACE or "") .. m .. "\n" end }

-- CET Observe / ObserveAfter: record the hook. A method CET can't find raises
-- an error, like the real thing reporting "function not found".
local hooks = {}
HOOK_MISSING = {}
local function mockObserve(class, method, fn)
    local key = class .. ":" .. method
    if HOOK_MISSING[key] then error("function " .. method .. " not found in " .. class) end
    hooks[key] = fn
end
Observe = mockObserve
ObserveAfter = mockObserve

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
check(hotkeys.spotipunkfm_next and hotkeys.spotipunkfm_previous, "hotkeys registered")
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
check(station.currentSong.path == "Start the SpotiPunk FM companion app.mp3", "offline label, got " .. station.currentSong.path)
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
check(station.currentSong.path == "Start the SpotiPunk FM companion app.mp3", "stale companion detected")

-- Commands ------------------------------------------------------------------------
hotkeys.spotipunkfm_next(); step(0.1)
local s = readState()
check(#s.commands == 1 and s.commands[1].action == "next" and s.commands[1].seq == 1, "next command queued")
for _ = 1, 12 do hotkeys.spotipunkfm_previous() end; step(0.1)
s = readState()
check(#s.commands == 8 and s.commands[8].seq == 13, "command list capped at 8, newest kept")
check(type(s.session) == "number" and s.hb > 0, "session + heartbeat present")

-- Skip buttons (1.0.5): 1/3 or LB/RB in the radio list --------------------------
local CLOCK = 1000
os.clock = function() return CLOCK end   -- the mod's cooldown clock, driven by the test
local function since(m) return (TRACE or ""):sub(m + 1) end
local function mark() return #(TRACE or "") end
local function action(name, pressed)
    return obj({
        GetName = function() return obj({ value = name }) end,
        IsButtonJustPressed = function() return pressed end,
    })
end
local function lastCmd() local c = readState().commands; return c[#c] end
local function press(name) onAction = hooks["PlayerPuppet:OnAction"]; onAction(player, action(name, true)); step(0.1); CLOCK = CLOCK + 1 end
local openList = function() hooks["VehicleRadioPopupGameController:SetupData"](obj()) end
local closeList = function() hooks["VehicleRadioPopupGameController:OnClose"](obj()) end

onAction = hooks["PlayerPuppet:OnAction"]
check(type(onAction) == "function", "PlayerPuppet:OnAction observed")
check(type(hooks["VehicleRadioPopupGameController:SetupData"]) == "function"
    and type(hooks["VehicleRadioPopupGameController:OnClose"]) == "function", "radio list open/close observed")

-- List closed: nothing, even while tuned (1/3 pick weapons, LB/RB do other things)
local seq0 = lastCmd().seq
press("next_menu"); press("prior_menu")
check(lastCmd().seq == seq0, "list closed: no skip")

-- List open, SpotiPunk FM playing
local m0 = mark()
openList()
press("next_menu")
check(lastCmd().action == "next" and lastCmd().seq == seq0 + 1, "3 / RB in the list sends next")
press("prior_menu")
check(lastCmd().action == "previous" and lastCmd().seq == seq0 + 2, "1 / LB in the list sends previous")
-- A/D: volume + popup_prior/next, never a skip
press("popup_prior"); press("radio_volume_down"); press("popup_next"); press("radio_volume_up")
check(lastCmd().seq == seq0 + 2, "A / D in the list don't skip")
-- held button / release: not a press
onAction(player, action("next_menu", false)); step(0.1)
check(lastCmd().seq == seq0 + 2, "non-press doesn't skip")
-- same press seen twice within the cooldown
onAction(player, action("next_menu", true)); onAction(player, action("next_menu", true)); step(0.1)
check(lastCmd().seq == seq0 + 3, "one press can't skip twice")
CLOCK = CLOCK + 0.3
onAction(player, action("next_menu", true)); step(0.1)
check(lastCmd().seq == seq0 + 4, "next press after the cooldown skips")
CLOCK = CLOCK + 1
local out = since(m0)
check(out:find("radio list opened, SpotiPunk FM playing") and out:find("press next_menu: sent next")
    and out:find("press prior_menu: sent previous") and out:find("press radio_volume_up")
    and out:find("too soon after the last skip"), "skip log lines, got: " .. out)
closeList()
check(since(m0):find("radio list closed"), "close logged")
press("next_menu")
check(lastCmd().seq == seq0 + 4, "closed again: no skip")

-- Another station playing: no skip
station.channels[-1] = false; step(1.5)
openList(); press("next_menu"); closeList()
check(lastCmd().seq == seq0 + 4, "other station playing: no skip")
station.channels[-1] = true; step(1.5)

-- Junk from the game must never reach a native call or raise
openList()
local nilBefore = NATIVE_CALLS_WITH_NIL
ok, err = pcall(function()
    onAction(nil, nil)
    onAction(player, "next_menu")
    onAction(player, obj({ IsButtonJustPressed = function() return true end, GetName = function() return nil end }))
    onAction(player, obj({ IsButtonJustPressed = function() return true end, GetName = function() return "next_menu" end }))
    onAction(player, obj({}))  -- methods missing
end)
check(ok, "odd action values don't raise: " .. tostring(err))
check(NATIVE_CALLS_WITH_NIL == nilBefore, "odd action values never reach a native call")
step(0.1)
check(lastCmd().seq == seq0 + 5, "a plain-string name still works once (CET may convert CNames)")
CLOCK = CLOCK + 1

-- A missed close is forgotten after a while
m0 = mark()
step(121)
check(since(m0):find("radio list assumed closed"), "radio list open state times out")
press("next_menu")
check(lastCmd().seq == seq0 + 5, "after the timeout: no skip")

-- Overlay shows the Skip buttons section
events.onOverlayOpen()
local headersBefore = imguiCalls.CollapsingHeader or 0
ok, err = pcall(events.onDraw)
check(ok, "overlay with Skip buttons draws: " .. tostring(err))
check((imguiCalls.CollapsingHeader or 0) - headersBefore == 2, "Behaviour + Skip buttons headers drawn")
events.onOverlayClose()

-- skipButtons=false in settings.json: presses are ignored
local sf = io.open("settings.json", "w"); sf:write('{"skipButtons": false}'); sf:close()
events.onInit(); step(1.5)
openList(); press("next_menu"); closeList()
check(lastCmd().seq == seq0 + 5, "skipButtons=false: no skip")
sf = io.open("settings.json", "w"); sf:write("{}"); sf:close()
events.onInit(); step(1.5)

-- A hook CET can't find doesn't stop the mod
HOOK_MISSING["VehicleRadioPopupGameController:OnClose"] = true
ok, err = pcall(events.onInit)
check(ok, "missing hook doesn't raise: " .. tostring(err))
expect("play", "", "missing hook: still plays")
HOOK_MISSING["VehicleRadioPopupGameController:OnClose"] = nil
events.onInit(); step(1.5)

-- Car's own station under SpotiPunk FM (1.0.6) -----------------------------------
local function carSince(m) return (TRACE or ""):sub(m + 1) end
local function settle(s) local t = 0; while t < s do events.onUpdate(0.05); t = t + 0.05; CLOCK = CLOCK + 0.05 end end
G.mounted = false; settle(1)

-- Entry with the vanilla receiver left on: switched off, music keeps playing
local cm = mark()
G.receiver = true; G.receiverToggles = 0
G.mounted = true; settle(1.5)
check(G.receiverToggles == 1 and G.receiver == false, "vanilla receiver switched off once, toggles=" .. G.receiverToggles)
check(carSince(cm):find("entered a car: SpotiPunk FM playing, car's own radio ON %(Radio Vexelstrom%)")
    and carSince(cm):find("switching it off %(try 1%)"), "car log, got: " .. carSince(cm))
expect("play", "", "still playing after the switch-off")

-- Switching off clears the car radio flag: it's put back, mode never goes off
G.mounted = false; settle(1)
G.receiver = true; G.receiverToggles = 0; G.toggleClearsFlag = true
G.mounted = true; settle(0.6)
check(G.vehRadio == true, "car radio flag restored after the switch-off")
expect("play", "", "flag cleared by the switch-off: still playing")
G.toggleClearsFlag = false

-- Receiver that won't switch off: at most 3 tries per ride, then gives up
G.mounted = false; settle(1)
cm = mark()
G.receiver = true; G.receiverToggles = 0; G.receiverStuck = true
G.mounted = true; settle(15)
check(G.receiverToggles == 3, "3 tries per ride, got " .. G.receiverToggles)
check(carSince(cm):find("giving up for this ride"), "give-up logged")
-- a new ride gets new tries
G.mounted = false; settle(1); G.mounted = true; settle(1)
check(G.receiverToggles == 4, "new ride, new try, got " .. G.receiverToggles)
G.receiverStuck = false

-- Another station playing: the car radio is the player's, never touched
G.mounted = false; settle(1)
station.channels[-1] = false; settle(1)
G.receiver = true; G.receiverToggles = 0
G.mounted = true; settle(3)
check(G.receiverToggles == 0, "vanilla station picked by the player is left alone")
station.channels[-1] = true; settle(1.5)
check(G.receiverToggles == 1, "switching to SpotiPunk FM while in the car silences the leftover")

-- On foot: nothing
G.mounted = false; G.receiver = true; G.receiverToggles = 0; settle(3)
check(G.receiverToggles == 0, "on foot: no car radio calls")

-- Setting off
sf = io.open("settings.json", "w"); sf:write('{"silenceCarRadio": false}'); sf:close()
events.onInit(); G.mounted = true; settle(3)
check(G.receiverToggles == 0, "silenceCarRadio=false: no switch-off")
sf = io.open("settings.json", "w"); sf:write("{}"); sf:close()
events.onInit(); settle(1.5)
check(G.receiverToggles == 1, "setting back on: switch-off")
G.mounted = false; settle(1)

-- Log is capped per session; skipping keeps working after the cap
openList()
for _ = 1, 400 do onAction(player, action("radio_volume_up", true)) end
local n = 0
for _ in TRACE:gmatch("%[input%]") do n = n + 1 end
check(n == 200, "input log capped at 200 lines, got " .. n)
check(TRACE:find("input log limit reached"), "cap is announced in the log")
press("next_menu")
check(lastCmd().seq == seq0 + 6, "skips still work after the log cap")
closeList()

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
