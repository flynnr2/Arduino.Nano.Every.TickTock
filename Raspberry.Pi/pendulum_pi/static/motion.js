'use strict';
class PendulumMotion {
 constructor(){
  this.el=id=>document.getElementById(id);this.motion=null;this.stale=false;
  this.el('motion-cycle-metric').onchange=()=>this.draw();
  if(typeof ResizeObserver!=='undefined'){this.resizeObserver=new ResizeObserver(()=>this.draw());this.resizeObserver.observe(this.el('motion-cycle-chart'));}
 }
 format(value,digits,unit){return Number.isFinite(value)?`${value.toFixed(digits)}${unit}`:'—';}
 update(motion,stale,timebase){
  this.motion=motion;this.stale=Boolean(stale || motion?.stale);
  const ready=Boolean(motion?.available && !this.stale);
  this.el('motion-state').textContent=this.stale?'Stale':!ready?'Waiting':`${timebase==='HOLDOVER'?'PPS holdover':'PPS calibrated'} · estimated`;
  for(const side of ['tick','tock'])for(const [metric,key,digits,unit] of [['velocity','velocity_mm_s',1,' mm/s'],['amplitude','amplitude_deg',2,'°']]){
   this.el(`motion-${side}-${metric}`).textContent=this.stale?'Stale':this.format(ready?motion.latest?.[`${side}_${key}`]:null,digits,unit);
   this.el(`motion-${side}-mean-${metric}`).textContent=this.stale?'Stale':this.format(ready?motion.mean?.[`${side}_${key}`]:null,digits,unit);
  }
  this.el('motion-mean-note').textContent=ready?`Means of individual crossing estimates · ${motion.count}/300 fresh swings${motion.learning?' · filling window':''}. The history charts below retain these means.`:'Waiting for fresh calibrated crossings.';
  this.draw();
 }
 draw(){
  const svg=this.el('motion-cycle-chart');
  const width=svg.clientWidth || 900,left=width<500?64:76,right=width-18;
  const cycle=this.motion?.cycle,points=cycle?.points || [],metric=this.el('motion-cycle-metric').value;
  // Live values arrive every second; keep focused points while their cycle
  // values remain unchanged instead of replacing the SVG on every poll.
  const key=JSON.stringify([width,metric,this.stale,this.motion?.available,cycle]);
  if(this.drawKey===key)return;
  this.drawKey=key;svg.replaceChildren();svg.setAttribute('viewBox',`0 0 ${width} 280`);
  const ready=Boolean(!this.stale && this.motion?.available && cycle?.complete_cycles);
  const note=this.el('motion-cycle-note');
  note.textContent=this.stale?'Stale — waiting for fresh motion estimates.':!ready?'Waiting for a complete 15-swing cycle.':`${cycle.complete_cycles} complete cycles · median of each phase · sequence phase zero${Number.isInteger(cycle.candidate_phase)?` · candidate impulse feature at phase ${cycle.candidate_phase}`:' · impulse candidate needs at least three cycles and a positive speed increase'}.`;
  this.el('motion-cycle-cursor').textContent=ready?'Hover, tap or focus a point to inspect its phase and median.':'Cycle values appear after one complete sequence cycle.';
  const add=(tag,attrs,text,parent=svg)=>{const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value]of Object.entries(attrs))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;parent.append(node);return node;};
  if(!ready){add('text',{x:width/2,y:125,'text-anchor':'middle',class:'phase-label'},this.stale?'Stale motion estimates':'Waiting for complete cycles');return;}
  const valid=points.map(p=>p[metric]).filter(Number.isFinite);if(!valid.length)return;
  let low=Math.min(...valid),high=Math.max(...valid),pad=Math.max((high-low)*.15,metric==='velocity_mm_s'?.01:.0001);low-=pad;high+=pad;
  const x=phase=>left+phase/29*(right-left),y=value=>220-(value-low)/(high-low)*190;
  for(let i=0;i<4;i++){const value=low+(high-low)*i/3;add('line',{x1:left,x2:right,y1:y(value),y2:y(value),stroke:'#e4eae5'});add('text',{x:left-10,y:y(value)+4,'text-anchor':'end',class:'phase-axis'},value.toFixed(metric==='velocity_mm_s'?2:4));}
  for(const phase of [0,5,10,15,20,25,29])add('text',{x:x(phase),y:244,'text-anchor':'middle',class:'phase-axis'},String(phase));
  add('text',{x:(left+right)/2,y:270,'text-anchor':'middle',class:'phase-label'},width<500?'Half phase / nominal seconds':'Half phase / nominal seconds within the 30-second cycle');
  add('text',{x:left,y:17,class:'phase-label'},metric==='velocity_mm_s'?'Passage speed (mm/s)':'Estimated semi-amplitude (°)');
  const candidate=cycle.candidate_phase;
  if(Number.isInteger(candidate))add('line',{x1:x(candidate),x2:x(candidate),y1:25,y2:220,stroke:'#8d6c4f','stroke-dasharray':'5 4'});
  for(const [side,color]of [['tick','#497eae'],['tock','#bb7541']]){
   const selected=points.filter(p=>p.side===side && Number.isFinite(p[metric]));
   add('polyline',{points:selected.map(p=>`${x(p.phase)},${y(p[metric])}`).join(' '),fill:'none',stroke:color,'stroke-width':2});
   for(const point of selected){
    const message=`${side==='tick'?'Tick':'Tock'} · phase ${point.phase} · ${this.format(point[metric],metric==='velocity_mm_s'?2:4,metric==='velocity_mm_s'?' mm/s':'°')} · ${point.count} complete cycles${point.phase===candidate?' · candidate impulse feature':''}`;
    const dot=add('circle',{cx:x(point.phase),cy:y(point[metric]),r:width<500?3.5:5,fill:color,tabindex:0,role:'img','aria-label':message});add('title',{},message,dot);
    for(const event of ['pointerenter','focus','click'])dot.addEventListener(event,()=>{this.el('motion-cycle-cursor').textContent=message;});
   }
  }
 }
}
