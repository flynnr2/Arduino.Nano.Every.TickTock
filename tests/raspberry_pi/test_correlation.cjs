'use strict';
// Real renderer: shared selection, all five plots, uncertainty and stale replies.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const directory=path.resolve(__dirname,'../../Raspberry.Pi/pendulum_pi/static');
const html=fs.readFileSync(path.join(directory,'index.html'),'utf8');
let arcs=0,strokes=[],current=[];
const drawing={scale(){},fillText(){},beginPath(){current=[];},moveTo(x,y){current.push(['move',x,y]);},lineTo(x,y){current.push(['line',x,y]);},stroke(){strokes.push({color:this.strokeStyle,path:current});},save(){},restore(){},rect(){},clip(){},setLineDash(){},arc(){arcs++;},fill(){}};
const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],{
 value:'',textContent:'',disabled:false,options:[],clientWidth:600,clientHeight:280,
 replaceChildren(){this.options=[];this.value='';},add(option){this.options.push(option);},
 getContext(){return drawing;},getBoundingClientRect(){return {left:0,top:0};},
}]));
const context=vm.createContext({window:{devicePixelRatio:1},document:{getElementById(id){assert(nodes[id],`Missing ${id}`);return nodes[id];}},
 ResizeObserver:class{observe(){}},Option:class{constructor(text,value){this.text=text;this.value=value;}},URLSearchParams,AbortController,setTimeout,clearTimeout,Date});
vm.runInContext(fs.readFileSync(path.join(directory,'correlation.js'),'utf8')+'\nglobalThis.Correlation=EnvironmentalRelationships;',context);
const variables=['temperature_C','humidity_pct','pressure_hPa'];
const group=(segment,count)=>({session:'s',segment,source:'demo',timebase:'PPS',start:100,end:100+count*60,samples:count*6,settings:{},
 spans:{temperature_C:[20,24],humidity_pct:[50,54],pressure_hPa:[1000,1004]},
 points:Array.from({length:count},(_,i)=>({time:100+i*60,bucket_start:60+i*60,temperature_C:20+i,humidity_pct:50+i,pressure_hPa:1000+i,
  period_s:2+i*1e-6,fitted_period_s:2+i*1e-6+.2e-6,residual_us:-.2,samples:6})),
 individual:Object.fromEntries(variables.map(key=>[key,{available:true,r_squared:.8,slope_us_per_unit:1,mean_x:key==='temperature_C'?21:key==='humidity_pct'?51:1001,mean_period_s:2.000001,
  uncertainty:{available:false,reason:'Insufficient history for uncertainty'}}])),
 joint:{available:true,coefficients_available:true,r_squared:.9,adjusted_r_squared:.89,residual_rms_us:.2,residual_reduction_pct:50,predictor_rank:3,condition_number:2,condition_limit:30,
  coefficients:Object.fromEntries(variables.map(key=>[key,{slope_us_per_unit:1,se_us_per_unit:.1,ci95_us_per_unit:[.804,1.196]}])),
  uncertainty:{available:true,window_seconds:600,lag_buckets:10,minimum_points:55}}});
const response={bucket_seconds:60,segments:[group('a',3),group('b',4)]};
(async()=>{
 const requests=[];const chart=new context.Correlation(async url=>{requests.push(url);return response;});
 await chart.load(0,1000,'s',1);
 assert.equal(nodes['correlation-segment'].value,'s:b');assert.equal(nodes['correlation-count'].textContent,'4');
 assert.equal(nodes['environment-temperature-slope'].textContent,'+1.00 µs/°C');
 assert.equal(nodes['environment-humidity-slope'].textContent,'+1.00 µs/% RH');
 assert.equal(nodes['environment-pressure-slope'].textContent,'+1.00 µs/hPa');
 assert.equal(nodes['environment-r2'].textContent,'0.900');assert.equal(nodes['environment-rms'].textContent,'0.200 µs');
 assert.equal(nodes['environment-improvement'].textContent,'50.0%');
 assert.equal(nodes['environment-temperature-adjusted-ci'].textContent,'0.804 to 1.20 µs/°C');
 assert.match(nodes['correlation-state'].textContent,/2 separate segments/);
 assert.match(nodes['correlation-details'].textContent,/600-second mean/);
 assert.match(nodes['environment-period-reference'].textContent,/period offsets.*selected mean/);
 assert.equal(arcs,24,'Three scatters, recorded/fitted and residual show only selected segment');
 const point=chart.hits['environment-pressure-chart'][0];chart.inspect('environment-pressure-chart',{clientX:point.x,clientY:point.y});
 assert.match(nodes['correlation-cursor'].textContent,/20.000 °C.*50.00 % RH.*1000.00 hPa.*2.0000000 s.*6 paired/);
 assert.match(requests[0],/\/api\/history\/environment\?/);assert.match(requests[0],/session=s/);
 await chart.load(5,1005,'s',1);assert.equal(requests.length,1,'30-second throttle');
 const joint=response.segments[1].joint;
 joint.coefficients_available=false;joint.reason='These variables cannot be separated reliably.';
 joint.uncertainty={available:false,reason:joint.reason};for(const c of Object.values(joint.coefficients))c.slope_us_per_unit=null;
 chart.render();assert.equal(nodes['environment-temperature-adjusted'].textContent,'—');assert.equal(nodes['environment-temperature-adjusted-ci'].textContent,'Unavailable');
 assert.match(nodes['environment-model-state'].textContent,/cannot be separated/);
 assert.equal(nodes['environment-r2'].textContent,'0.900','Joint descriptive fit remains visible');
 chart.request=async()=>({...response,segments:[group('a',5),group('b',4)]});await chart.load(0,2000,'s',2);
 assert.equal(nodes['correlation-segment'].value,'s:a');
 nodes['correlation-segment'].onchange();chart.request=async()=>response;await chart.load(0,2000,'s',2,true);
 assert.equal(nodes['correlation-segment'].value,'s:a','Preserve explicit segment choice');
 // Sparse buckets must break both recorded and fitted lines.
 const sparse=group('a',4);sparse.points[2].bucket_start+=60;sparse.points[3].bucket_start+=60;
 chart.data={bucket_seconds:60,segments:[sparse]};strokes=[];chart.render();
 for(const color of ['#217c65','#be702c']){
  const trace=strokes.find(s=>s.color===color);assert.equal(trace.path.filter(p=>p[0]==='move').length,2);
 }
 chart.request=async()=>({...response,segments:[]});await chart.load(0,1000,'',3);
 assert.equal(nodes['environment-r2'].textContent,'—');assert.equal(nodes['environment-rms'].textContent,'—');
 assert.equal(nodes['environment-temperature-adjusted'].textContent,'—');
 assert(Object.values(chart.hits).every(points=>points.length===0));assert.match(nodes['correlation-state'].textContent,/No complete paired/);
 const pending=[];chart.request=(url,options)=>new Promise((resolve,reject)=>pending.push({resolve,reject,signal:options.signal}));
 const old=chart.load(0,1000,'',4);chart.invalidate();const fresh=chart.load(0,1000,'',5);assert(pending[0].signal.aborted);
 pending[1].resolve(response);await fresh;pending[0].resolve({...response,segments:[]});await old;
 assert.equal(nodes['environment-r2'].textContent,'0.900');
 const failed=chart.load(0,1000,'',6);pending[2].reject(new Error('History busy'));await failed;
 assert.equal(nodes['environment-r2'].textContent,'—');assert.match(nodes['correlation-state'].textContent,/unavailable.*History busy/);
 assert(Object.values(chart.hits).every(points=>points.length===0));assert.equal(nodes['environment-humidity-adjusted-ci'].textContent,'Unavailable');
 console.log('PASS: environmental plots, shared selection, gaps, uncertainty, errors and throttling.');
})().catch(error=>{console.error(error);process.exitCode=1;});
