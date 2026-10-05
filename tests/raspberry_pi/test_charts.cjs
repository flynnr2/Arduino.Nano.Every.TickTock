'use strict';
// Run with `node tests/raspberry_pi/test_charts.cjs`; no browser or packages needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const staticDirectory = path.resolve(__dirname, '../../Raspberry.Pi/pendulum_pi/static');
const context = vm.createContext({window: {devicePixelRatio: 1}});
vm.runInContext(
  fs.readFileSync(path.join(staticDirectory, 'charts.js'), 'utf8') +
  '\nglobalThis.History = ObservatoryHistory;', context,
);

// Record data strokes, excluding axis/grid strokes, using the real renderer.
function renderPoints(points, options = {}) {
  const strokes = [];
  let currentPath = [], dash = [];
  const drawing = {
    scale() {}, fillText() {}, save() {}, restore() {}, rect() {}, clip() {},
    arc() {}, fill() {},
    beginPath() {currentPath = [];},
    moveTo(x, y) {currentPath.push(['move', x, y]);},
    lineTo(x, y) {currentPath.push(['line', x, y]);},
    setLineDash(value) {dash = Array.from(value);},
    stroke() {
      if (this.strokeStyle === '#test') strokes.push({path: [...currentPath], dash});
    },
  };
  const canvas = {clientWidth: 600, clientHeight: 200, getContext() {return drawing;}};
  const chart = Object.create(context.History.prototype);
  chart.el = () => canvas;
  chart.axisValue = point => point.time;
  chart.domain = [0, 12];
  chart.formatTime = String;
  chart.points = points;
  chart.draw({id: 'test', ...options, series: [{
    color: '#test', value: point => point.value, learning: point => point.learning,
  }]});
  return strokes;
}

const point = (time, overrides = {}) => ({time, value: 2, session: 'A', segment: 'a', ...overrides});
assert.equal(renderPoints([point(0), point(1)]).length, 1, 'Compatible observations join');
for (const [name, points] of [
  ['missing metric', [point(0), point(1, {value: null}), point(2)]],
  ['recording pause', [point(0), point(1, {gap_reason: 'recording_disabled'}), point(2)]],
  ['segment boundary', [point(0), point(1, {segment: 'b'})]],
  ['session boundary', [point(0), point(1, {session: 'B'})]],
  ['backward host time', [point(1), point(0)]],
  ['duplicate host time', [point(1), point(1)]],
  ['unknown continuity', [point(0, {segment: null}), point(1, {segment: null})]],
]) {
  assert.equal(renderPoints(points).length, 0, `${name} must break the trace`);
}
assert.equal(renderPoints([
  point(0), point(1), point(2, {value: null}), point(3), point(4),
]).length, 2, 'Rendering resumes independently after a gap');
assert.deepEqual(renderPoints([point(0, {learning: true}), point(1)])[0].dash,
  [5, 4], 'A learning transition is visibly dashed');

// Exercise the user-visible disclosure of downsampling limitations.
const chart = Object.create(context.History.prototype);
chart.points = [point(0, {source: 'demo'})];
assert.match(chart.summary({reduced: true}), /extrema retained/);
const limited = chart.summary({reduced: true, budget_limited: true});
assert.match(limited, /point budget reached/i);
assert.match(limited, /some extrema may be omitted/i);
assert.match(limited, /narrower range/i);
assert.doesNotMatch(limited, /extrema retained/);
assert.match(limited, /source: demo/);
chart.points = [];
assert.match(chart.summary({budget_limited: true}), /narrower range/i);

// History uses retained canonical values; no recomputation or EWMA fallback.
const meanPoint={window_period_s:2,window_rate_s_day:43.2,
 quality:{window_learning:true},settings:{target_period_s:2,estimator_model:'swing_mean_600s_pps_dual_ewma_v1'}};
assert.equal(chart.timingValue(meanPoint,'window',false,false),2);
assert.equal(chart.timingValue(meanPoint,'window',true,false),43.2);
assert.equal(chart.timingValue(meanPoint,'window',true,true),1.8);
assert.equal(chart.learning(meanPoint,'window'),true);
assert.equal(chart.timingValue({short_period_s:2,long_period_s:2},'window',false,false),null);
const cursor={textContent:''};chart.el=id=>id==='chart-cursor'?cursor:{value:id==='chart-metric'?'period':'day'};
chart.axisValue=point=>point.time;chart.formatTime=String;chart.points=[{...meanPoint,time:1}];chart.inspect(1);
assert.match(cursor.textContent,/600-second mean 2.000000 s/);
assert.match(cursor.textContent,/estimator swing_mean_600s_pps_dual_ewma_v1/);
assert.doesNotMatch(cursor.textContent,/Short|Long|Blended|half-lives/);
console.log('PASS: chart gaps, learning, budget disclosure and canonical mean history.');

const unrelated=[point(0,{continuity:{timing:'t',sht4x:'s'}}),point(1,{segment:'b',continuity:{timing:'t',sht4x:'s'}})];
assert.equal(renderPoints(unrelated,{continuity:'timing'}).length,1,'Unrelated sensor boundary does not break valid period');
assert.equal(renderPoints(unrelated,{continuity:'sht4x',environment:true}).length,1,'Independent environmental continuity joins');
assert.equal(renderPoints([point(0),point(1,{gap_reason:'stale'})],{environment:true}).length,1,'Fresh sensor values survive stale timing');
assert.equal(renderPoints([point(0),point(1,{gap_reason:'recording_disabled'})],{environment:true}).length,0,'Recording pause still breaks environment');

const legacy=point(0,{config_revision:'config',recording_session:'recording',source:'serial',monotonic:100,capture:{drop_ir:0,drop_swing:0},quality:{timebase:'PPS'}});
const legacyTimeout={...legacy,time:5,monotonic:105,segment:'b',boundary:['utc_quality_changed']};
assert.equal(renderPoints([legacy,legacyTimeout]).length,1,'Old recorded diagnostic boundary can be safely joined');
assert.equal(renderPoints([legacy,{...legacyTimeout,boundary:['observation_gap']}]).length,1,'Old short publication pause can be joined');
assert.equal(renderPoints([legacy,{...legacyTimeout,monotonic:140}]).length,0,'Long old observation gap remains a gap');
assert.equal(renderPoints([legacy,{...legacyTimeout,boundary:['capture_loss','utc_quality_changed']}]).length,0,'Old real capture loss remains a gap');
