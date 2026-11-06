(async()=>{
 'use strict';
 const d=JSON.parse(document.getElementById('data').textContent);
 const names=['chart','mode','scale','net','day','duration','apply','all','zoomIn','zoomOut','previous','next','range','hover','hover-values','legend'];
 const ui=Object.fromEntries(names.map(n=>[n,document.getElementById(n)]));
 try{
  const raw=atob(d.packed.gzip_base64),bytes=new Uint8Array(raw.length);
  for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
  const buffer=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  const types={Float64Array,Float32Array,Uint32Array};
  const arrays={};for(const [k,c] of Object.entries(d.packed.columns))arrays[k]=new types[c.type](buffer,c.offset,c.length);
  const {t,seq,values}=arrays,N=t.length,canvas=ui.chart,ctx=canvas.getContext('2d');
  let lo=Math.max(0,t[0]-15*d.nominal_s/2),hi=t[N-1]+15*d.nominal_s/2;
  const first=lo,last=hi;let start=0,end=N,W=1200;
  const L=72,R=20,T=30,B=455,H=510;
  const value=(i,p)=>values[i*15+p]-(ui.mode.value==='comparison'?d.medians[p]:0);
  const search=x=>{let l=0,r=N;while(l<r){const m=(l+r)>>1;if(t[m]<x)l=m+1;else r=m;}return l;};
  for(let p=0;p<15;p++){const span=document.createElement('span'),i=document.createElement('i');i.style.background=d.colors[p];span.append(i,document.createTextNode('Phase '+p));ui.legend.append(span);}
  function draw(){
   start=search(lo);end=search(hi);W=Math.max(280,canvas.clientWidth||1200);
   const ratio=window.devicePixelRatio||1;canvas.style.height=H+'px';canvas.width=Math.round(W*ratio);canvas.height=Math.round(H*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,W,H);ctx.font='13px system-ui';ctx.fillStyle='#283e4b';
   if(start===end){ctx.fillText('No eligible complete cycles in this window.',L,100);ui.range.textContent='No eligible cycles; select another window.';return;}
   let [min,max]=d.limits[ui.mode.value];
   if(ui.scale.value==='visible'){
    min=0;max=0;for(let i=start;i<end;i++){let pos=0,neg=0;for(let p=0;p<15;p++){const v=value(i,p);pos+=Math.max(0,v);neg+=Math.min(0,v);}min=Math.min(min,neg);max=Math.max(max,pos);}
    const pad=Math.max(100,(max-min)*.05);min-=pad;max+=pad;
   }
   const X=x=>L+(W-L-R)*(x-lo)/(hi-lo),Y=y=>B-(B-T)*(y-min)/(max-min);
   for(let j=0;j<=5;j++){const v=min+(max-min)*j/5;ctx.strokeStyle='#e1e7ea';ctx.lineWidth=.7;ctx.beginPath();ctx.moveTo(L,Y(v));ctx.lineTo(W-R,Y(v));ctx.stroke();const label=(v/1000).toFixed(2);ctx.fillText(label,Math.max(2,L-ctx.measureText(label).width-8),Y(v)+4);}
   ctx.fillText(ui.mode.value==='nominal'?`Cycle duration − ${15*d.nominal_s} s (ms)`:'Cycle duration − sum of phase medians (ms)',L,17);
   ctx.save();ctx.beginPath();ctx.rect(L,T,W-L-R,B-T);ctx.clip();
   const half=7*d.nominal_s;
   for(let i=start;i<end;i++){let pos=0,neg=0;const left=X(t[i]-half),width=Math.max(.01,X(t[i]+half)-left);for(let p=0;p<15;p++){const v=value(i,p),base=v>=0?pos:neg;ctx.fillStyle=d.colors[p];ctx.fillRect(left,Math.min(Y(base),Y(base+v)),width,Math.abs(Y(base+v)-Y(base)));if(v>=0)pos+=v;else neg+=v;}}
   ctx.strokeStyle='#111111';ctx.lineWidth=1;
   if(ui.net.checked){ctx.beginPath();for(let i=start;i<end;i++){let sum=0;for(let p=0;p<15;p++)sum+=value(i,p);if(i===start||seq[i]-seq[i-1]!==15)ctx.moveTo(X(t[i]),Y(sum));else ctx.lineTo(X(t[i]),Y(sum));}ctx.stroke();}
   ctx.setLineDash([4,4]);ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(L,Y(0));ctx.lineTo(W-R,Y(0));ctx.stroke();ctx.restore();ctx.setLineDash([]);
   ctx.fillStyle='#283e4b';const ticks=W<500?2:4;for(let j=0;j<=ticks;j++){const x=lo+(hi-lo)*j/ticks,label='day '+(x/86400).toFixed(hi-lo<86400?4:2);ctx.fillText(label,Math.max(L,Math.min(W-R-ctx.measureText(label).width,X(x)-35)),B+27);}
   ui.range.textContent=`Day ${(lo/86400).toFixed(5)}–${(hi/86400).toFixed(5)} · ${(end-start).toLocaleString('en-GB')} actual cycles · ${ui.scale.value==='visible'?'visible-cycle scales':'whole-dataset scales'} · ${ui.net.checked?'black = signed sum':'net line hidden'}`;
   ui.day.value=(lo/86400).toFixed(5);ui.duration.value=((hi-lo)/86400).toFixed(5);
  }
  function windowTo(a,b){const span=Math.min(last-first,Math.max(15*d.nominal_s,b-a));lo=Math.max(first,Math.min(last-span,a));hi=lo+span;draw();}
  ui.apply.onclick=()=>{const a=Number(ui.day.value)*86400,b=Number(ui.duration.value)*86400;if(!Number.isFinite(a)||!Number.isFinite(b)||b<=0){ui.range.textContent='Enter a finite start day and positive duration.';return;}windowTo(a,a+b);};
  ui.all.onclick=()=>windowTo(first,last);ui.zoomIn.onclick=()=>{const mid=(lo+hi)/2;windowTo(mid-(hi-lo)/4,mid+(hi-lo)/4);};ui.zoomOut.onclick=()=>{const mid=(lo+hi)/2;windowTo(mid-(hi-lo),mid+(hi-lo));};
  ui.previous.onclick=()=>windowTo(lo-(hi-lo)/2,hi-(hi-lo)/2);ui.next.onclick=()=>windowTo(lo+(hi-lo)/2,hi+(hi-lo)/2);
  ui.mode.onchange=draw;ui.scale.onchange=draw;ui.net.onchange=draw;
  function inspect(e){if(start===end)return;const x=e.clientX-canvas.getBoundingClientRect().left;if(x<L||x>W-R)return;const at=lo+(x-L)/(W-L-R)*(hi-lo);let i=Math.max(start,Math.min(end-1,search(at)));if(i>start&&Math.abs(t[i-1]-at)<Math.abs(t[i]-at))i--;if(Math.abs(t[i]-at)>8*d.nominal_s){ui.hover.textContent='No complete cycle at this position.';return;}let total=0;ui['hover-values'].replaceChildren();for(let p=0;p<15;p++){const v=value(i,p);total+=v;const span=document.createElement('span');span.textContent=`Phase ${p}: ${(v/1000).toFixed(6)} ms`;ui['hover-values'].append(span);}ui.hover.textContent=`Day ${(t[i]/86400).toFixed(6)} · sequences ${seq[i]}–${seq[i]+14} · net ${(total/1000).toFixed(6)} ms`;}
  canvas.onpointermove=inspect;canvas.onclick=inspect;
  let pending;const observer=new ResizeObserver(()=>{cancelAnimationFrame(pending);pending=requestAnimationFrame(draw);});observer.observe(canvas);draw();
 }catch(error){ui.range.textContent='Interactive view unavailable: '+error.message+'. Static figures and full-precision CSV remain available.';throw error;}
})();
