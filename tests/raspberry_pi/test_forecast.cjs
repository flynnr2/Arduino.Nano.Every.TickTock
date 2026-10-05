'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const drawing={scale(){},clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},arc(){},fill(){}};
const nodes={};const context=vm.createContext({window:{devicePixelRatio:1},ResizeObserver:class{observe(){}},document:{getElementById(id){return nodes[id] ||= {textContent:'',clientWidth:600,clientHeight:180,getContext(){return drawing;}};}}});
vm.runInContext(fs.readFileSync('Raspberry.Pi/pendulum_pi/static/forecast.js','utf8')+'\nglobalThis.Forecast=SwingForecast;',context);
const chart=new context.Forecast();
const forecast=(seq=10)=>({enabled:true,available:true,learning:false,cycle_length:15,model:'pattern_v1',next:{target_seq:seq+1,origin_seq:seq,period_us:2000012,timebase:'PPS'},last:{target_seq:seq,origin_seq:seq-1,observed_period_us:2000002,error_us:2,baseline_error_us:200},recent:{count:100,rmse_us:12,baseline_rmse_us:408}});
chart.update(undefined,false,1);assert.equal(nodes['forecast-state'].textContent,'Disabled');
chart.update(forecast(),false,1);assert.equal(nodes['forecast-next'].textContent,'2.000012 s');assert.equal(nodes['forecast-rmse'].textContent,'12.00 / 408.00 µs');
assert.equal(chart.points.length,1);chart.update(forecast(),false,1);assert.equal(chart.points.length,1,'Repeated snapshot is not an extra result');
chart.update(forecast(11),false,1);assert.equal(chart.points[1].join,true);
chart.update(forecast(13),false,1);assert.equal(chart.points[2].join,false,'Missed swings stay gaps');
chart.update(forecast(13),true,1);assert.equal(nodes['forecast-state'].textContent,'Stale');assert.equal(nodes['forecast-error'].textContent,'—');
chart.update(forecast(14),false,1);assert.equal(chart.points.at(-1).join,false,'Stale interval breaks the trace');
chart.update(forecast(14),false,2);assert.equal(chart.points.length,1,'Estimator reset clears incompatible live results');
chart.update({...forecast(15),cycle_length:1},false,2);assert.equal(chart.points.length,1,'Cycle configuration resets the trace');
for(let seq=16;seq<200;seq++)chart.update({...forecast(seq),cycle_length:1},false,2);
assert.equal(chart.points.length,120,'Live chart has a finite bound');
console.log('PASS: forecast causal status, duplicate snapshots, gaps, resets, stale masking and bounded chart.');

const server=seq=>({...forecast(seq),revision:1,results:Array.from({length:seq-9},(_,i)=>({
 target_seq:10+i,origin_seq:9+i,error_us:2,baseline_error_us:200,
 elapsed_seconds:20+i*2,observed_epoch:1800000000+i*2}))});
const buffered=new context.Forecast();
buffered.update(server(10),false,1);buffered.update(server(14),false,1);
assert.equal(buffered.points.length,5,'Service buffer backfills four scored results after missed polls');
assert(buffered.points.slice(1).every(p=>p.join),'Backfilled consecutive results join');
buffered.update(server(14),true,1);
assert.equal(buffered.points.length,5,'Stale live values do not discard previously scored results');
buffered.update(server(15),false,1);
assert.equal(buffered.points.at(-1).join,true,'Recovery connects retained scores with proven sequence continuity');
const reopened=new context.Forecast();reopened.update(server(15),false,1);
assert.equal(reopened.points.length,6,'Page reload restores service-held chart history');
reopened.update({...server(15),revision:2,results:[]},false,1);
assert.equal(reopened.points.length,0,'A genuine model reset clears historical results');
