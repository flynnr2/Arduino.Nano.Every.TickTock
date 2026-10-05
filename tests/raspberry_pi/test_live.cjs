'use strict';
// Run with `node tests/raspberry_pi/test_live.cjs`; no browser packages needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const directory = path.resolve(__dirname, '../../Raspberry.Pi/pendulum_pi/static');
const html = fs.readFileSync(path.join(directory, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(directory, 'app.js'), 'utf8');
const nodes = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)]
  .map(match => [match[1], {textContent: '', value: 'day'}]));
const context = vm.createContext({window:{},
  byId(id) {assert(nodes[id], `Missing HTML element: ${id}`); return nodes[id];},
  configuredTarget: null, showDefinitions() {}, flatten: value => value,
});
// Use the real live renderer without starting the polling entrypoint.
vm.runInContext(
  app.slice(app.indexOf('function number('), app.indexOf('function flatten(')) +
  app.slice(app.indexOf('function renderSymmetry('), app.indexOf("byId('rate-unit').addEventListener")), context);

const horizon = {learning:true,period_us:2000000,bpm:30,gain_seconds_per_day:43.2,
 tick_us:1000000,tock_us:800000,tick_block_us:110000,tock_block_us:90000,
 tick_delta_us:200000,block_delta_us:20000,half_delta_us:220000,
 open_imbalance_pct:10,block_imbalance_pct:20};
const state={display:{available:true,timebase:'PPS',target_period_s:2,window:horizon}};
context.renderLive(state);
assert.equal(nodes['window-period'].textContent,'2.000000 s');
assert.equal(nodes['window-bpm'].textContent,'30.000000 BPM');
// Deliberately inconsistent fixture proves the client displays the canonical
// server rate instead of independently deriving it from target and period.
assert.equal(nodes['window-rate'].textContent,'Filling window · Gaining 43.200 s/day');
assert.equal(nodes['window-tick'].textContent,'1000000.00');
assert.equal(nodes['window-tock-block'].textContent,'90000.00');
assert.equal(nodes['window-tick-delta'].textContent,'+200000.00');
assert.equal(nodes['window-open-imbalance'].textContent,'+10.0000 %');
assert.equal(nodes['window-half-delta'].textContent,'+220000.00 µs');
assert.match(nodes['window-symmetry-state'].textContent,/600-second mean/);
assert.match(nodes['window-period-note'].textContent,/dual-EWMA PPS/);
nodes['rate-unit'].value='hour';context.renderLive(state);
assert.equal(nodes['window-rate'].textContent,'Filling window · Gaining 1.800 s/hour');
const metrics=['tick','tock','tick-block','tock-block','tick-delta','block-delta','open-imbalance','block-imbalance','half-delta','bpm','period'];
for(const staleState of [...['service_stale','capture_stale','stopped'].map(flag=>({...state,[flag]:true})),{display:{...state.display,stale:true}}]){
 context.renderLive(staleState);
 for(const metric of metrics)assert.equal(nodes[`window-${metric}`].textContent,'Stale');
 assert.equal(nodes['window-rate'].textContent,'Unavailable');
}
context.renderLive({});
for(const metric of metrics)assert.equal(nodes[`window-${metric}`].textContent,'—');
context.renderLive({display:{available:true,short:horizon,long:horizon}});
assert.equal(nodes['window-period'].textContent,'—','Old statuses cannot masquerade as a mean');
context.renderLive({display:{...state.display,window:{...horizon,tick_delta_us:-.0001,open_imbalance_pct:NaN,block_imbalance_pct:Infinity,gain_seconds_per_day:null}}});
assert.equal(nodes['window-tick-delta'].textContent,'0.00');
assert.equal(nodes['window-open-imbalance'].textContent,'—');
assert.equal(nodes['window-rate'].textContent,'Choose a target for rate');
context.renderLive({display:{...state.display,timebase:'HOLDOVER',pps:{initialized:true,stale:true,expired:false,status:'HOLDOVER',blended_hz:16000000,calibration_age_seconds:90}}});
assert.equal(nodes.timebase.textContent,'PPS holdover');assert.match(nodes['timebase-note'].textContent,/90.0 s old/);assert.equal(nodes.rate.textContent,'16000000.0 Hz');
context.renderLive({display:{...state.display,stale:true,timebase:'STALE',pps:{initialized:true,expired:true,status:'EXPIRED'}}});
assert.equal(nodes.timebase.textContent,'Unavailable');assert.equal(nodes.rate.textContent,'Unavailable');
console.log('PASS: canonical mean, signed interval diagnostics, old status, staleness and PPS holdover.');
context.renderLive({display:{...state.display,window:{...horizon,priming:{active:true,checkpoint_age_seconds:42,fresh_seconds:12}}}});
assert.equal(nodes['window-learning'].textContent,'Restored / provisional');
assert.match(nodes['window-period-note'].textContent,/42 s old/);
assert.match(nodes['window-period-note'].textContent,/12 \/ 600 s fresh/);
