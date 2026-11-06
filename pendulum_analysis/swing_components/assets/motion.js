(async () => {
  'use strict';
  const spec=JSON.parse(document.getElementById('plot-data').textContent);
  const ids=['sampling','units','start','duration','apply','detail','all','earlier','later','status','motion-chart','hover','selected-stats'];
  const ui=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)]));
  const canvas=ui['motion-chart'],ctx=canvas.getContext('2d'),sides=['tick','tock'],colors=['#333333','#777777'];
  try {
    const series={};
    async function decode(packed){
      const raw=atob(packed.gzip_base64),bytes=new Uint8Array(raw.length);
      for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
      const buffer=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
      const constructors={Float64Array,Uint32Array};
      return Object.fromEntries(Object.entries(packed.columns).map(([key,c])=>[key,new constructors[c.type](buffer,c.offset,c.length)]));
    }
    const chunked=Boolean(spec.individual.chunks),cache=new Map();
    for(const name of ['individual','cycles']) {
      if(name==='individual'&&chunked)continue;
      series[name]=await decode(spec[name]);
    }
    if(chunked)ui.sampling.value='cycles';
    async function loadChunk(chunk){
      if(cache.has(chunk.id))return cache.get(chunk.id);
      const packed=await new Promise((resolve,reject)=>{
        const script=document.createElement('script');let result;
        const receive=event=>{if(event.detail.id===chunk.id)result=event.detail.packed;};
        const clean=()=>{document.removeEventListener('pendulum-motion-chunk',receive);script.remove();};
        document.addEventListener('pendulum-motion-chunk',receive);
        script.src=chunk.src;
        script.onload=()=>{clean();result?resolve(result):reject(new Error('Detailed data did not load. Keep the plots folder beside the report.'));};
        script.onerror=()=>{clean();reject(new Error('Cannot open detailed data. Keep the plots folder beside the report.'));};
        document.head.append(script);
      });
      const data=await decode(packed);cache.set(chunk.id,data);
      while(cache.size>2)cache.delete(cache.keys().next().value);
      return data;
    }
    let lo=spec.start,hi=spec.end,geometry=null;
    let request=0;
    async function update(){
      const version=++request;
      if(chunked&&ui.sampling.value==='individual'){
        const chunks=spec.individual.chunks.filter(c=>c.end>=lo-spec.nominal*1.25&&c.start<hi);
        if(chunks.length>spec.individual.max_chunks){
          ui.sampling.value='cycles';draw();
          ui.status.textContent+=' · showing cycle averages for this wide interval; zoom in for individual passages';
          return;
        }
        ui.status.textContent='Loading individual passages for this interval…';
        geometry=null;
        try{
          const loaded=[];
          for(const chunk of chunks){
            loaded.push(await loadChunk(chunk));
            if(version!==request)return;
          }
          const keys=loaded.length?Object.keys(loaded[0]):['t','run','seq','phase','tick_phase','tock_phase','tick_t','tock_t','tick_v','tock_v','tick_a','tock_a','tick_blocked','tock_blocked','period'];
          const count=loaded.reduce((n,s)=>n+s.t.length,0);
          series.individual=Object.fromEntries(keys.map(key=>{
            const Type=loaded.length?loaded[0][key].constructor:(key==='run'?Uint32Array:Float64Array);
            const result=new Type(count);let offset=0;
            for(const s of loaded){result.set(s[key],offset);offset+=s.t.length;}
            return [key,result];
          }));
        }catch(error){if(version===request)ui.status.textContent=error.message;return;}
      }
      if(version===request)draw();
    }
    const number=v=>Number.isFinite(v)?v.toLocaleString('en-GB',{maximumFractionDigits:5}):'unavailable';
    const find=(a,v)=>{let l=0,r=a.length;while(l<r){const m=(l+r)>>1;if(a[m]<v)l=m+1;else r=m;}return l;};
    const time=(s,side,metric,i)=>metric==='period'?s.t[i]:(s[side+'_t']?.[i]??s.t[i]);
    function value(s,side,metric,i){
      const t=time(s,side,metric,i);
      if(t<lo||t>=hi)return NaN;
      const y=metric==='period'?(s.period[i]-spec.nominal)*1e6:s[side+'_'+metric]?.[i];
      return ui.units.value==='relative'&&['v','a'].includes(metric)?100*(y/spec.baselines[side][metric]-1):y;
    }
    function drawLine(s,a,b,side,metric,X,Y,color) {
      ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=.9;
      let cell=-1,run=-1,first=-1,last=-1,min=-1,max=-1,pen=false;
      const x=i=>X(time(s,side,metric,i));
      function flush(){
        if(first<0)return;
        for(const i of [...new Set([first,min,max,last])].sort((a,b)=>a-b)){
          if(!pen){ctx.moveTo(x(i),Y(value(s,side,metric,i)));pen=true;}
          else ctx.lineTo(x(i),Y(value(s,side,metric,i)));
        }
        first=-1;
      }
      for(let i=a;i<b;i++){
        const v=value(s,side,metric,i);
        if(!Number.isFinite(v)){flush();pen=false;cell=run=-1;continue;}
        if(s.run[i]!==run){flush();pen=false;cell=-1;run=s.run[i];}
        const next=Math.floor(x(i));
        if(next!==cell){flush();cell=next;first=last=min=max=i;}
        else {last=i;if(v<value(s,side,metric,min))min=i;if(v>value(s,side,metric,max))max=i;}
      }
      flush();ctx.stroke();
      if(b-a<150&&ui.sampling.value==='cycles'){ctx.fillStyle=color;for(let i=a;i<b;i++){const v=value(s,side,metric,i);if(Number.isFinite(v)){ctx.beginPath();ctx.arc(x(i),Y(v),2,0,2*Math.PI);ctx.fill();}}}
    }
    function drawPassages(s,a,b,side,metric,X,Y){
      // Keep each phase's first/minimum/maximum/last separately. A dominant rail
      // must never hide another phase when a wide interval shares screen pixels.
      const buckets=Array.from({length:15},()=>({cell:-1,run:-1,indices:[]}));
      const radius=b-a<200?3:1.3;
      function flush(p){
        const bucket=buckets[p];ctx.fillStyle=spec.phase_colors[p];
        for(const i of [...new Set(bucket.indices)]){
          const x=X(time(s,side,metric,i)),y=Y(value(s,side,metric,i));
          ctx.beginPath();
          if(side==='tock'&&metric!=='period'){
            ctx.moveTo(x,y-radius*1.3);ctx.lineTo(x-radius,y+radius);ctx.lineTo(x+radius,y+radius);ctx.closePath();
          }else ctx.arc(x,y,radius,0,2*Math.PI);
          ctx.fill();
        }
        bucket.indices=[];
      }
      for(let i=a;i<b;i++){
        const p=s.phase[i],v=value(s,side,metric,i);
        if(!Number.isInteger(p)||p<0||p>=15)continue;
        const bucket=buckets[p],cell=Math.floor(X(time(s,side,metric,i)));
        if(!Number.isFinite(v)){flush(p);bucket.run=-1;continue;}
        if(cell!==bucket.cell||s.run[i]!==bucket.run){flush(p);bucket.cell=cell;bucket.run=s.run[i];}
        if(!bucket.indices.length)bucket.indices=[i,i,i,i];
        else{
          bucket.indices[3]=i;
          if(v<value(s,side,metric,bucket.indices[1]))bucket.indices[1]=i;
          if(v>value(s,side,metric,bucket.indices[2]))bucket.indices[2]=i;
        }
      }
      for(let p=0;p<15;p++)flush(p);
    }
    function summary(s,a,b){
      const rows=[];
      for(const side of sides){
        for(const [metric,label] of [['v','Passage speed (mm/s)'],['a','Estimated semi-amplitude (°)']]){
          let n=0,mean=0,m2=0,min=Infinity,max=-Infinity;
          for(let i=a;i<b;i++){
            const t=time(s,side,metric,i);if(t<lo||t>=hi)continue;
            const v=s[side+'_'+metric][i];if(!Number.isFinite(v))continue;
            n++;const delta=v-mean;mean+=delta/n;m2+=delta*(v-mean);min=Math.min(min,v);max=Math.max(max,v);
          }
          rows.push([side,label,number(n),n?number(mean):'unavailable',n>1?number(Math.sqrt(m2/(n-1))):'unavailable',n?number(max-min):'unavailable']);
        }
      }
      ui['selected-stats'].innerHTML='<div class="table"><table><thead><tr>'+['Passage','Measurement','Selected values','Mean','SD','Peak-to-peak'].map(v=>'<th>'+v+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</tbody></table></div><p class="meta">Statistics use every selected finite value in absolute units. Cycle counts describe averages; overlapping source edges and serial dependence prevent an independent-sample interpretation.</p>';
    }
    function draw(){
      const s=series[ui.sampling.value],a=find(s.t,lo-spec.nominal*1.25),b=find(s.t,hi),dpr=window.devicePixelRatio||1;
      const width=Math.max(280,canvas.clientWidth||1100),height=850,L=90,R=18;
      canvas.style.height=height+'px';canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
      ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);ctx.font='12px system-ui';
      const X=t=>L+(width-L-R)*(t-lo)/(hi-lo),relative=ui.units.value==='relative';
      const panels=[['v',relative?'Passage speed change (%)':'Passage speed (mm/s)'],['a',relative?'Estimated semi-amplitude change (%)':'Estimated semi-amplitude (°)'],['blocked','Blocked duration (ms)'],['period','Full period − nominal (µs)']];
      panels.forEach(([metric,label],k)=>{
        const top=30+k*200,bottom=top+155,which=metric==='period'?['tick']:sides;
        let min=Infinity,max=-Infinity;
        for(const side of which)for(let i=a;i<b;i++){const v=value(s,side,metric,i);if(Number.isFinite(v)){min=Math.min(min,v);max=Math.max(max,v);}}
        ctx.fillStyle='#203047';ctx.fillText(label,L,top-10);
        if(!Number.isFinite(min)){
          ctx.fillText(metric==='blocked'&&ui.sampling.value==='cycles'?'Choose individual passages to inspect blocked durations.':'No eligible values in this interval.',L,top+50);return;
        }
        const pad=Math.max((max-min)*.08,Math.abs(max)*.00001,.000001);min-=pad;max+=pad;
        const Y=v=>bottom-(bottom-top)*(v-min)/(max-min);
        for(let j=0;j<=3;j++){
          const v=min+(max-min)*j/3,y=Y(v);ctx.strokeStyle='#e1e7ea';ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(width-R,y);ctx.stroke();
          ctx.fillStyle='#203047';ctx.fillText(number(v),2,y+4);
        }
        ctx.save();ctx.beginPath();ctx.rect(L,top,width-L-R,bottom-top);ctx.clip();
        for(const side of which){
          ctx.setLineDash(side==='tock'?[5,3]:[]);
          if(ui.sampling.value==='cycles'||b-a<200)drawLine(s,a,b,side,metric,X,Y,colors[sides.indexOf(side)]);
          ctx.setLineDash([]);
          if(ui.sampling.value==='individual')drawPassages(s,a,b,side,metric,X,Y);
        }
        ctx.restore();
      });
      ctx.fillStyle='#203047';const ticks=width<550?2:4;
      for(let j=0;j<=ticks;j++){
        const t=lo+(hi-lo)*j/ticks,label=(t/86400).toFixed(hi-lo<86400?6:2);
        ctx.fillText(label,Math.max(L,Math.min(width-R-ctx.measureText(label).width,X(t)-ctx.measureText(label).width/2)),820);
      }
      ctx.fillText('Observed elapsed days · phase colours · tick ○ / tock △ · cycle means: solid / dashed',L,842);
      ui.start.value=(lo/86400).toFixed(8);ui.duration.value=((hi-lo)/60).toFixed(4);
      ui.status.textContent=`${number(lo/86400)}–${number(hi/86400)} elapsed days · ${(b-a).toLocaleString('en-GB')} source ${ui.sampling.value==='cycles'?'cycle groups':'swing records'} · scales fitted to this interval`;
      geometry={s,a,b,width,L,R};summary(s,a,b);
    }
    function setWindow(start,end){
      if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start){ui.status.textContent='Enter a finite start day and positive duration.';return;}
      lo=start;hi=end;update();
    }
    ui.apply.onclick=()=>setWindow(Number(ui.start.value)*86400,Number(ui.start.value)*86400+Number(ui.duration.value)*60);
    ui.detail.onclick=()=>{ui.sampling.value='individual';const mid=(lo+hi)/2;setWindow(mid-45,mid+45);};
    ui.all.onclick=()=>setWindow(spec.start,spec.end);
    ui.earlier.onclick=()=>setWindow(lo-(hi-lo)/2,hi-(hi-lo)/2);
    ui.later.onclick=()=>setWindow(lo+(hi-lo)/2,hi+(hi-lo)/2);
    ui.units.onchange=()=>update();ui.sampling.onchange=()=>{
      if(chunked&&ui.sampling.value==='individual'){
        const chunks=spec.individual.chunks.filter(c=>c.end>=lo-spec.nominal*1.25&&c.start<hi);
        if(chunks.length>spec.individual.max_chunks){const mid=(lo+hi)/2;lo=mid-45;hi=mid+45;}
      }
      update();
    };
    canvas.onpointermove=event=>{
      if(!geometry)return;
      const {s,a,b,width,L,R}=geometry,x=event.clientX-canvas.getBoundingClientRect().left;
      if(x<L||x>width-R||a===b)return;
      const t=lo+(x-L)/(width-L-R)*(hi-lo);let i=Math.min(b-1,Math.max(a,find(s.t,t)));
      if(i>a&&Math.abs(s.t[i-1]-t)<Math.abs(s.t[i]-t))i--;
      const tolerance=spec.nominal*(ui.sampling.value==='cycles'?8:1.5);
      if(Math.abs(s.t[i]-t)>tolerance){ui.hover.textContent='No eligible support near this time.';return;}
      ui.hover.textContent=`Day ${number(s.t[i]/86400)} · sequence ${number(s.seq[i])}${ui.sampling.value==='cycles'?'–'+number(s.seq[i]+14):' · phase '+number(s.phase[i])+' (half phases '+'tick '+number(s.tick_phase[i])+'/tock '+number(s.tock_phase[i])+')'} · `+sides.map(side=>side+': '+number(s[side+'_v'][i])+' mm/s, '+number(s[side+'_a'][i])+'°').join(' · ');
    };
    let pending;new ResizeObserver(()=>{cancelAnimationFrame(pending);pending=requestAnimationFrame(()=>{if(series[ui.sampling.value])draw();});}).observe(canvas);
    draw();
  }catch(error){ui.status.textContent='Interactive view unavailable: '+error.message+'. Static figures and CSV exports remain available.';throw error;}
})();
