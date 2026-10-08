const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../monitoring/nursery_watch.js'), 'utf8');
const run = new (Object.getPrototypeOf(async function(){}).constructor)('ws', 'hookState', code);
const now=Date.now(), iso=new Date(now).toISOString();
const row=s=>({s,c:iso});
const record={v:2,phase:'ready',expected:'on',issued:now/1000-2,booked:now/1000-1,t:21.8,generation:'test:1',reason:'zigbee_ack'};
const data=()=>({temp:row('21.8'),tgt:row('21'),bel:row('on'),sw:row('off'),stat:row('CLICK'),snap:row('21.5'),controller:row('on'),legacy:row('off'),listener:row('off'),recovery:row('off'),phaseb:row('off'),journal:row(JSON.stringify(record)),journal_events:[]});
const state=()=>({guardedSince:now-3600000,guardSeen:[],gatewayReady:true,bel:'on',tgt:21});
async function evaluate(d=data(),st=state()) {
  return run({host:{exec:async({args})=>{
    assert.ok(args[1].includes('switch.er_tong_fang_nursery_hvac_toggle'));
    assert.ok(args[1].includes('select.er_tong_fang_nursery_hvac_toggle_mode'));
    assert.ok(!args[1].includes('adp_cn_976148245'));
    return {exitCode:0,stdout:JSON.stringify(d)};
  }}},st);
}
test('Zigbee acknowledgement is reported once and never claimed physical',async()=>{
  const d=data(),r=await evaluate(d);
  assert.equal(r.state.gatewayReady,true);
  assert.match(r.message,/Zigbee command acknowledged/);
  assert.match(r.message,/physical timing unconfirmed/);
  const again=await evaluate(d,r.state);
  assert.ok(!again.message?.includes('Zigbee command acknowledged'));
});
test('mode and availability gates match the controller',async()=>{
  for(const value of ['SWITCH','PROGRAM','unknown','unavailable']){
    const d=data();d.stat=row(value);const r=await evaluate(d);
    assert.equal(r.state.gatewayReady,false);assert.match(r.message,/NOT IN CLICK MODE/);
    assert.ok(!(await evaluate(d,r.state)).message?.includes('NOT IN CLICK MODE'));
  }
  const d=data();d.sw=row('unavailable');assert.equal((await evaluate(d)).state.gatewayReady,false);
});
test('recovery keeps the command protection wording',async()=>{
  const st=state();st.gatewayReady=false;
  assert.match((await evaluate(data(),st)).message,/READY.*protection intervals/);
});
test('temperature alerts and prior journal history are retained',async()=>{
  const d=data();d.temp=row('19.8');const st=state();st.guardSeen=['old-key'];st.cycles=7;
  const r=await evaluate(d,st);
  assert.match(r.message,/NURSERY COLD/);assert.ok(r.state.guardSeen.includes('old-key'));
  assert.equal(st.guardSeen.length,1);
});
test('legacy feedback remains distinguishable from Zigbee acknowledgement',async()=>{
  const d=data();d.journal=row(JSON.stringify({...record,reason:'feedback'}));
  const r=await evaluate(d);assert.match(r.message,/feedback booked/);assert.ok(!r.message.includes('Zigbee command acknowledged'));
});
