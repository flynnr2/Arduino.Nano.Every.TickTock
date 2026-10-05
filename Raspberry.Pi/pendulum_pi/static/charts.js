'use strict';
/* Local, bounded canvas plots. Values are sampled display estimates, never re-smoothed. */
class ObservatoryHistory {
 constructor(request) {
  this.request=request; this.points=[]; this.sessions=[]; this.rangeSeconds=900;
  this.live=true; this.fixed=null; this.resetRange=null; this.cursor=null; this.busy=false;
  this.version=0; this.data=null; this.plots=[];
  this.el=id=>document.getElementById(id);
  this.environment=new EnvironmentalRelationships(request);
  document.querySelectorAll('[data-range]').forEach(button=>button.addEventListener('click',()=>{
   this.rangeSeconds=Number(button.dataset.range);this.live=true;this.fixed=null;this.resetRange=null;this.changed();
  }));
  this.el('custom-toggle').onclick=()=>{const open=this.el('history-custom').hidden;this.el('history-custom').hidden=!open;this.el('custom-toggle').setAttribute('aria-expanded',String(open));};
  this.el('history-custom').onsubmit=event=>{event.preventDefault();const start=new Date(this.el('history-start').value).getTime()/1000,end=new Date(this.el('history-end').value).getTime()/1000;if(!(end>start)){this.el('history-state').textContent='Choose an end after the start.';return;}this.live=false;this.fixed=[start,end];this.resetRange=[start,end];this.changed();};
  this.el('chart-metric').onchange=()=>this.render();
  this.el('chart-axis').onchange=()=>{if(this.el('chart-axis').value==='elapsed' && !this.el('history-session').value){const session=this.sessions[this.sessions.length-1];if(session)this.el('history-session').value=session.session;}this.changed();};
  this.el('history-session').onchange=()=>{if(!this.el('history-session').value)this.el('chart-axis').value='host';this.changed();};
  this.el('return-live').onclick=()=>{this.live=true;this.fixed=null;this.resetRange=null;this.changed();};
  this.el('reset-zoom').onclick=()=>{if(this.resetRange){this.live=false;this.fixed=[...this.resetRange];}else{this.live=true;this.fixed=null;}this.changed();};
  this.el('zoom-in').onclick=()=>{const [start,end]=this.bounds();this.zoom(start+(end-start)*.25,end-(end-start)*.25);};
  for(const id of ['timing-chart','temperature-chart','pressure-chart','humidity-chart'])this.bindPointer(this.el(id));
  this.resizeObserver=new ResizeObserver(()=>this.render());this.resizeObserver.observe(this.el('history-charts'));
  this.load();this.timer=setInterval(()=>{if(this.live)this.load();},10000);
 }
 summary(data){
  const sources=[...new Set(this.points.map(point=>point.source).filter(Boolean))];
  const reduction=data.budget_limited ? ' · point budget reached; some extrema may be omitted. Choose a narrower range to inspect more detail' : data.reduced ? ' · reduced with extrema retained' : '';
  return this.points.length ? `${this.points.length.toLocaleString()} retained display observations${reduction} · source: ${sources.join(', ') || 'unknown'}.` : `No retained observations in this range. Recording may be paused, history may be new, or older points may have expired.${data.budget_limited ? ' Point budget reached. Choose a narrower range.' : ''}`;
 }
 changed(){this.version++;this.cursor=null;this.environment.invalidate();this.load();}
 bounds(){const end=Date.now()/1000;return this.fixed || [end-this.rangeSeconds,end];}
 async load(){
  if(this.busy){this.reloadPending=true;return;}this.busy=true;const version=this.version;
  const [start,end]=this.bounds(),session=this.el('history-session').value;
  this.environment.load(start,end,session,this.version);
  const query=new URLSearchParams({start:String(start),end:String(end),max_points:'1200'});if(session)query.set('session',session);
  try{
   const data=await this.request(`/api/history?${query}`);if(version!==this.version)return;
   this.data=data;this.points=Array.isArray(data.points)?data.points:[];this.loadedBounds=[start,end];
   if(Array.isArray(data.sessions)){
    // Session choices are metadata, never inferred from or merged into traces.
    this.sessions=[...new Map([...this.sessions,...data.sessions].map(item=>[item.session,item])).values()].sort((a,b)=>a.start-b.start).slice(-200);
    const select=this.el('history-session');select.replaceChildren(new Option('All retained sessions',''));
    for(const item of this.sessions){const startLabel=Number.isFinite(item.start)?new Date(item.start*1000).toLocaleString():item.session;select.add(new Option(`${item.source || 'Unknown source'} · ${startLabel}`,item.session));}
    select.value=session;
    if(this.el('chart-axis').value==='elapsed' && !session && this.sessions.length){select.value=this.sessions[this.sessions.length-1].session;this.changed();return;}
   }
   this.el('history-state').textContent=this.summary(data);
   const boundaries=[...new Set(this.points.flatMap(point=>[...(point.boundary || []),point.gap_reason].filter(Boolean)))];
   this.el('history-boundaries').textContent=boundaries.length ? `Visible boundaries / gaps: ${boundaries.map(item=>item.replaceAll('_',' ')).join('; ')}.` : 'No boundary reported in this selection. Separate sessions, invalid readings and continuity segments are never joined.';
   this.render();
  }catch(error){if(version===this.version){this.el('history-state').textContent=`History unavailable: ${error.message}. ${this.data ? 'Showing the last successful history response.' : 'Live measurements remain independent.'}`;this.el('history-live').textContent='History unavailable';}}
  finally{this.busy=false;if(this.reloadPending){this.reloadPending=false;this.load();}}
 }
 zoom(start,end){if(!(end>start))return;if(!this.resetRange&&!this.live)this.resetRange=[...this.bounds()];this.fixed=[start,end];this.live=false;this.changed();}
 axisValue(point){return this.el('chart-axis').value==='elapsed' ? point.elapsed_seconds : point.time;}
 formatTime(value){if(this.el('chart-axis').value==='elapsed'){const sign=value<0?'-':'';const seconds=Math.abs(value);return `${sign}${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m ${Math.floor(seconds%60)}s`;}return new Date(value*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:this.domain&&this.domain[1]-this.domain[0]<3600?'2-digit':undefined});}
 timingValue(point,name,rate,hourly){
  const value=point[`${name}_${rate?'rate_s_day':'period_s'}`];
  return Number.isFinite(value)?value/(rate&&hourly?24:1):null;
 }
 joinable(previous,point,spec){
  const group=spec.continuity;
  const continuity=p=>group && p.continuity ? p.continuity[group] : p.segment;
  if(point.session!==previous.session || continuity(point)==null)return false;
  if(continuity(point)===continuity(previous))return true;
  // Older persisted observations have only a shared segment. Permit narrowly
  // identified diagnostic boundaries with matching measurement provenance.
  if(point.continuity || previous.continuity || !point.config_revision ||
    point.config_revision!==previous.config_revision || !point.recording_session ||
    point.recording_session!==previous.recording_session || point.source!==previous.source ||
    point.quality?.timebase!==previous.quality?.timebase)return false;
  const reasons=point.boundary || [];
  if(!reasons.length || !reasons.every(r=>['utc_quality_changed','observation_gap'].includes(r)))return false;
  const delay=point.monotonic-previous.monotonic;
  return Number.isFinite(delay) && delay>0 && delay<=30 &&
   ['drop_ir','drop_swing'].every(key=>Number.isFinite(point.capture?.[key]) && point.capture[key]===previous.capture?.[key]);
 }
 learning(point,name){return Boolean(point.quality?.[`${name}_learning`]);}
 render(){
  const elapsed=this.el('chart-axis').value==='elapsed',rate=this.el('chart-metric').value==='rate',hourly=this.el('rate-unit').value==='hour';
  this.el('history-live').textContent=this.live?'Live range':'Historical range';
  document.querySelectorAll('[data-range]').forEach(button=>button.setAttribute('aria-pressed',String(this.live && Number(button.dataset.range)===this.rangeSeconds)));
  this.el('timing-chart-title').textContent=rate?'Gain / loss':'Full period';this.el('timing-chart-unit').textContent=rate?(hourly?'seconds/hour':'seconds/day'):'seconds';
  this.el('history-provenance').textContent=`${elapsed?'Axis: elapsed monotonic observation time within the selected session.':'Axis: Pi observation time; cross-session ordering is uncertain when host UTC is unverified. Wall-clock corrections create boundaries.'} These are sampled display estimates, not exact Nano event UTC. Historical rates retain each point’s recorded target and estimator identity. Only recorded 600-second means are shown; older EWMA values are not converted. Calibration method changes create segment boundaries. Environmental association does not establish causation.`;
  const coordinates=this.points.map(point=>this.axisValue(point)).filter(Number.isFinite);
  this.domain=elapsed?(coordinates.length?[Math.min(...coordinates),Math.max(...coordinates)]:[0,this.rangeSeconds]):(this.loadedBounds || this.bounds());
  if(this.domain[0]===this.domain[1])this.domain=[this.domain[0]-.5,this.domain[1]+.5];
  const timingValue=(point,horizon)=>this.timingValue(point,horizon,rate,hourly);
  const specs=[
   {id:'timing-chart',continuity:'timing',zero:rate,flatPad:rate?(hourly?.1/24:.1):.000001,series:[{color:'#356fa0',name:'600-second mean',value:p=>timingValue(p,'window'),learning:p=>this.learning(p,'window') || p.quality?.timebase==='HOLDOVER'}]},
   {id:'temperature-chart',continuity:'sht4x',environment:true,minPad:.05,decimals:2,series:[{color:'#b86d3e',value:p=>p.temperature_C}]},
   {id:'pressure-chart',continuity:'bmp280',environment:true,minPad:.1,decimals:2,series:[{color:'#547e99',value:p=>p.pressure_hPa}]},
   {id:'humidity-chart',continuity:'sht4x',environment:true,minPad:.1,decimals:1,series:[{color:'#6a8462',value:p=>p.humidity_pct}]}
  ];
  this.plots=specs.map(spec=>this.draw(spec));
 }
 draw(spec){
  const canvas=this.el(spec.id),width=canvas.clientWidth||600,height=canvas.clientHeight||150,dpr=window.devicePixelRatio||1;
  canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
  const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.font='11px system-ui';
  const left=70,right=18,top=14,bottom=34,w=Math.max(1,width-left-right),h=height-top-bottom;
  const invalid=point=>spec.environment ? ['recording_disabled','storage_unavailable'].includes(point.gap_reason) : Boolean(point.gap_reason);
  const values=this.points.flatMap(point=>invalid(point)?[]:spec.series.map(series=>series.value(point))).filter(Number.isFinite);
  let low=values.length?Math.min(...values):0,high=values.length?Math.max(...values):1;
  if(spec.zero){low=Math.min(0,low);high=Math.max(0,high);}const pad=Math.max((high-low)*.12,spec.minPad || (high===low?spec.flatPad || .000001:.000001));low-=pad;high+=pad;
  const x=value=>left+(value-this.domain[0])/(this.domain[1]-this.domain[0])*w,y=value=>top+(high-value)/(high-low)*h;
  const decimals=spec.decimals ?? (high-low<.01?6:high-low<1?3:high-low<10?2:1);
  ctx.fillStyle='#708179';ctx.strokeStyle='#e4eae5';ctx.textAlign='right';
  for(let i=0;i<4;i++){const v=low+(high-low)*i/3,py=y(v);ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(width-right,py);ctx.stroke();ctx.fillText(v.toFixed(decimals),left-8,py+4);}
  if(spec.zero){ctx.strokeStyle='#a5b6ad';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(left,y(0));ctx.lineTo(width-right,y(0));ctx.stroke();ctx.setLineDash([]);}
  ctx.textAlign='center';const ticks=width<500?2:4;
  for(let i=0;i<=ticks;i++){const value=this.domain[0]+(this.domain[1]-this.domain[0])*i/ticks;ctx.textAlign=i===0?'left':i===ticks?'right':'center';ctx.fillText(this.formatTime(value),x(value),height-10);}
  ctx.save();ctx.beginPath();ctx.rect(left,top,w,h);ctx.clip();
  for(const series of spec.series){
   let previous=null;ctx.strokeStyle=series.color;ctx.fillStyle=series.color;ctx.lineWidth=1.8;
   for(const point of this.points){const value=invalid(point)?null:series.value(point),time=this.axisValue(point);
    if(!Number.isFinite(value)||!Number.isFinite(time)){previous=null;continue;}
    const px=x(time),py=y(value),learning=Boolean(series.learning?.(point));
    // A line is permitted only within an explicitly shared continuity segment.
    if(previous && this.joinable(previous.point,point,spec) && time>previous.time){ctx.setLineDash(learning||previous.learning?[5,4]:[]);ctx.beginPath();ctx.moveTo(previous.x,previous.y);ctx.lineTo(px,py);ctx.stroke();}
    else{ctx.beginPath();ctx.arc(px,py,2,0,Math.PI*2);ctx.fill();}
    previous={x:px,y:py,time,point,learning};
   }
  }
  ctx.setLineDash([]);
  if(Number.isFinite(this.cursor)){ctx.strokeStyle='#344f45';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x(this.cursor),top);ctx.lineTo(x(this.cursor),height-bottom);ctx.stroke();}
  if(this.drag){ctx.fillStyle='rgba(35,118,99,.12)';const x1=x(this.drag.start),x2=x(this.drag.end);ctx.fillRect(Math.min(x1,x2),top,Math.abs(x2-x1),h);}
  ctx.restore();
  if(!values.length){ctx.fillStyle='#708179';ctx.textAlign='center';ctx.fillText(spec.zero?'No rate values — a recorded target is required':'No available readings in this range',left+w/2,top+h/2);}
  return {canvas,left,w,x};
 }
 position(canvas,event){const rect=canvas.getBoundingClientRect(),plot=this.plots.find(item=>item.canvas===canvas);if(!plot)return null;const fraction=Math.max(0,Math.min(1,(event.clientX-rect.left-plot.left)/plot.w));return this.domain[0]+fraction*(this.domain[1]-this.domain[0]);}
 bindPointer(canvas){
  canvas.onpointerdown=event=>{if(event.button!==0)return;const value=this.position(canvas,event);if(value===null)return;this.drag={start:value,end:value};canvas.setPointerCapture(event.pointerId);};
  canvas.onpointermove=event=>{const value=this.position(canvas,event);if(value===null)return;this.cursor=value;if(this.drag)this.drag.end=value;this.inspect(value);this.render();};
  canvas.onpointerleave=()=>{if(!this.drag){this.cursor=null;this.render();}};
  canvas.onpointercancel=()=>{this.drag=null;this.render();};
  canvas.onpointerup=event=>{
   if(!this.drag)return;let start=Math.min(this.drag.start,this.drag.end),end=Math.max(this.drag.start,this.drag.end);this.drag=null;
   if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
   if(end-start<(this.domain[1]-this.domain[0])*.01){this.render();return;}
   if(this.el('chart-axis').value==='elapsed'){const points=this.points.filter(point=>Number.isFinite(point.elapsed_seconds));if(!points.length)return;const nearest=value=>points.reduce((a,b)=>Math.abs(a.elapsed_seconds-value)<Math.abs(b.elapsed_seconds-value)?a:b);start=nearest(start).time;end=nearest(end).time;}
   if(!(end>start)){this.el('history-state').textContent='This selection crosses a host clock correction. Choose a range on one side of the boundary.';this.render();return;}this.zoom(start,end);
  };
 }
 inspect(value){
  const points=this.points.filter(point=>Number.isFinite(this.axisValue(point)));if(!points.length){this.el('chart-cursor').textContent='No observations in this selection.';return;}
  const point=points.reduce((a,b)=>Math.abs(this.axisValue(a)-value)<Math.abs(this.axisValue(b)-value)?a:b);
  const nearest=this.axisValue(point),rate=this.el('chart-metric').value==='rate',hourly=this.el('rate-unit').value==='hour';
  const fmt=(v,digits=3)=>Number.isFinite(v)?v.toFixed(digits):'—';
  const timing=name=>`${fmt(this.timingValue(point,name,rate,hourly),rate?3:6)} ${rate?(hourly?'s/hour':'s/day'):'s'}${this.learning(point,name)?' (learning)':''}`;
  this.el('chart-cursor').textContent=`Nearest observation: ${this.formatTime(nearest)} · ${point.source || 'unknown source'} · ${point.gap_reason?`Gap: ${point.gap_reason.replaceAll('_',' ')}`:`600-second mean ${timing('window')} · ${fmt(point.temperature_C,2)} °C · ${fmt(point.pressure_hPa,2)} hPa · ${fmt(point.humidity_pct,1)} % RH`} · estimator ${point.settings?.estimator_model || 'legacy mean / unspecified'} · target ${fmt(point.settings?.target_period_s,6)} s · ${point.quality?.timebase || 'unknown timebase'}${Number.isFinite(point.quality?.calibration_age_seconds)?` · calibration ${fmt(point.quality.calibration_age_seconds,1)} s old`:''}`;
 }
}
