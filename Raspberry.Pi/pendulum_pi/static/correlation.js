'use strict';
/* Same complete paired averages for every individual and combined fit. */
class EnvironmentalRelationships {
 constructor(request) {
  this.request=request;this.el=id=>document.getElementById(id);
  this.variables=[['temperature','temperature_C','Temperature (°C)','µs/°C',2],['humidity','humidity_pct','Humidity (% RH)','µs/% RH',2],['pressure','pressure_hPa','Pressure (hPa)','µs/hPa',2]];
  this.data=null;this.selection=null;this.preferredSegment=null;this.generation=0;this.lastRequest=-Infinity;this.hits={};
  this.message='Waiting for the selected history range.';
  this.el('correlation-segment').onchange=()=>{this.preferredSegment=this.el('correlation-segment').value;this.render();};
  for(const id of [...this.variables.map(v=>`environment-${v[0]}-chart`),'environment-fit-chart','environment-residual-chart']){
   this.el(id).onpointermove=event=>this.inspect(id,event);
   this.el(id).onpointerleave=()=>{this.el('correlation-cursor').textContent='Move over a point to inspect its paired average.';};
  }
  this.resizeObserver=new ResizeObserver(()=>this.render());this.resizeObserver.observe(this.el('environmental-relationships'));this.render();
 }
 invalidate(){this.controller?.abort();this.generation++;this.data=null;this.lastRevision=null;this.message='Loading the selected range…';this.render();}
 async load(start,end,session,revision,force=false){
  this.selection=[start,end,session,revision];
  if(!force && revision===this.lastRevision && Date.now()-this.lastRequest<30000)return;
  this.lastRequest=Date.now();this.lastRevision=revision;
  this.controller?.abort();const controller=new AbortController();this.controller=controller;const generation=++this.generation;
  const query=new URLSearchParams({start:String(start),end:String(end)});if(session)query.set('session',session);
  const timeout=setTimeout(()=>controller.abort(),7000);
  try{
   const data=await this.request(`/api/history/environment?${query}`,{signal:controller.signal});if(generation!==this.generation)return;
   if(data.state==='updating' && !(data.segments||[]).length){this.message=data.message;this.lastRequest=0;this.render();setTimeout(()=>{if(generation===this.generation && this.selection)this.load(...this.selection,true);},2000);return;}
   this.data=data;const select=this.el('correlation-segment');select.replaceChildren();const segments=data.segments||[];
   for(const group of segments)select.add(new Option(`${this.time(group.start)} – ${this.time(group.end)} · ${group.source} · ${group.points.length} averages`,this.key(group)));
   if(!segments.length)select.add(new Option('No complete paired tracking observations',''));select.disabled=segments.length<2;
   const chosen=segments.find(group=>this.key(group)===this.preferredSegment)||[...segments].sort((a,b)=>b.points.length-a.points.length||b.end-a.end)[0];
   select.value=chosen?this.key(chosen):'';
   this.message=data.message || 'No complete paired tracking observations in this range. All three fresh environmental readings need 600 seconds of continuous history and period learning must be complete.';this.render();
  }catch(error){if(generation!==this.generation)return;this.data=null;this.message=`Environmental comparison unavailable: ${error.name==='AbortError'?'request timed out; try a shorter range':error.message}.`;this.render();}
  finally{clearTimeout(timeout);}
 }
 key(group){return `${group.session}:${group.segment}`;}
 time(value){return new Date(value*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
 selected(){return this.data?.segments?.find(group=>this.key(group)===this.el('correlation-segment').value);}
 fmt(value,digits=3){return Number.isFinite(value)?value.toFixed(digits):'—';}
 precise(value){return Number.isFinite(value)?value.toPrecision(3):'—';}
 slope(value,unit){return Number.isFinite(value)?`${value>0?'+':''}${this.precise(value)} ${unit}`:'—';}
 interval(values,unit){return values?.length===2&&values.every(Number.isFinite)?`${this.precise(values[0])} to ${this.precise(values[1])} ${unit}`:'Unavailable';}
 render(){
  const group=this.selected(),points=group?.points||[],joint=group?.joint,ready=joint?.uncertainty?.available;
  const mean=points.length?points.reduce((sum,p)=>sum+p.period_s,0)/points.length:0;
  this.el('environment-period-reference').textContent=group?`Plots show period offsets in microseconds from this segment's selected mean (${mean.toFixed(7)} s). Colours follow time; dashed scatter lines show the individual fits.`:'';
  for(const [name,key,label,unit,digits] of this.variables){
   const fit=group?.individual?.[key],span=group?.spans?.[key],uncertainty=fit?.uncertainty,coefficient=joint?.coefficients?.[key];
   this.el(`environment-${name}-slope`).textContent=this.slope(fit?.slope_us_per_unit,unit);
   this.el(`environment-${name}-summary`).textContent=group?`R² ${this.fmt(fit?.r_squared)} · span ${span?`${this.fmt(span[0],digits)}–${this.fmt(span[1],digits)} ${label.match(/\((.*)\)/)[1]}`:'—'}${fit?.available?'':` · ${fit?.reason||'Fit unavailable'}`}`:'Waiting for paired averages.';
   this.el(`environment-${name}-ci`).textContent=uncertainty?.available?`95% interval: ${this.interval(uncertainty.ci95_us_per_unit,unit)}`:uncertainty?.reason||'95% interval unavailable';
   this.el(`environment-${name}-adjusted`).textContent=this.slope(coefficient?.slope_us_per_unit,unit);
   this.el(`environment-${name}-adjusted-ci`).textContent=ready?this.interval(coefficient?.ci95_us_per_unit,unit):'Unavailable';
   this.el(`environment-${name}-se`).textContent=ready?`${this.precise(coefficient?.se_us_per_unit)} ${unit}`:'—';
   this.draw(`environment-${name}-chart`,points,{x:p=>p[key],xlabel:label,ylabel:'Period offset (µs)',mean,
    line:fit?.available?value=>((fit.mean_period_s-mean)*1e6+fit.slope_us_per_unit*(value-fit.mean_x)):null});
  }
  this.el('environment-r2').textContent=this.fmt(joint?.r_squared);
  this.el('environment-rms').textContent=Number.isFinite(joint?.residual_rms_us)?`${this.precise(joint.residual_rms_us)} µs`:'—';
  this.el('environment-improvement').textContent=Number.isFinite(joint?.residual_reduction_pct)?`${this.fmt(joint.residual_reduction_pct,1)}%`:'—';
  this.el('correlation-count').textContent=String(points.length);
  this.el('environment-model-state').textContent=joint?.available?(joint.reason||'Combined fit available. Adjusted slopes include all three variables.'):`${joint?.reason||'Waiting for a combined fit.'}`;
  this.el('environment-adjusted-r2').textContent=this.fmt(joint?.adjusted_r_squared);
  this.el('environment-condition').textContent=joint?.available?`${Number.isFinite(joint.condition_number)?this.precise(joint.condition_number):'Rank deficient'} · rank ${joint.predictor_rank} of 3 · display limit ${joint.condition_limit}`:'—';
  const uncertainty=joint?.uncertainty;
  this.el('correlation-window').textContent=Number.isFinite(uncertainty?.window_seconds)?`${this.fmt(uncertainty.window_seconds/60,1)} minutes · ${uncertainty.lag_buckets} bucket lags`:'—';
  this.el('correlation-minimum').textContent=Number.isFinite(uncertainty?.minimum_points)?`${uncertainty.minimum_points} consecutive paired averages`:'—';
  this.el('correlation-uncertainty-state').textContent=ready?'Approximate uncertainty accounts for smoothing and time dependence using Newey–West.':uncertainty?.reason||joint?.reason||'Waiting for uncertainty estimates.';
  this.el('correlation-earlier').textContent=group?this.time(group.start):'Earlier';this.el('correlation-later').textContent=group?this.time(group.end):'Later';
  this.el('correlation-cursor').textContent='Move over a point to inspect its paired average.';
  const segments=this.data?.segments?.length||0;
  this.el('correlation-state').textContent=group?`${points.length} complete paired ${this.data.bucket_seconds/60}-minute averages from ${group.samples.toLocaleString()} retained observations. ${segments>1?`${segments} separate segments available; showing the selected segment only.`:'One continuous measurement segment.'}`:this.message;
  this.el('correlation-details').textContent=group?`${group.source} · ${group.timebase==='PPS'?'PPS-corrected timebase':group.timebase==='NOMINAL'?'Nominal timebase — PPS correction unavailable':group.timebase||'Unknown timebase'} · 600-second mean · estimator ${group.settings?.estimator_model||'unspecified'}.`:'';
  this.draw('environment-fit-chart',joint?.available?points:[],{x:p=>p.time,xlabel:'Observation time',ylabel:'Period offset (µs)',mean,timeline:true,fitted:true});
  this.draw('environment-residual-chart',joint?.available?points:[],{x:p=>p.time,y:p=>p.residual_us,xlabel:'Observation time',ylabel:'Residual (µs)',mean,timeline:true,zero:true});
 }
 draw(id,points,options){
  const canvas=this.el(id),width=canvas.clientWidth||600,height=canvas.clientHeight||280,dpr=window.devicePixelRatio||1;
  canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.font='11px system-ui';this.hits[id]=[];
  const left=65,right=16,top=26,bottom=48,w=Math.max(1,width-left-right),h=height-top-bottom;
  ctx.fillStyle='#6b7c73';ctx.textAlign='left';ctx.fillText(options.ylabel,left,13);
  if(!points.length){ctx.textAlign='center';ctx.fillText('No paired averages to plot',left+w/2,top+h/2);return;}
  const yvalue=options.y||(p=>(p.period_s-options.mean)*1e6),xs=points.map(options.x),ys=points.map(yvalue);
  if(options.fitted)ys.push(...points.map(p=>(p.fitted_period_s-options.mean)*1e6));if(options.zero)ys.push(0);
  const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
  const xpad=Math.max((xmax-xmin)*.06,options.timeline?1:.005),ypad=Math.max((ymax-ymin)*.12,.1);
  const xlow=xmin-xpad,xhigh=xmax+xpad,ylow=ymin-ypad,yhigh=ymax+ypad;
  const x=v=>left+(v-xlow)/(xhigh-xlow)*w,y=v=>top+(yhigh-v)/(yhigh-ylow)*h;
  ctx.strokeStyle='#e4eae5';ctx.textAlign='right';
  for(let i=0;i<4;i++){const value=ylow+(yhigh-ylow)*i/3,py=y(value);ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(width-right,py);ctx.stroke();ctx.fillText(value.toFixed(2),left-8,py+4);}
  const ticks=options.timeline||width<500?2:4;
  for(let i=0;i<=ticks;i++){const value=xlow+(xhigh-xlow)*i/ticks;ctx.textAlign=i===0?'left':i===ticks?'right':'center';ctx.fillText(options.timeline?this.time(value):value.toFixed(2),x(value),height-28);}
  ctx.textAlign='center';ctx.fillText(options.xlabel,left+w/2,height-8);ctx.save();ctx.beginPath();ctx.rect(left,top,w,h);ctx.clip();
  if(options.line){ctx.strokeStyle='#344f45';ctx.setLineDash([6,4]);ctx.beginPath();ctx.moveTo(x(xmin),y(options.line(xmin)));ctx.lineTo(x(xmax),y(options.line(xmax)));ctx.stroke();ctx.setLineDash([]);}
  if(options.zero){ctx.strokeStyle='#9baaa2';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(left,y(0));ctx.lineTo(width-right,y(0));ctx.stroke();ctx.setLineDash([]);}
  if(options.timeline){
   const connect=(value,color,dash)=>{ctx.strokeStyle=color;ctx.setLineDash(dash);ctx.beginPath();let previous=null;
    for(const point of points){const px=x(options.x(point)),py=y(value(point));if(previous&&Math.abs(point.bucket_start-previous.bucket_start-this.data.bucket_seconds)<1e-6)ctx.lineTo(px,py);else ctx.moveTo(px,py);previous=point;}ctx.stroke();ctx.setLineDash([]);};
   connect(yvalue,'#217c65',[]);if(options.fitted)connect(p=>(p.fitted_period_s-options.mean)*1e6,'#be702c',[6,4]);
  }
  const firstTime=points[0].time,lastTime=points.at(-1).time;
  for(const point of points){const fraction=lastTime>firstTime?(point.time-firstTime)/(lastTime-firstTime):.5,px=x(options.x(point)),py=y(yvalue(point));
   ctx.fillStyle=options.timeline?'#217c65':`hsl(${220-195*fraction}, 60%, 44%)`;ctx.beginPath();ctx.arc(px,py,3,0,Math.PI*2);ctx.fill();this.hits[id].push({x:px,y:py,point});}
  if(options.fitted){ctx.fillStyle='#be702c';for(const point of points){ctx.beginPath();ctx.arc(x(point.time),y((point.fitted_period_s-options.mean)*1e6),2.5,0,Math.PI*2);ctx.fill();}}
  ctx.restore();
 }
 inspect(id,event){const rect=this.el(id).getBoundingClientRect(),distance=p=>Math.hypot(p.x-(event.clientX-rect.left),p.y-(event.clientY-rect.top));
  const nearest=this.hits[id].reduce((a,b)=>!a||distance(b)<distance(a)?b:a,null);if(!nearest||distance(nearest)>25)return;const p=nearest.point;
  this.el('correlation-cursor').textContent=`${this.time(p.time)} · ${p.temperature_C.toFixed(3)} °C · ${p.humidity_pct.toFixed(2)} % RH · ${p.pressure_hPa.toFixed(2)} hPa · ${p.period_s.toFixed(7)} s${Number.isFinite(p.residual_us)?` · residual ${this.precise(p.residual_us)} µs`:''} · ${p.samples} paired observations`;
 }
}
