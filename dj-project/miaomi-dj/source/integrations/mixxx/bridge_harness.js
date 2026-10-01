// A protocol simulation only. Real Mixxx and hardware require separate checks.
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const path = require("path");
const values = {"[Channel1]|track_loaded": 1, "[Channel2]|track_loaded": 0,
    "[Channel1]|play": 0, "[Channel2]|play": 0,
    "[Channel1]|volume": 0.5, "[Channel2]|volume": 0.5,
    "[Master]|crossfader": -1};
const callbacks = {};
const emitted = [];
let blockAutoDjOff = false;
const engine = {
    getValue: (g, k) => values[g + "|" + k] || 0,
    getParameter: (g, k) => values[g + "|" + k] || 0,
    setValue(g, k, v) {
        if (blockAutoDjOff && g === "[AutoDJ]" && k === "enabled" && v === 0) return;
        values[g + "|" + k] = v;
        (callbacks[g + "|" + k] || []).forEach(fn => fn(v));
    },
    setParameter(g, k, v) { this.setValue(g, k, v); },
    makeConnection(g, k, fn) {
        const name = g + "|" + k;
        (callbacks[name] ||= []).push(fn);
        return {
            disconnect() { callbacks[name] = callbacks[name].filter(x => x !== fn); },
            trigger() { fn(Object.hasOwn(values, name) ? values[name] : 0); },
        };
    },
};
const midi = {sendSysexMsg(bytes, length) { assert.equal(bytes.length, length); emitted.push(bytes); }};
const context = vm.createContext({engine, midi, console});
vm.runInContext(fs.readFileSync(path.join(__dirname, "dj-agent-bridge.js"), "utf8"), context);
const bridge = context.DJAgentBridge;
bridge.init("DJ Agent bridge", false);
function command(seq, op, deck, value = "") {
    const payload = `DJ1|${seq}|${op}|${deck}|${value}`;
    const data = [0xF0, 0x7D, 0x44, 0x4A, 0x01, ...Buffer.from(payload), 0xF7];
    // Mixxx 2.5 dispatches SysEx through functionprefix.incomingData.
    bridge.incomingData(data, data.length);
    return Buffer.from(emitted.pop().slice(5, -1)).toString("ascii");
}
assert.equal(command(1, "HELLO", 0), "DJ1|1|OK|1");
assert.equal(command(2, "READ", 1, "track_loaded"), "DJ1|2|OK|1");
assert.equal(command(3, "PLAY", 2), "DJ1|3|ERR|no_track");
assert.equal(command(4, "PLAY", 1), "DJ1|4|OK|1");
assert.equal(command(5, "LEVEL", 1, "0.75"), "DJ1|5|OK|0.75");
assert.equal(command(6, "CROSSFADE", 0, "0.2"), "DJ1|6|OK|0.2");
engine.setValue("[Channel1]", "volume", 0.3); // physical control or GUI
assert.equal(command(7, "READ", 1, "owner"), "DJ1|7|OK|MANUAL");
assert.equal(command(8, "STOP", 1), "DJ1|8|ERR|manual");
assert.equal(command(9, "CROSSFADE", 0, "0.5"), "DJ1|9|ERR|manual");
assert.equal(command(10, "RELEASE", 1), "DJ1|10|OK|AUTO");
assert.equal(command(11, "STOP", 1), "DJ1|11|OK|0");
assert.equal(command(12, "LEVEL", 1, "NaN"), "DJ1|12|ERR|range");
assert.equal(command(13, "AUTODJ_FADE", 0), "DJ1|13|ERR|autodj_off");
assert.equal(command(14, "AUTODJ_ENABLE", 0), "DJ1|14|OK|1");
assert.equal(command(15, "AUTODJ_FADE", 0), "DJ1|15|OK|1");
engine.setValue("[Master]", "crossfader", 0.9); // Mixxx Auto DJ transition
assert.equal(command(16, "READ", 1, "owner"), "DJ1|16|OK|MIX");
assert.equal(command(17, "LEVEL", 1, "0.4"), "DJ1|17|ERR|autodj");
assert.equal(command(18, "AUTODJ_FADE", 0), "DJ1|18|OK|1");
engine.setValue("[AutoDJ]", "enabled", 0); // user disables Auto DJ in Mixxx
assert.equal(command(19, "READ", 1, "owner"), "DJ1|19|OK|AUTO");
assert.equal(command(20, "LEVEL", 1, "0.4"), "DJ1|20|OK|0.4");
engine.setValue("[AutoDJ]", "enabled", 1);
bridge.shutdown();
bridge.init("DJ Agent bridge", false); // mapping loaded while Mixxx Auto DJ is already on
assert.equal(command(21, "READ", 1, "owner"), "DJ1|21|OK|MIX");
assert.equal(command(22, "LEVEL", 1, "0.3"), "DJ1|22|ERR|autodj");
blockAutoDjOff = true;
assert.equal(command(23, "TAKE", 1), "DJ1|23|ERR|autodj_still_on");
assert.equal(command(24, "READ", 1, "owner"), "DJ1|24|OK|MIX");
blockAutoDjOff = false;
assert.equal(command(25, "TAKE", 1), "DJ1|25|OK|MANUAL_BOTH");
assert.equal(engine.getValue("[AutoDJ]", "enabled"), 0);
assert.equal(command(26, "READ", 1, "owner"), "DJ1|26|OK|MANUAL");
assert.equal(command(27, "READ", 2, "owner"), "DJ1|27|OK|MANUAL");
assert.equal(command(28, "AUTODJ_ENABLE", 0), "DJ1|28|ERR|manual");
assert.equal(command(29, "RELEASE", 1), "DJ1|29|OK|AUTO");
assert.equal(command(30, "CROSSFADE", 0, "0.5"), "DJ1|30|ERR|manual");
assert.equal(command(31, "RELEASE", 2), "DJ1|31|OK|AUTO");
assert.equal(command(32, "CROSSFADE", 0, "0.5"), "DJ1|32|OK|0.5");
bridge.shutdown();
values["[AutoDJ]|enabled"] = NaN;
bridge.init("DJ Agent bridge", false);
assert.equal(command(33, "HELLO", 0), "DJ1|33|ERR|not_ready");
assert.equal(command(34, "LEVEL", 1, "0.6"), "DJ1|34|ERR|not_ready");
engine.setValue("[AutoDJ]", "enabled", 0);
assert.equal(command(35, "HELLO", 0), "DJ1|35|OK|1");
bridge.shutdown();
console.log("PASS: Mixxx mapping protocol simulation");
