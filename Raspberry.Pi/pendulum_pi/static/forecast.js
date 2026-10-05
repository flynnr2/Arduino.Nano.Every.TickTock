'use strict';
/* Frozen causal predictions from status; retrospective phase bins stay separate. */
class SwingForecast {
 constructor(){
  this.el=id=>document.getElementById(id);this.points=[];this.identity=null;this.lastKey=null;this.lastSeq=null;this.gap=true;
  this.resizeObserver=new ResizeObserver(()=>this.draw());this.resizeObserver.observe(this.el('forecast-chart'));
 }
 update(forecast,stale,revision){
  const f=forecast || {}, identity=JSON.stringify([revision,f.revision,f.model,f.cycle_length]);
  if(identity!==this.identity){this.points=[];this.lastKey=null;this.lastSeq=null;this.gap=true;this.identity=identity;}
  const disabled=!f.enabled, unavailable=stale || f.stale || !f.available;
  this.el('forecast-state').textContent=disabled ? 'Disabled' : stale || f.stale ? 'Stale' : !f.available ? 'Waiting' : f.learning ? 'Learning' : 'Tracking';
  const next=!disabled && !unavailable ? f.next : null,last=!disabled && !unavailable ? f.last : null;
  const fmt=(value,digits,suffix='')=>Number.isFinite(value)?value.toFixed(digits)+suffix:'—';
  this.el('forecast-next').textContent=fmt(next?.period_us/1e6,6,' s');
  this.el('forecast-observed').textContent=fmt(last?.observed_period_us/1e6,6,' s');
  this.el('forecast-error').textContent=fmt(last?.error_us,2,' µs');
  this.el('forecast-rmse').textContent=disabled || unavailable ? '—' : `${fmt(f.recent?.rmse_us,2)} / ${fmt(f.recent?.baseline_rmse_us,2)} µs`;
  this.el('forecast-details').textContent=disabled ? 'Prediction disabled. Set a cycle length: 1 for a uniform swing, or the measured repeating pattern (15 for this Synchronome).' : unavailable ? 'Waiting for fresh, causally qualified swing predictions.' : `Cycle length: ${f.cycle_length} · next sequence ${next?.target_seq ?? '—'} predicted after sequence ${next?.origin_seq ?? '—'} · ${next?.timebase || 'timebase unavailable'} · recent service scores: ${f.recent?.count ?? 0} swings. Pattern deviations leave the rate estimate unchanged.`;
  if(Array.isArray(f.results)){
   // The acquisition service retains every scored swing, so missed browser
   // polls and page reloads can backfill without inventing predictions.
   if(!disabled){
    const results=f.results.filter(row=>Number.isFinite(row.error_us) && Number.isFinite(row.baseline_error_us)).slice(-120);
    this.points=results.map((row,i)=>({seq:row.target_seq,error:row.error_us,baseline:row.baseline_error_us,
     elapsed:row.elapsed_seconds,epoch:row.observed_epoch,
     join:i>0 && row.origin_seq===results[i-1].target_seq &&
      (!Number.isFinite(row.observed_epoch) || !Number.isFinite(results[i-1].observed_epoch) || row.observed_epoch>=results[i-1].observed_epoch)}));
   }
  }else{
   // Compatibility with an older service while dashboard assets are updated.
   if(disabled || unavailable)this.gap=true;
   const key=last?`${last.origin_seq}:${last.target_seq}`:null;
   if(key && key!==this.lastKey && Number.isFinite(last.error_us) && Number.isFinite(last.baseline_error_us)){
    this.points.push({seq:last.target_seq,error:last.error_us,baseline:last.baseline_error_us,join:!this.gap && this.lastSeq!==null && last.origin_seq===this.lastSeq});
    if(this.points.length>120)this.points.shift();
    this.lastKey=key;this.lastSeq=last.target_seq;this.gap=false;
   }
  }
  this.draw();
 }
 draw(){
  const canvas=this.el('forecast-chart'),width=canvas.clientWidth || 600,height=canvas.clientHeight || 180,dpr=window.devicePixelRatio || 1;
  canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
  const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);
  ctx.font='11px system-ui';ctx.fillStyle='#6b7c73';ctx.fillText('Prediction error (green) · mean-only error (grey) · µs',12,16);
  if(!this.points.length){ctx.fillText('No scored predictions retained yet.',12,45);return;}
  const extent=Math.max(1,...this.points.flatMap(p=>[Math.abs(p.error),Math.abs(p.baseline)]));
  const first=this.points[0].elapsed,last=this.points.at(-1).elapsed;
  const timed=Number.isFinite(first) && Number.isFinite(last) && last>first;
  const y=value=>30+(height-70)*(1-value/extent)/2;
  const x=i=>48+(timed ? (this.points[i].elapsed-first)/(last-first) : i/Math.max(1,this.points.length-1))*(width-62);
  ctx.textAlign='left';
  for(const index of [...new Set([0,Math.floor((this.points.length-1)/2),this.points.length-1])]){
   const point=this.points[index];
   const label=Number.isFinite(point.epoch) ? new Date(point.epoch*1000).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : `Swing ${point.seq}`;
   ctx.textAlign=index===0?'left':index===this.points.length-1?'right':'center';
   ctx.fillText(label,x(index),height-8);
  }
  ctx.textAlign='left';
  ctx.strokeStyle='#d8ded8';ctx.beginPath();ctx.moveTo(48,y(0));ctx.lineTo(width-14,y(0));ctx.stroke();
  ctx.fillText('0',10,y(0)+3);ctx.fillText(extent.toFixed(0),10,34);ctx.fillText((-extent).toFixed(0),10,height-40);
  for(const [field,color] of [['baseline','#9aa39d'],['error','#237663']]){
   ctx.strokeStyle=color;ctx.fillStyle=color;ctx.beginPath();
   this.points.forEach((p,i)=>{if(i && p.join)ctx.lineTo(x(i),y(p[field]));else ctx.moveTo(x(i),y(p[field]));});ctx.stroke();
   this.points.forEach((p,i)=>{ctx.beginPath();ctx.arc(x(i),y(p[field]),2,0,2*Math.PI);ctx.fill();});
  }
 }
}
