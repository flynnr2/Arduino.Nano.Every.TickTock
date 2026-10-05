'use strict';
// Run with `node tests/raspberry_pi/test_charts.cjs`; no browser or packages needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const staticDirectory = path.resolve(__dirname, '../../Raspberry.Pi/pendulum_pi/static');
const context = vm.createContext({window: {devicePixelRatio: 1},document:{querySelectorAll:()=>[]}});
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
const startup={checked:false};chart.el=()=>startup;
assert.equal(chart.timingValue(meanPoint,'window',false,false),null,'Learning period is hidden by default');
assert.equal(chart.timingValue(meanPoint,'window',true,false),null,'Learning rate is hidden by default');
startup.checked=true;
assert.equal(chart.timingValue(meanPoint,'window',false,false),2);
assert.equal(chart.timingValue(meanPoint,'window',true,false),43.2);
assert.equal(chart.timingValue(meanPoint,'window',true,true),1.8);
assert.equal(chart.learning(meanPoint,'window'),true);
assert.equal(chart.timingValue({short_period_s:2,long_period_s:2},'window',false,false),null);
const cursor={textContent:''};chart.el=id=>id==='chart-cursor'?cursor:id==='show-startup-estimates'?startup:{value:id==='chart-metric'?'period':'day'};
chart.axisValue=point=>point.time;chart.formatTime=String;chart.points=[{...meanPoint,time:1}];chart.inspect(1);
assert.match(cursor.textContent,/600-second mean 2.000000 s/);
assert.match(cursor.textContent,/estimator swing_mean_600s_pps_dual_ewma_v1/);
assert.doesNotMatch(cursor.textContent,/Short|Long|Blended|half-lives/);
startup.checked=false;chart.inspect(1);
assert.match(cursor.textContent,/startup estimate hidden/);
assert.doesNotMatch(cursor.textContent,/600-second mean 2\.000000 s/);
assert.equal(chart.timingValue({...meanPoint,quality:{window_learning:false,timebase:'HOLDOVER'}},'window',false,false),2,'Settled holdover is retained');

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

// Use the production render path for both metrics, including vertical axes.
function historyView(points,metric='rate',unit='day'){
 const chart=Object.create(context.History.prototype), elements={}, drawings={};
 chart.points=points;chart.rangeSeconds=604800;chart.loadedBounds=[0,12];chart.live=false;
 chart.el=id=>{
  if(!elements[id])elements[id]={value:id==='chart-metric'?metric:id==='rate-unit'?unit:'host',checked:false,textContent:''};
  return elements[id];
 };
 for(const id of ['timing-chart','temperature-chart','pressure-chart','humidity-chart']){
  const labels=[],strokes=[];let dash=[];
  const drawing={scale(){},save(){},restore(){},rect(){},clip(){},arc(){},fill(){},beginPath(){},moveTo(){},lineTo(){},
   fillText(value){labels.push(value);},setLineDash(value){dash=Array.from(value);},
   stroke(){if(this.strokeStyle==='#356fa0')strokes.push([...dash]);}};
  elements[id]={clientWidth:600,clientHeight:200,getContext:()=>drawing};drawings[id]={labels,strokes};
 }
 chart.formatTime=String;
 const render=()=>{for(const d of Object.values(drawings)){d.labels.length=0;d.strokes.length=0;}chart.render();};
 return {chart,elements,drawings,render};
}
const settled=time=>point(time,{window_period_s:2.00002+time*1e-7,window_rate_s_day:-.8-time*.01,
 temperature_C:20+time,humidity_pct:50+time,pressure_hPa:1020+time,quality:{window_learning:false,timebase:'PPS'}});
const warm={...settled(2),window_period_s:1.9998467531705069,window_rate_s_day:6.620770340131088,quality:{window_learning:true,timebase:'PPS'}};
for(const [metric,unit] of [['period','day'],['rate','day'],['rate','hour']]){
 const view=historyView([settled(0),settled(1),warm,settled(3),settled(4)],metric,unit);
 view.render();const hiddenTicks=view.drawings['timing-chart'].labels.slice(0,4);
 assert.equal(view.drawings['timing-chart'].strokes.length,2,'Hidden learning observation breaks the timing trace');
 const reference=historyView([settled(0),settled(1),{...warm,window_period_s:null,window_rate_s_day:null},settled(3),settled(4)],metric,unit);
 reference.render();assert.deepEqual(hiddenTicks,reference.drawings['timing-chart'].labels.slice(0,4),'Hidden outlier cannot set the vertical scale');
 const environment=view.drawings['temperature-chart'].labels.slice();
 view.elements['show-startup-estimates'].checked=true;view.render();
 assert.notDeepEqual(hiddenTicks,view.drawings['timing-chart'].labels.slice(0,4),'Opt-in restores startup scale');
 assert.deepEqual(view.drawings['timing-chart'].strokes,[[],[5,4],[5,4],[]],'Opt-in restores dashed learning transitions');
 assert.deepEqual(environment,view.drawings['temperature-chart'].labels,'Environment readings and scale stay visible');
 assert.match(view.elements['history-learning-note'].textContent,/startup estimates or PPS holdover/);
 view.elements['show-startup-estimates'].checked=false;view.render();
 assert.deepEqual(hiddenTicks,view.drawings['timing-chart'].labels.slice(0,4),'Turning the control off restores settled scale');
}
const filling=historyView([warm]);filling.render();
assert.ok(filling.drawings['timing-chart'].labels.includes('Startup estimates hidden'),'All-learning range explains the empty chart');
const noTarget=historyView([{...warm,window_rate_s_day:null}]);noTarget.render();
assert.ok(noTarget.drawings['timing-chart'].labels.includes('No rate values — a recorded target is required'),'Unset target keeps its own explanation');
const holding=historyView([settled(0),{...settled(1),quality:{window_learning:false,timebase:'HOLDOVER'}}]);holding.render();
assert.deepEqual(holding.drawings['timing-chart'].strokes,[[5,4]],'Holdover stays visible and dashed without opting into startup estimates');
const html=fs.readFileSync(path.join(staticDirectory,'index.html'),'utf8');
assert.match(html,/<label class="startup-control"><input id="show-startup-estimates" type="checkbox">Show startup estimates<\/label>/,'Startup control is labelled and unchecked by default');
// Toggling a seven-day view must not submit more history/environment jobs.
const controls=historyView([]);let historyRequests=0,environmentRequests=0;
context.document.getElementById=id=>controls.chart.el(id);
context.EnvironmentalRelationships=class{load(){environmentRequests++;}invalidate(){}};
context.ResizeObserver=class{observe(){}};context.setInterval=()=>0;
context.URLSearchParams=URLSearchParams;
const browserChart=new context.History(()=>{historyRequests++;return new Promise(()=>{});});
browserChart.points=[settled(0),warm];browserChart.loadedBounds=[0,604800];
controls.elements['show-startup-estimates'].checked=true;controls.elements['show-startup-estimates'].onchange();
controls.elements['show-startup-estimates'].checked=false;controls.elements['show-startup-estimates'].onchange();
assert.equal(historyRequests,1,'Startup toggle uses the already loaded history');
assert.equal(environmentRequests,1,'Startup toggle leaves environmental analysis alone');
console.log('PASS: startup visibility, axes, gaps, holdover, environment, cursor and canonical mean history.');
