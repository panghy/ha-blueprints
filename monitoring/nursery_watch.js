// Read-only workspace hook, polled every 7.5 minutes. Installation replaces
// SEED with the retiring hook's complete lastState; never reset its history.
// ZHA switch state is command receipt evidence, not the AC's physical state.
const PY = "import json,urllib.request,io,os,datetime,urllib.parse\nBASE=\"http://192.168.1.13:8123\"\nraw=io.open(os.path.expanduser(\"~/.ha_token_header\")).read().strip()\nn,_,v=raw.partition(\":\"); H={n.strip():v.strip()}\ndef get(path):\n    return json.loads(urllib.request.urlopen(urllib.request.Request(BASE+path,headers=H),timeout=12).read().decode())\nrows={r[\"entity_id\"]:r for r in get(\"/api/states\")}\nentities={\"temp\":\"sensor.nursery_temperature\",\"tgt\":\"input_number.nursery_target_temperature\",\"bel\":\"input_boolean.nursery_hvac_cooling_active\",\"sw\":\"switch.er_tong_fang_nursery_hvac_toggle\",\"stat\":\"select.er_tong_fang_nursery_hvac_toggle_mode\",\"snap\":\"input_number.nursery_hvac_temp_at_off_press\",\"mode\":\"input_select.house_hvac_mode\",\"resync\":\"automation.hvac_startup_belief_resync\",\"journal\":\"input_text.nursery_hvac_command\",\"controller\":\"automation.nursery_guarded_thermostat\",\"legacy\":\"automation.nursery_hvac_automation\",\"listener\":\"automation.nursery_hvac_press_listener\",\"recovery\":\"automation.nursery_lost_press_recovery\",\"phaseb\":\"input_boolean.nursery_lost_press_recovery\"}\nout={}\nfor k,e in entities.items():\n    r=rows.get(e)\n    out[k]={\"s\":r.get(\"state\"),\"c\":r.get(\"last_changed\"),\"lt\":r.get(\"attributes\",{}).get(\"last_triggered\")} if r else {\"err\":\"missing entity\"}\nend=datetime.datetime.now(datetime.timezone.utc);start=end-datetime.timedelta(hours=2)\ntry:\n    hist=get(\"/api/history/period/\"+urllib.parse.quote(start.isoformat())+\"?\"+urllib.parse.urlencode({\"filter_entity_id\":entities[\"journal\"],\"end_time\":end.isoformat(),\"minimal_response\":\"false\",\"no_attributes\":\"true\",\"skip_initial_state\":\"true\"}))\n    out[\"journal_events\"]=[{\"s\":r[\"state\"],\"c\":r.get(\"last_changed\")} for series in hist for r in series]\nexcept Exception as ex:out[\"history_error\"]=str(ex)\nprint(json.dumps(out))";
const ABS_FLOOR = 20.0, ABS_CEIL = 24.5, BAND_TGT_MAX = 22.5, LOST_MIN = 15;
const SEED = {};
const st = hookState || SEED;
const now = Date.now();
const hk = (ms) => new Date(ms + 8*3600*1000).toISOString().slice(11,16);
const r = await ws.host.exec({ command: "python3", args: ["-c", PY], timeoutMs: 45000 });
if (r.exitCode !== 0 || !r.stdout) {
  const fails = (st.fails || 0) + 1;
  if (fails === 3) return { dispatch: true, message: "[Nursery watch] HA unreachable on 3 consecutive polls: " + String(r.stderr || "").slice(0,200), state: Object.assign({}, st, { fails: 0 }) };
  return { dispatch: false, state: Object.assign({}, st, { fails }) };
}
let d; try { d = JSON.parse(r.stdout.trim()); } catch (e) { return { dispatch: false, state: Object.assign({}, st, { fails: (st.fails||0)+1 }) }; }
if (!d.temp || d.temp.err || !d.tgt || d.tgt.err || !d.bel || d.bel.err) return { dispatch: false, state: Object.assign({}, st, { fails: (st.fails||0)+1 }) };
const temp = parseFloat(d.temp.s), tgt = parseFloat(d.tgt.s), bel = d.bel.s;
if (!isFinite(temp) || !isFinite(tgt)) return { dispatch: false, state: Object.assign({}, st, { fails: (st.fails||0)+1 }) };
const ns = Object.assign({}, st, { fails: 0, temp, tgt, bel });
if (st.tgt !== undefined && st.tgt !== tgt) { ns.targetChangedAt = now; ns.coldRef = null; ns.hotRef = false; }
const targetStep = ns.targetChangedAt && (now - ns.targetChangedAt) < 20*60*1000;
const bandActive = tgt <= BAND_TGT_MAX;
const dayKey = new Date(now + 8*3600*1000).toISOString().slice(0,10);
if (ns.dayKey !== dayKey) { ns.dayKey = dayKey; ns.dayMin = temp; ns.dayMax = temp; ns.cycles = 0; ns.lost = 0; ns.presses = 0; ns.retries = 0; ns.phantoms = 0; ns.band = null; }
ns.dayMin = Math.min(ns.dayMin === undefined ? temp : ns.dayMin, temp);
ns.dayMax = Math.max(ns.dayMax === undefined ? temp : ns.dayMax, temp);
const msgs = [];
const tMs = Date.parse(d.temp.c);
if (ns.cycle && isFinite(tMs) && tMs > ns.cycle.offMs) { if (ns.cycle.trough === null || ns.cycle.trough === undefined || temp < ns.cycle.trough) ns.cycle.trough = temp; }
const swOk = d.sw && !d.sw.err && d.sw.s !== "unavailable" && d.sw.s !== "unknown";
const swMs = swOk ? Date.parse(d.sw.c) : NaN;
const belMs = Date.parse(d.bel.c);
const haStartMs = (d.resync && !d.resync.err && d.resync.lt) ? Date.parse(d.resync.lt) : NaN;
const restartArtifact = (ms) => isFinite(haStartMs) && Math.abs(ms - haStartMs) < 10*60*1000;
const statOk = d.stat && !d.stat.err && d.stat.s !== "unavailable" && d.stat.s !== "unknown";
const statMs = statOk ? Date.parse(d.stat.c) : NaN;
const statIdle = statOk && d.stat.s === "CLICK";
let ackedLagMin = null, retried = false;
const cmdOk = d.cmd && !d.cmd.err && d.cmd.w;
const cmdMs = cmdOk ? Date.parse(d.cmd.w) : NaN;
const phMs = (d.ph && d.ph.w) ? Date.parse(d.ph.w) : NaN;
if (isFinite(phMs) && phMs !== ns.lastPhMs) { ns.lastPhMs = phMs; ns.phantoms = (ns.phantoms || 0) + 1; }
const safeParse = (s) => { try { const v = JSON.parse(s); return v && typeof v === "object" && !Array.isArray(v) ? v : null; } catch (_) { return null; } };
const liveRecord = d.journal && !d.journal.err ? safeParse(d.journal.s) : null;
const gatewayReady = Boolean(d.sw && !d.sw.err && ["on", "off"].includes(d.sw.s) && d.stat && !d.stat.err && d.stat.s === "CLICK");
const recovering = liveRecord && liveRecord.v === 2;
if (st.gatewayReady === false && gatewayReady) {
  if (recovering) msgs.push("ZIGBEE FINGERBOT READY; automatic recovery can reevaluate demand after protection intervals");
  else if (liveRecord && liveRecord.phase === "needs_verification" && liveRecord.reason === "gateway_unavailable") msgs.push("GATEWAY AVAILABLE AGAIN; controller remains locked; fresh physical check needed before resumption");
}
if (!gatewayReady && st.gatewayReady !== false) msgs.push("NURSERY ZIGBEE FINGERBOT UNAVAILABLE OR NOT IN CLICK MODE; automatic commands will resume when ready");
ns.gatewayReady = gatewayReady;
const seen = new Set(ns.guardSeen || []);
const history = (d.journal_events || []).concat(d.journal && !d.journal.err ? [d.journal] : []);
for (const row of history) {
  const rec = safeParse(row.s);
  if (!rec || ![1, 2].includes(rec.v) || (recovering && rec.v === 1)) continue;
  const changed = Date.parse(row.c);
  if (!isFinite(changed) || changed < (ns.guardedSince || now)) continue;
  const key = rec.v === 2
    ? JSON.stringify([2, rec.phase, rec.issued, rec.booked, rec.expected, rec.reason])
    : JSON.stringify([rec.phase, rec.issued, rec.booked, rec.expected, rec.reason, row.c]);
  if (seen.has(key)) continue;
  seen.add(key);
  const direction = String(rec.expected).toUpperCase();
  if (rec.v === 2) {
    if (rec.reason === "migration") continue;
    if (rec.phase === "pending" && rec.issued > 0 && ["command", "retry"].includes(rec.reason)) {
      ns.presses = (ns.presses || 0) + 1;
      if (rec.reason === "retry") ns.retries = (ns.retries || 0) + 1;
      msgs.push("automatic " + direction + (rec.reason === "retry" ? " retry" : " command") + " recorded " + hk(rec.issued * 1000) + "; awaiting feedback or temperature evidence; physical actuation unconfirmed");
    } else if (rec.phase === "ready" && ["feedback", "zigbee_ack"].includes(rec.reason) && rec.issued > 0 && rec.booked >= rec.issued) {
      ackedLagMin = (rec.booked - rec.issued) / 60;
      msgs.push(direction + (rec.reason === "zigbee_ack" ? " Zigbee command acknowledged " : " feedback booked ") + hk(rec.booked * 1000) + " (" + ackedLagMin.toFixed(1) + " min after command); physical timing unconfirmed");
    } else if (rec.phase === "ready" && rec.reason === "temperature") {
      msgs.push("temperature-based " + direction + " estimate recorded " + hk(rec.booked * 1000) + "; physical state unconfirmed");
    } else if (rec.phase === "ready" && rec.reason === "estimate_adopted") {
      msgs.push("existing " + direction + " estimate adopted automatically; 13-minute protection interval applies; no physical check claimed");
    }
  } else if (rec.phase === "pending" && isFinite(rec.issued) && rec.issued > 0) {
    ns.presses = (ns.presses || 0) + 1;
    msgs.push("guarded " + direction + " command recorded " + hk(rec.issued * 1000) + "; awaiting feedback, no retry; physical actuation unconfirmed");
  } else if (rec.phase === "ready" && isFinite(rec.issued) && rec.issued > 0 && rec.booked >= rec.issued) {
    ackedLagMin = (rec.booked - rec.issued) / 60;
    msgs.push("guarded " + direction + " feedback booked " + hk(rec.booked * 1000) + " (" + ackedLagMin.toFixed(1) + " min after command); physical timing unconfirmed");
  } else if (rec.phase === "ready" && rec.issued === 0) {
    msgs.push("guarded controller ready after physical verification; minimum protection interval still applies");
  } else if (rec.phase === "needs_verification" && !["startup", "physical_check"].includes(rec.reason)) {
    if (rec.reason === "feedback_timeout") ns.lost = (ns.lost || 0) + 1;
    msgs.push("CONTROL LOCKED: " + rec.reason + "; physical check required, no automatic power retry");
  }
}
ns.guardSeen = Array.from(seen).slice(-80);
const controlIssue = ["legacy", "listener", "recovery", "phaseb"].filter(k => d[k] && d[k].s === "on").join(",");
if (controlIssue && controlIssue !== ns.controlIssue) msgs.push("CONTROL CONFLICT: legacy nursery path enabled: " + controlIssue);
ns.controlIssue = controlIssue;
const journalIssue = !liveRecord || !([1, 2].includes(liveRecord.v) && (liveRecord.v === 2 ? ["ready", "pending"] : ["verified", "ready", "pending", "needs_verification"]).includes(liveRecord.phase));
if (journalIssue && !ns.journalIssue) msgs.push("CONTROL JOURNAL UNAVAILABLE: inspect nursery controller; physical state unconfirmed");
ns.journalIssue = journalIssue;
if (d.history_error && !ns.historyError) msgs.push("Command history unavailable; watching current journal only");
ns.historyError = Boolean(d.history_error);
ns.controller = d.controller && d.controller.s;
ns.guardPhase = liveRecord && liveRecord.phase;
ns.guardReason = liveRecord && liveRecord.reason;
ns.pend = null;
if (swOk) ns.swLast = d.sw.s;
if (st.bel === "off" && bel === "on") { ns.onTemp = isFinite(temp) ? temp : null; ns.onMs = isFinite(belMs) ? belMs : now; }
if (st.bel === "on" && bel === "off") {
  const snap = parseFloat(d.snap && d.snap.s);
  if (targetStep || !bandActive) { ns.cycle = null; }
  else {
    ns.cycles = (ns.cycles || 0) + 1;
    const lag = (ackedLagMin !== null) ? ackedLagMin : null;
    ns.cycle = { offMs: isFinite(belMs) ? belMs : now, lagMin: lag, snap: isFinite(snap) ? snap : null, offTemp: temp, tgt, trough: (isFinite(temp) ? temp : null), onTemp: (ns.onTemp === undefined ? null : ns.onTemp), runMin: (ns.onMs ? ((isFinite(belMs) ? belMs : now) - ns.onMs)/60000 : null), n: ns.cycles, reported: false };
    if (retried) msgs.push("cycle " + ns.cycles + " OFF belief updated " + hk(belMs) + " after repeat command; physical timing unknown (first command " + (lag !== null ? lag.toFixed(1) + " min earlier" : "unknown") + "), snapshot " + (isFinite(snap) ? snap : "?") + " vs target " + tgt);
  }
}
if (ns.cycle && !ns.cycle.reported && (now - ns.cycle.offMs) > 25*60*1000) {
  const c = ns.cycle;
  if (c.trough === null || c.trough === undefined) msgs.push("cycle " + c.n + " trough UNMEASURED - no probe sample in 25 min after OFF estimate (temperature then " + (c.offTemp !== undefined ? c.offTemp : "?") + ", target " + c.tgt + "); now " + temp);
  else {
    const coast = (c.offTemp === undefined || c.offTemp === null) ? null : (c.offTemp - c.trough);
    const b = ns.band || { n: 0, troughMin: null, troughMax: null, peakMin: null, peakMax: null, coastMax: 0, runMax: null };
    b.n = b.n + 1;
    b.troughMin = (b.troughMin === null) ? c.trough : Math.min(b.troughMin, c.trough);
    b.troughMax = (b.troughMax === null) ? c.trough : Math.max(b.troughMax, c.trough);
    if (c.onTemp !== null && c.onTemp !== undefined) {
      b.peakMin = (b.peakMin === null) ? c.onTemp : Math.min(b.peakMin, c.onTemp);
      b.peakMax = (b.peakMax === null) ? c.onTemp : Math.max(b.peakMax, c.onTemp);
    }
    if (coast !== null && coast > b.coastMax) b.coastMax = coast;
    if (c.runMin !== null && c.runMin !== undefined && (b.runMax === null || c.runMin > b.runMax)) b.runMax = c.runMin;
    ns.band = b;
    const deep = (c.trough - c.tgt) <= -0.5;
    const bigCoast = coast !== null && coast >= 0.4;
    if (deep || bigCoast) msgs.push("cycle " + c.n + " trough " + c.trough.toFixed(1) + " vs target " + c.tgt + " (delta " + (c.trough - c.tgt).toFixed(1) + (coast !== null ? ", post-estimate drop " + coast.toFixed(1) : "") + (c.runMin ? ", belief-on interval " + c.runMin.toFixed(1) + " min" : "") + "; temperature seen with OFF estimate " + (c.offTemp !== undefined ? c.offTemp : "?") + "); now " + temp);
  }
  ns.cycle = null;
}
const floor = bandActive ? Math.max(tgt - 1.0, ABS_FLOOR) : ABS_FLOOR;
if (!targetStep && temp <= floor) {
  if (ns.coldRef === null || ns.coldRef === undefined) { ns.coldRef = temp; msgs.push("NURSERY COLD: " + temp + " (floor " + floor.toFixed(1) + ", target " + tgt + ", cooling belief " + bel + ")"); }
  else if (temp <= ns.coldRef - 0.3) { ns.coldRef = temp; msgs.push("NURSERY DEEPENING: " + temp + " (target " + tgt + ", cooling belief " + bel + ")"); }
} else if (temp > floor + 0.2) { ns.coldRef = null; }
const ceil = bandActive ? Math.min(tgt + 1.6, ABS_CEIL) : ABS_CEIL;
if (!targetStep && temp >= ceil) { if (!ns.hotRef) { ns.hotRef = true; msgs.push("NURSERY HOT: " + temp + " (ceiling " + ceil.toFixed(1) + ", target " + tgt + ", cooling belief " + bel + ")"); } }
else if (temp < ceil - 0.2) { ns.hotRef = false; }
const hhmm = new Date(now + 8*3600*1000).toISOString().slice(11,16);
if (hhmm >= "07:05" && hhmm < "07:20" && ns.lastDigest !== dayKey) {
  ns.lastDigest = dayKey;
  const bb = ns.band;
  msgs.push("daily digest " + dayKey + ": now " + temp + ", min " + ns.dayMin + ", max " + ns.dayMax + ", target " + tgt + ", cooling belief " + bel + ", belief cycles " + (ns.cycles || 0) + ", commands observed awaiting feedback " + (ns.presses || 0) + ", automatic retries " + (ns.retries || 0) + ", guarded timeouts " + (ns.lost || 0) + ", unattributed switch edges " + (ns.phantoms === undefined ? "?" : ns.phantoms) + (bb && bb.n ? ", measured " + bb.n + " belief cycles: troughs " + bb.troughMin + "-" + bb.troughMax + ", temperatures seen with ON estimate " + (bb.peakMin === null ? "?" : bb.peakMin + "-" + bb.peakMax) + ", max post-estimate drop " + bb.coastMax.toFixed(1) + (bb.runMax ? ", longest belief-on interval " + bb.runMax.toFixed(1) + " min" : "") : ""));
}
if (msgs.length) return { dispatch: true, message: "[Nursery temp watch " + hhmm + " HKT] " + msgs.join(" | "), state: ns };
return { dispatch: false, state: ns };