// Dedicated Mixxx 2.5 MIDI mapping. Never attach this to a hardware controller.
var DJAgentBridge = {};
DJAgentBridge.owner = {1: "AUTO", 2: "AUTO"};
DJAgentBridge.autodjActive = false;
DJAgentBridge.pending = {};
DJAgentBridge.connections = [];
DJAgentBridge.stateReady = false;

DJAgentBridge.send = function (seq, status, value) {
    var payload = "DJ1|" + seq + "|" + status + "|" + value;
    var bytes = [0xF0, 0x7D, 0x44, 0x4A, 0x01];
    for (var i = 0; i < payload.length; i++) bytes.push(payload.charCodeAt(i));
    bytes.push(0xF7);
    midi.sendSysexMsg(bytes, bytes.length);
};

DJAgentBridge.watch = function (group, key, token, decks) {
    var callback = function (value) {
        if (DJAgentBridge.autodjActive) return;
        var expected = DJAgentBridge.pending[token];
        delete DJAgentBridge.pending[token];
        if (expected !== undefined && Math.abs(value - expected) < 0.002) return;
        decks.forEach(function (deck) { DJAgentBridge.owner[deck] = "MANUAL"; });
    };
    DJAgentBridge.connections.push(engine.makeConnection(group, key, callback));
};

DJAgentBridge.init = function (_id, _debugging) {
    DJAgentBridge.stateReady = false;
    DJAgentBridge.pending = {};
    var autoDjConnection = engine.makeConnection("[AutoDJ]", "enabled", function (value) {
        DJAgentBridge.stateReady = value === 0 || value === 1;
        if (DJAgentBridge.stateReady) DJAgentBridge.autodjActive = value === 1;
    });
    DJAgentBridge.connections.push(autoDjConnection);
    autoDjConnection.trigger();
    for (var deck = 1; deck <= 2; deck++) {
        var group = "[Channel" + deck + "]";
        DJAgentBridge.watch(group, "play", "play" + deck, [deck]);
        DJAgentBridge.watch(group, "volume", "volume" + deck, [deck]);
    }
    DJAgentBridge.watch("[Master]", "crossfader", "crossfader", [1, 2]);
};

DJAgentBridge.shutdown = function () {
    DJAgentBridge.connections.forEach(function (connection) { connection.disconnect(); });
    DJAgentBridge.connections = [];
};

DJAgentBridge.incomingData = function (data, length) {
    if (length < 7 || length > 110 || data[0] !== 0xF0 || data[1] !== 0x7D ||
            data[2] !== 0x44 || data[3] !== 0x4A || data[4] !== 0x01 || data[length - 1] !== 0xF7) return;
    var payload = "";
    for (var i = 5; i < length - 1; i++) {
        if (data[i] < 32 || data[i] > 126) return;
        payload += String.fromCharCode(data[i]);
    }
    var fields = payload.split("|");
    if (fields.length !== 5 || fields[0] !== "DJ1") return;
    var seq = Number(fields[1]);
    var op = fields[2];
    var deck = Number(fields[3]);
    var value = fields[4];
    if (!Number.isInteger(seq) || seq < 1 || seq > 2147483647) return;
    if (!DJAgentBridge.stateReady) { DJAgentBridge.send(seq, "ERR", "not_ready"); return; }
    if (op === "HELLO" && deck === 0) { DJAgentBridge.send(seq, "OK", "1"); return; }
    if ((op === "AUTODJ_ENABLE" || op === "AUTODJ_FADE") && deck === 0) {
        if (DJAgentBridge.owner[1] === "MANUAL" || DJAgentBridge.owner[2] === "MANUAL") {
            DJAgentBridge.send(seq, "ERR", "manual"); return;
        }
        if (op === "AUTODJ_ENABLE") {
            engine.setValue("[AutoDJ]", "enabled", 1);
            DJAgentBridge.autodjActive = !!engine.getValue("[AutoDJ]", "enabled");
            DJAgentBridge.send(seq, "OK", String(engine.getValue("[AutoDJ]", "enabled")));
        } else {
            if (!engine.getValue("[AutoDJ]", "enabled")) {
                DJAgentBridge.send(seq, "ERR", "autodj_off"); return;
            }
            engine.setValue("[AutoDJ]", "fade_now", 1);
            engine.setValue("[AutoDJ]", "fade_now", 0);
            DJAgentBridge.send(seq, "OK", "1");
        }
        return;
    }
    if (op === "CROSSFADE" && deck === 0) {
        var cross = Number(value);
        if (!Number.isFinite(cross) || cross < -1 || cross > 1) {
            DJAgentBridge.send(seq, "ERR", "range"); return;
        }
        if (DJAgentBridge.autodjActive) { DJAgentBridge.send(seq, "ERR", "autodj"); return; }
        if (DJAgentBridge.owner[1] !== "AUTO" || DJAgentBridge.owner[2] !== "AUTO") {
            DJAgentBridge.send(seq, "ERR", "manual"); return;
        }
        DJAgentBridge.pending.crossfader = cross;
        engine.setValue("[Master]", "crossfader", cross);
        DJAgentBridge.send(seq, "OK", String(engine.getValue("[Master]", "crossfader")));
        return;
    }
    if (deck !== 1 && deck !== 2) { DJAgentBridge.send(seq, "ERR", "deck"); return; }
    var group = "[Channel" + deck + "]";
    if (op === "TAKE") {
        if (DJAgentBridge.autodjActive) {
            engine.setValue("[AutoDJ]", "enabled", 0);
            if (engine.getValue("[AutoDJ]", "enabled") !== 0) {
                DJAgentBridge.send(seq, "ERR", "autodj_still_on"); return;
            }
            DJAgentBridge.owner[1] = "MANUAL";
            DJAgentBridge.owner[2] = "MANUAL";
            DJAgentBridge.send(seq, "OK", "MANUAL_BOTH"); return;
        }
        DJAgentBridge.owner[deck] = "MANUAL";
        DJAgentBridge.send(seq, "OK", "MANUAL"); return;
    }
    if (op === "RELEASE") {
        DJAgentBridge.owner[deck] = "AUTO";
        DJAgentBridge.send(seq, "OK", DJAgentBridge.autodjActive ? "MIX" : "AUTO"); return;
    }
    if (op === "READ") {
        if (value === "owner") {
            DJAgentBridge.send(seq, "OK", DJAgentBridge.owner[deck] === "MANUAL" ? "MANUAL" :
                (DJAgentBridge.autodjActive ? "MIX" : "AUTO")); return;
        }
        if (value === "crossfader") {
            DJAgentBridge.send(seq, "OK", String(engine.getValue("[Master]", "crossfader"))); return;
        }
        if (value !== "track_loaded" && value !== "play" && value !== "volume") {
            DJAgentBridge.send(seq, "ERR", "key"); return;
        }
        var read = value === "volume" ? engine.getParameter(group, value) : engine.getValue(group, value);
        DJAgentBridge.send(seq, "OK", String(read)); return;
    }
    if (DJAgentBridge.owner[deck] !== "AUTO") { DJAgentBridge.send(seq, "ERR", "manual"); return; }
    if (DJAgentBridge.autodjActive) { DJAgentBridge.send(seq, "ERR", "autodj"); return; }
    if (op === "LOAD_SELECTED") {
        if (engine.getValue(group, "play") !== 0) { DJAgentBridge.send(seq, "ERR", "playing"); return; }
        if (engine.getValue(group, "track_loaded") !== 0) { DJAgentBridge.send(seq, "ERR", "not_empty"); return; }
        engine.setValue(group, "LoadSelectedTrack", 1);
        engine.setValue(group, "LoadSelectedTrack", 0);
        DJAgentBridge.send(seq, "OK", "requested");
        return;
    }
    if (op === "PLAY" || op === "STOP") {
        if (op === "PLAY" && !engine.getValue(group, "track_loaded")) {
            DJAgentBridge.send(seq, "ERR", "no_track"); return;
        }
        var target = op === "PLAY" ? 1 : 0;
        DJAgentBridge.pending["play" + deck] = target;
        engine.setValue(group, "play", target);
        DJAgentBridge.send(seq, "OK", String(engine.getValue(group, "play")));
        return;
    }
    if (op === "LEVEL") {
        var level = Number(value);
        if (!Number.isFinite(level) || level < 0 || level > 1) {
            DJAgentBridge.send(seq, "ERR", "range"); return;
        }
        DJAgentBridge.pending["volume" + deck] = level;
        engine.setParameter(group, "volume", level);
        DJAgentBridge.send(seq, "OK", String(engine.getParameter(group, "volume")));
        return;
    }
    DJAgentBridge.send(seq, "ERR", "op");
};
