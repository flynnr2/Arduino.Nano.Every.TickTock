'use strict';
/* Small SVG phase summaries; all metrology is calculated by the analysis suite. */
class ObservatoryPhase {
 constructor(request) {
  this.request=request;this.el=id=>document.getElementById(id);this.busy=false;
  this.el('phase-refresh').onclick=()=>this.load();
  this.load();
 }
 async load() {
  if(this.busy)return;this.busy=true;clearTimeout(this.timer);
  let delay=60000;
  try {
   const data=await this.request('/api/phase');this.render(data);
   if(data.updating)delay=2000;
  } catch(error) {
   this.el('phase-state').textContent=`Phase view unavailable: ${error.message}. Any charts below are the previous snapshot.`;
   this.el('phase-badge').textContent='Unavailable';
  } finally {this.busy=false;this.timer=setTimeout(()=>this.load(),delay);}
 }
 node(tag,attributes={},text=null) {
  const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attributes))node.setAttribute(key,String(value));
  if(text!==null)node.textContent=text;
  return node;
 }
 format(value,digits=3) {return Number.isFinite(value)?value.toFixed(digits):'—';}
 describe(point,half) {
  const side=half?` · ${point.phase%2?'Tock':'Tick'}`:'';
  return `Phase ${point.phase}${side} · median ${this.format(point.median_s,9)} s · ${Number.isFinite(point.deviation_us)&&point.deviation_us>=0?'+':''}${this.format(point.deviation_us)} µs from reference · ${point.count} eligible, ${point.excluded} excluded`;
 }
 render(data) {
  this.el('phase-badge').textContent=data.updating?'Updating':data.stale?'Previous snapshot':data.state==='unavailable'?'Unavailable':data.live?'Recent recording':'Recording inactive';
  const generated=Number.isFinite(data.generated_at)?new Date(data.generated_at*1000).toLocaleTimeString():null;
  const info=generated?`${data.eligible || 0} eligible of ${data.records || 0} swings · ${this.format((data.observed_span_seconds || 0)/60,1)} minutes between first and last capture · updated ${generated}. `:'';
  this.el('phase-state').textContent=info+(data.message || (data.updating?'Calculating the latest snapshot…':'Recent recorded swings; these charts have their own selection, independent of the history range above.'));
  this.el('phase-coverage').textContent=data.segment&&generated?`Source: ${data.source} · ${data.segment} · capture epoch ${data.epoch ?? '—'} · sequences ${data.first_seq ?? '—'}–${data.last_seq ?? '—'}. Up to ${data.swing_limit} recent full swings from this segment; ${data.limited?'older swings are outside this window':'all available swings fit within the window'}. ${data.live?'':'Recording is inactive or stale; this is a recorded snapshot. '}${data.stale?'The last calculation failed; the displayed snapshot has not been refreshed.':''}`:data.segment?`Source: ${data.source} · ${data.segment}. Recording detected; phase statistics are not available yet. ${data.live?'Capture is active.':'Capture is inactive or stale.'}`:'Waiting for a recording. No smoothed display estimates are used.';
  this.el('phase-cursor').textContent='Hover, tap or focus a bar to inspect its median and sample count.';
  for(const metric of ['full','half']) {
   const chart=(data.charts || []).find(item=>item.metric===metric);
   const emptyMessage=data.state==='unavailable'||data.stale?'Phase analysis unavailable':data.updating&&!data.generated_at?'Calculating phase medians…':'Waiting for valid PPS coverage';
   this.draw(metric,chart,emptyMessage);
  }
 }
 draw(metric,chart,emptyMessage='Waiting for valid PPS coverage') {
  const half=metric==='half',svg=this.el(`phase-${metric}`),reference=this.el(`phase-${metric}-reference`),table=this.el(`phase-${metric}-rows`);
  svg.replaceChildren();table.replaceChildren();
  const points=chart?.points || [],available=points.filter(p=>p.count>0 && Number.isFinite(p.deviation_us));
  reference.textContent=chart? (half?`Tick reference ${this.format(chart.references_s[0],9)} s · tock reference ${this.format(chart.references_s[1],9)} s`:`Full-swing reference ${this.format(chart.references_s[0],9)} s`):emptyMessage==='Phase analysis unavailable'?'Analysis unavailable; see the status above.':'Waiting for PPS-calibrated measurements.';
  for(const p of points) {
   const row=document.createElement('tr');
   for(const value of [p.phase,half?(p.phase%2?'Tock':'Tick'):'Full',this.format(p.median_s,9),this.format(p.deviation_us),p.count,p.excluded]) {
    const cell=document.createElement('td');cell.textContent=value;row.append(cell);
   }
   table.append(row);
  }
  if(!available.length) {
   svg.append(this.node('circle',{cx:210,cy:185,r:110,fill:'none',stroke:'#d8e2db','stroke-dasharray':'4 6'}),this.node('text',{x:210,y:183,'text-anchor':'middle',class:'phase-empty'},emptyMessage),this.node('text',{x:210,y:205,'text-anchor':'middle',class:'phase-axis'},emptyMessage==='Waiting for valid PPS coverage'?'Empty bins are never shown as zero':'See analysis status above'));
   return;
  }
  const maximum=Math.max(.001,...available.map(p=>Math.abs(p.deviation_us)));
  const power=10**Math.floor(Math.log10(maximum));
  const scale=[1,2,5,10].find(n=>n*power>=maximum)*power;
  const cx=210,cy=185,zero=91,spread=62;
  const position=(radius,angle)=>[cx+radius*Math.sin(angle),cy-radius*Math.cos(angle)];
  const radius=value=>zero+value/scale*spread;
  for(const value of [-scale,0,scale]) {
   svg.append(this.node('circle',{cx,cy,r:radius(value),fill:'none',stroke:value===0?'#879b8f':'#e3e9e2','stroke-width':value===0?1.5:1}));
  }
  const cursor=this.el('phase-cursor');
  for(const p of points) {
   const angle=p.phase*2*Math.PI/chart.bins;
   const [lx,ly]=position(173,angle);
   svg.append(this.node('text',{x:lx,y:ly+4,'text-anchor':'middle',class:'phase-label'},p.phase));
   const finite=p.count>0 && Number.isFinite(p.deviation_us);
   const color=half?(p.phase%2?'#bb7541':'#497eae'):'#237663';
   let bar;
   if(!finite) {
    const [x,y]=position(zero,angle);
    bar=this.node('text',{x,y:y+4,'text-anchor':'middle',fill:'#a36244'},'×');
   } else {
    const end=radius(p.deviation_us),inner=Math.min(zero,end),outer=Math.max(zero,end),width=2*Math.PI/chart.bins*.36;
    // A zero deviation is a thin visible arc, distinct from an empty phase.
    const a=position(Math.max(outer,inner+.7),angle-width),b=position(Math.max(outer,inner+.7),angle+width),c=position(inner,angle+width),d=position(inner,angle-width);
    bar=this.node('path',{d:`M ${a} A ${Math.max(outer,inner+.7)} ${Math.max(outer,inner+.7)} 0 0 1 ${b} L ${c} A ${inner} ${inner} 0 0 0 ${d} Z`,fill:color,'fill-opacity':'.86'});
   }
   const description=this.describe(p,half);
   bar.setAttribute('tabindex','0');bar.setAttribute('role','img');bar.setAttribute('aria-label',description);
   bar.append(this.node('title',{},description));
   for(const event of ['pointerenter','focus','click'])bar.addEventListener(event,()=>{cursor.textContent=description;});
   svg.append(bar);
  }
  // Ring values sit on a diagonal between phase spokes.
  for(const value of [-scale,0,scale]) {
   const [x,y]=position(radius(value),2.3);
   svg.append(this.node('text',{x,y,'text-anchor':'middle',class:'phase-axis phase-ring-label'},`${value>0?'+':''}${Number(value.toPrecision(3))}`));
  }
  svg.append(this.node('text',{x:210,y:387,'text-anchor':'middle',class:'phase-axis'},'Deviation from pooled median · µs'));
 }
}
