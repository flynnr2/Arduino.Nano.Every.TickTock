(async () => {
  'use strict';
  const spec=JSON.parse(document.getElementById('plot-data').textContent);
  const nominal=spec.nominal || 2;
  const names=['sampling','units','scale','start','duration','apply','two','context','all','earlier','later','status','hover','selected-stats','rolling-chart'];
  const ui=Object.fromEntries(names.map(id=>[id,document.getElementById(id)]));
  const canvas=ui['rolling-chart'],ctx=canvas.getContext('2d');
  try {
    const series={};
    for(const [name,packed] of Object.entries(spec.series)) {
      const raw=atob(packed.gzip_base64),bytes=new Uint8Array(raw.length);
      for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
      const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      const buffer=await new Response(stream).arrayBuffer();
      const constructors={Float64Array,Float32Array,Uint32Array};
      const data={};
      for(const [field,c] of Object.entries(packed.columns))data[field]=new constructors[c.type](buffer,c.offset,c.length);
      series[name]=data;
    }
    let lo=0,hi=series.dense.t[series.dense.t.length-1]+2,focus=null;
    let width=1200,height=540,geometry=null;
    const L=76,R=22,T1=30,B1=225,T2=295,B2=490;
    const number=x=>Number.isFinite(x)?x.toLocaleString('en-GB',{maximumFractionDigits:4}):'unavailable';
    const dates=spec.origin?new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/London',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',second:'2-digit'}):null;
    function stamp(t){return 'day '+(t/86400).toFixed(6)+(dates?' · '+dates.format(new Date(Date.parse(spec.origin)+t*1000)):'');}
    function find(a,value){let l=0,r=a.length;while(l<r){const m=(l+r)>>1;if(a[m]<value)l=m+1;else r=m;}return l;}
    function top(s,i){return ui.units.value==='frequency'?(nominal/(nominal+s.mean[i]/1e6)-1)*1e6:s.mean[i];}
    function bottom(s,i){
      if(!Number.isFinite(s.range[i]))return NaN;
      return ui.units.value==='frequency'?(nominal/(nominal+s.low[i]/1e6)-nominal/(nominal+(s.low[i]+s.range[i])/1e6))*1e6:s.range[i];
    }
    function bounds(s,a,b,value,zero){
      let min=zero?0:Infinity,max=zero?0:-Infinity;
      for(let i=a;i<b;i++){const v=value(s,i);if(Number.isFinite(v)){min=Math.min(min,v);max=Math.max(max,v);}}
      if(!Number.isFinite(min)||!Number.isFinite(max))return [0,1];
      const pad=Math.max(.001,(max-min)*.08,Math.abs(max)*.002);
      return [zero?0:min-pad,max+pad];
    }
    function summary(s,a,b){
      const n=b-a;
      if(!n){ui['selected-stats'].textContent='No eligible averaged periods in this interval.';return;}
      let sum=0,min=Infinity,max=-Infinity,breaks=0;
      for(let i=a;i<b;i++){const y=s.mean[i];sum+=y;min=Math.min(min,y);max=Math.max(max,y);if(i>a&&s.run[i]!==s.run[i-1])breaks++;}
      const mean=sum/n,centre=(s.t[a]+s.t[b-1])/2;
      let xx=0,xy=0,yy=0,sx=0;
      for(let i=a;i<b;i++){const x=s.t[i]-centre,y=s.mean[i]-mean;sx+=x;xx+=x*x;xy+=x*y;yy+=y*y;}
      const ssx=xx-sx*sx/n,slope=ssx>0?xy/ssx:0;
      const residual=Math.sqrt(Math.max(0,(yy-(ssx>0?xy*xy/ssx:0))/n));
      const rows=[['Averaged-period points',number(n)],['Mean period offset (µs)',number(mean)],
        ['Peak-to-peak of selected means (µs)',number(max-min)],['RMS about selected mean (µs)',number(Math.sqrt(yy/n))],
        ['Linear slope (µs/hour)',n>=3?number(slope*3600):'insufficient points'],
        ['Linear-detrended RMS (µs)',n>=3?number(residual):'insufficient points'],['Discontinuities inside selection',number(breaks)]];
      ui['selected-stats'].innerHTML='<div class="table"><table><tbody>'+rows.map(r=>'<tr><th>'+r[0]+'</th><td>'+r[1]+'</td></tr>').join('')+'</tbody></table></div>';
    }
    function drawLine(s,a,b,value,X,Y,color){
      ctx.strokeStyle=color;ctx.lineWidth=1;ctx.beginPath();
      let cell=-1,run=-1,first=-1,last=-1,min=-1,max=-1,pen=false;
      function flush(){
        if(first<0)return;
        const indices=[...new Set([first,min,max,last])].sort((a,b)=>a-b);
        for(const i of indices){const x=X(s.t[i]),y=Y(value(s,i));if(!pen){ctx.moveTo(x,y);pen=true;}else ctx.lineTo(x,y);}
        first=-1;
      }
      for(let i=a;i<b;i++){
        const v=value(s,i);
        if(!Number.isFinite(v)){flush();pen=false;run=-1;cell=-1;continue;}
        const c=Math.floor(X(s.t[i]));
        if(s.run[i]!==run){flush();pen=false;run=s.run[i];cell=-1;}
        if(c!==cell){flush();cell=c;first=min=max=last=i;}
        else {last=i;if(v<value(s,min))min=i;if(v>value(s,max))max=i;}
      }
      flush();ctx.stroke();
      if(b-a<100){ctx.fillStyle=color;for(let i=a;i<b;i++){const v=value(s,i);if(Number.isFinite(v)){ctx.beginPath();ctx.arc(X(s.t[i]),Y(v),2,0,2*Math.PI);ctx.fill();}}}
    }
    function draw(){
      const s=series[ui.sampling.value],a=find(s.t,lo),b=find(s.t,hi);
      const dpr=window.devicePixelRatio||1;
      width=Math.max(280,canvas.clientWidth||1200);
      height=width<500?560:540;
      canvas.style.height=height+'px';canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
      ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
      ctx.font='13px system-ui';ctx.fillStyle='#283e4b';
      if(a===b){ctx.fillText('No eligible averages in this interval.',L,100);ui.status.textContent='No eligible averages; choose another interval.';summary(s,a,b);geometry=null;return;}
      const full=ui.scale.value==='dataset',aa=full?0:a,bb=full?s.t.length:b;
      const upper=bounds(s,aa,bb,top,false),lower=bounds(s,aa,bb,bottom,true);
      const X=t=>L+(width-L-R)*(t-lo)/(hi-lo);
      const Y1=v=>B1-(B1-T1)*(v-upper[0])/(upper[1]-upper[0]);
      const Y2=v=>B2-(B2-T2)*(v-lower[0])/(lower[1]-lower[0]);
      ctx.save();
      if(focus&&focus[0]<hi&&focus[1]>lo){
        ctx.fillStyle='#d9ad50';ctx.globalAlpha=.16;
        for(const [t,b] of [[T1,B1],[T2,B2]])ctx.fillRect(X(Math.max(lo,focus[0])),t,X(Math.min(hi,focus[1]))-X(Math.max(lo,focus[0])),b-t);
        ctx.globalAlpha=1;
      }
      ctx.font='13px system-ui';
      for(const [domain,Y,t,b] of [[upper,Y1,T1,B1],[lower,Y2,T2,B2]]){
        for(let j=0;j<=3;j++){
          const v=domain[0]+(domain[1]-domain[0])*j/3,y=Y(v);
          ctx.strokeStyle='#e1e7ea';ctx.lineWidth=.7;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(width-R,y);ctx.stroke();
          const label=number(v);ctx.fillStyle='#283e4b';ctx.fillText(label,Math.max(2,L-ctx.measureText(label).width-8),y+4);
        }
        ctx.strokeStyle='#93a1a9';ctx.beginPath();ctx.moveTo(L,t);ctx.lineTo(L,b);ctx.lineTo(width-R,b);ctx.stroke();
      }
      ctx.fillStyle='#283e4b';
      ctx.fillText(ui.units.value==='period'?`Mean full period − ${nominal} s (µs)`:`Frequency offset from ${1/nominal} Hz (ppm)`,L,17);
      ctx.fillText(ui.units.value==='period'?'Trailing two-minute range (µs)':'Trailing two-minute frequency range (ppm)',L,280);
      ctx.save();ctx.beginPath();ctx.rect(L,T1,width-L-R,B1-T1);ctx.clip();drawLine(s,a,b,top,X,Y1,'#307c94');ctx.restore();
      ctx.save();ctx.beginPath();ctx.rect(L,T2,width-L-R,B2-T2);ctx.clip();drawLine(s,a,b,bottom,X,Y2,'#bd6a34');ctx.restore();
      ctx.fillStyle='#283e4b';ctx.font='12px system-ui';
      const ticks=width<500?2:4;
      for(let j=0;j<=ticks;j++){
        const t=lo+(hi-lo)*j/ticks,txt='day '+(t/86400).toFixed(hi-lo<86400?5:2),tw=ctx.measureText(txt).width;
        ctx.fillText(txt,Math.max(L,Math.min(width-R-tw,X(t)-tw/2)),B2+24);
      }
      ctx.restore();
      const available=Array.prototype.reduce.call(s.range.subarray(a,b),(n,v)=>n+Number.isFinite(v),0);
      ui.status.textContent=`${stamp(lo)} — ${stamp(hi)} · ${(b-a).toLocaleString('en-GB')} averaged periods · ${available.toLocaleString('en-GB')} complete trailing ranges · ${full?'whole-dataset scales':'scales fitted to this interval'}${focus?' · gold: detail interval':''}`;
      ui.start.value=(lo/86400).toFixed(6);ui.duration.value=((hi-lo)/60).toFixed(3);
      geometry={s,a,b,X};summary(s,a,b);
    }
    function setWindow(start,end,preserveFocus=false){
      const endOfData=series.dense.t[series.dense.t.length-1]+2;
      const span=Math.min(endOfData,Math.max(4,end-start));
      lo=Math.max(0,Math.min(endOfData-span,start));hi=lo+span;
      if(!preserveFocus)focus=null;
      draw();
    }
    ui.apply.onclick=()=>{
      const start=Number(ui.start.value)*86400,duration=Number(ui.duration.value)*60;
      if(!Number.isFinite(start)||!Number.isFinite(duration)||duration<=0){ui.status.textContent='Enter a finite start day and a positive duration.';return;}
      setWindow(start,start+duration);
    };
    ui.two.onclick=()=>{const mid=(lo+hi)/2;setWindow(mid-60,mid+60);};
    ui.context.onclick=()=>{const mid=(lo+hi)/2;if(hi-lo<=125)focus=[lo,hi];setWindow(mid-7200,mid+7200,true);};
    ui.all.onclick=()=>setWindow(0,series.dense.t[series.dense.t.length-1]+2);
    ui.earlier.onclick=()=>setWindow(lo-(hi-lo)/2,hi-(hi-lo)/2);
    ui.later.onclick=()=>setWindow(lo+(hi-lo)/2,hi+(hi-lo)/2);
    ui.sampling.onchange=draw;ui.units.onchange=draw;ui.scale.onchange=draw;
    function inspect(event){
      if(!geometry)return;
      const rect=canvas.getBoundingClientRect(),x=event.clientX-rect.left;
      if(x<L||x>width-R)return;
      const t=lo+(x-L)/(width-L-R)*(hi-lo),{s,a,b}=geometry;
      let i=Math.max(a,Math.min(b-1,find(s.t,t)));
      if(i>a&&Math.abs(s.t[i-1]-t)<Math.abs(s.t[i]-t))i--;
      const tolerance=ui.sampling.value==='dense'?3:18;
      if(Math.abs(s.t[i]-t)>tolerance){ui.hover.textContent=stamp(t)+' · no eligible averaged period at this position';return;}
      const left=find(s.t,s.t[i]-120),count=i-left+1-(s.t[left]===s.t[i]-120?1:0);
      ui.hover.textContent=`${stamp(s.t[i])} · support sequences ${s.seq[i]-14}–${s.seq[i]} · mean offset ${number(s.mean[i])} µs · trailing range ${number(s.range[i])} µs${Number.isFinite(s.range[i])?' · '+count+' points in the range window':''}`;
    }
    canvas.onpointermove=inspect;canvas.onclick=inspect;
    let pending;
    const observer=new ResizeObserver(()=>{cancelAnimationFrame(pending);pending=requestAnimationFrame(draw);});
    observer.observe(canvas);draw();
  } catch(error) {
    ui.status.textContent='The interactive view could not initialise: '+error.message+'. Static figures and CSV exports remain available.';
    throw error;
  }
})();
