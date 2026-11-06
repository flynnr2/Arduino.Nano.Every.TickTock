(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('data').textContent);
  const ns = 'http://www.w3.org/2000/svg';
  const controls = Object.fromEntries(['units','extent','mask','bins'].map(id => [id, document.getElementById(id)]));
  const views = document.getElementById('views');
  const tip = document.getElementById('tooltip');
  const state = {units:'percent', extent:'all', mask:'2', bins:1, overrides:{}};
  const groups = [];
  const number = x => x.toLocaleString('en-GB', {maximumFractionDigits:2});
  const signed = x => Number.isFinite(x) ? (x >= 0 ? '+' : '−') + Math.abs(x).toFixed(2) : 'unavailable';
  const el = (name, attrs={}, content) => {
    const n = document.createElementNS(ns, name);
    Object.entries(attrs).forEach(([k,v]) => n.setAttribute(k, v));
    if(content !== undefined) n.textContent = content;
    return n;
  };
  function addText(svg, x, y, text, anchor='middle', extra={}) {
    svg.append(el('text', {x,y,'text-anchor':anchor,...extra}, text));
  }
  function ticks(lo, hi, count) {
    const rough = (hi-lo)/count;
    const power = 10**Math.floor(Math.log10(rough));
    const scale = rough/power;
    const step = (scale<=1?1:scale<=2?2:scale<=5?5:10)*power;
    const out=[];
    for(let x=Math.ceil(lo/step)*step; x<=hi+step*1e-8; x+=step) out.push(Math.abs(x)<step*1e-8?0:x);
    return out;
  }
  function ownScale(g, phase) {
    const key = `${g.key}:${phase}`;
    if(Object.hasOwn(state.overrides,key)) return state.overrides[key];
    if(state.mask === 'none') return false;
    if(state.mask === 'all') return true;
    const seeds = g.seeds;
    return seeds.some(p => Math.min((phase-p+g.phases.length)%g.phases.length,(p-phase+g.phases.length)%g.phases.length)<=Number(state.mask));
  }
  function histogram(p) {
    const k=Number(state.bins), width=p.width*k;
    // Align coarser bins to a common zero-based lattice across every panel.
    const lo=Math.floor(p.lo/width)*width;
    const offset=Math.round((p.lo-lo)/p.width);
    const counts=Array(Math.ceil((offset+p.counts.length)/k)).fill(0);
    p.counts.forEach((v,i) => { counts[Math.floor((i+offset)/k)]+=v; });
    const zoomLo=p.lo+p.zoom[0]*p.width, zoomHi=p.lo+p.zoom[1]*p.width;
    const first=state.extent==='all'?0:Math.max(0,Math.floor((zoomLo-lo)/width));
    const end=state.extent==='all'?counts.length:Math.min(counts.length,Math.ceil((zoomHi-lo)/width));
    const yFactor=state.units==='percent'?100/p.n:1;
    return {counts,width,lo,first,end,yFactor,peak:Math.max(...counts)*yFactor};
  }
  function drawPanel(g, panel, h, common) {
    const {p,index,svg,button,moments,tail} = panel;
    const own=ownScale(g,index);
    panel.root.classList.toggle('own',own);
    button.setAttribute('aria-pressed',String(own));
    button.innerHTML = `Phase ${index}<span class="scale-label">${own?'Own scale':'Shared scale'}</span>`;
    button.setAttribute('aria-label',`Phase ${index}, ${own?'own':'shared'} vertical scale; click to switch`);
    moments.textContent=`SD ${number(p.sd)} µs · skew ${signed(p.skew)} · excess kurt. ${signed(p.kurt)}`;
    const leftHidden=h.counts.slice(0,h.first).reduce((a,b)=>a+b,0);
    const rightHidden=h.counts.slice(h.end).reduce((a,b)=>a+b,0);
    tail.textContent=state.extent==='all'?'All observations shown':`Outside view: ${number(leftHidden)} left + ${number(rightHidden)} right (${number(100*(leftHidden+rightHidden)/p.n)}%)`;
    const W=svg.clientWidth, H=228, left=55, right=15, top=12, bottom=53;
    if(W<140) return;
    const x0=h.lo+h.first*h.width, x1=h.lo+h.end*h.width;
    const ceiling=(own?h.peak:common)*1.08 || 1;
    const X=x=>left+(x-x0)/(x1-x0)*(W-left-right);
    const Y=y=>H-bottom-y/ceiling*(H-top-bottom);
    svg.replaceChildren(); svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    svg.setAttribute('aria-label',`${g.label}, phase ${index}. ${own?'Independent':'Shared'} vertical scale. Full-population skewness ${signed(p.skew)}, excess kurtosis ${signed(p.kurt)}. ${tail.textContent}.`);
    svg.append(el('title',{},`${g.label} · Phase ${index}`));
    for(const y of ticks(0,ceiling,3)) {
      svg.append(el('line',{x1:left,x2:W-right,y1:Y(y),y2:Y(y),class:'gridline'}));
      addText(svg,left-7,Y(y)+4,number(y),'end');
    }
    svg.append(el('line',{x1:left,x2:W-right,y1:Y(0),y2:Y(0),class:'axis'}));
    svg.append(el('line',{x1:left,x2:left,y1:top,y2:Y(0),class:'axis'}));
    const xt=ticks(x0,x1,W<330?3:4);
    for(const x of xt) {
      const pos=X(x);
      svg.append(el('line',{x1:pos,x2:pos,y1:Y(0),y2:Y(0)+4,class:'axis'}));
      const anchor=pos>W-right-20?'end':pos<left+15?'start':'middle';
      addText(svg,pos,H-bottom+20,number(x),anchor);
    }
    // One filled step path retains every original bin without thousands of DOM nodes.
    let path=`M${X(x0)},${Y(0)}`;
    for(let j=h.first;j<h.end;j++) {
      const y=Y(h.counts[j]*h.yFactor);
      path+=`L${X(h.lo+j*h.width)},${y}L${X(h.lo+(j+1)*h.width)},${y}`;
    }
    path+=`L${X(x1)},${Y(0)}Z`;
    svg.append(el('path',{d:path,fill:g.colors[index],'fill-opacity':'.88'}));
    for(const [v,color,dash] of [[p.mean,'var(--mean)','4 3'],[p.median,'var(--ink)','']]) {
      if(v>=x0 && v<=x1) svg.append(el('line',{x1:X(v),x2:X(v),y1:top,y2:Y(0),stroke:color,'stroke-width':1.3,'stroke-dasharray':dash}));
    }
    addText(svg,(left+W-right)/2,H-8,`Full period − ${g.nominal} s (µs)`);
    const ylabel=state.units==='percent'?'Cycles / bin (%)':'Cycles / bin';
    addText(svg,13,(top+H-bottom)/2,ylabel,'middle',{transform:`rotate(-90 13 ${(top+H-bottom)/2})`});
    const hit=el('rect',{x:left,y:top,width:W-left-right,height:H-top-bottom,fill:'transparent'});
    hit.addEventListener('pointermove',event=>{
      const bounds=svg.getBoundingClientRect();
      const val=x0+(event.clientX-bounds.left-left)/(W-left-right)*(x1-x0);
      const bin=Math.min(h.end-1,Math.max(h.first,Math.floor((val-h.lo)/h.width)));
      tip.textContent=`${g.label} · Phase ${index}\n${number(h.lo+bin*h.width)} to ${number(h.lo+(bin+1)*h.width)} µs\n${number(h.counts[bin])} cycles · ${number(h.counts[bin]/p.n*100)}% of all ${number(p.n)}`;
      tip.style.display='block';
      tip.style.left=Math.max(4,Math.min(window.innerWidth-tip.offsetWidth-8,event.clientX+12))+'px';
      tip.style.top=Math.max(4,Math.min(window.innerHeight-tip.offsetHeight-8,event.clientY+12))+'px';
    });
    hit.addEventListener('pointerleave',()=>{tip.style.display='none';});
    svg.append(hit);
  }
  function draw() {
    tip.style.display='none';
    for(const g of groups) {
      const hists=g.phases.map(histogram);
      const ordinary=hists.filter((_,i)=>!ownScale(g,i));
      const common=Math.max(0,...ordinary.map(h=>h.peak));
      const own=g.phases.map((_,i)=>i).filter(i=>ownScale(g,i));
      g.note.textContent=`${state.units==='percent'?'Percent of all cycles':'Cycle counts'} · ${hists[0].width} µs bins · ${ordinary.length} phases share 0–${number(common*1.08)}${state.units==='percent'?'%':''} · own scale: ${own.length?own.join(', '):'none'}`;
      g.panels.forEach((p,i)=>drawPanel(g,p,hists[i],common));
    }
  }
  const layout=Object.keys(data).map(dataset=>[dataset,'full','phase15-'+dataset.replace(/[^a-zA-Z0-9-]/g,'-')]);
  for(const [dataset,kind,id] of layout) {
    const phases=data[dataset].metrics[kind];
    const label=dataset+` · Phase-${phases.length}`;
    const section=document.createElement('section'); section.id=id;
    section.innerHTML=`<h2>${label}</h2><p class="meta">${number(phases[0].n)} complete cycles · one observation per phase per cycle</p><p class="scale-note" aria-live="polite"></p><div class="grid"></div>`;
    const g={seeds:data[dataset].seeds,nominal:data[dataset].nominal,colors:data[dataset].colors,dataset,kind,key:`${dataset}:${kind}`,label,phases,note:section.querySelector('.scale-note'),panels:[]};
    const grid=section.querySelector('.grid');
    phases.forEach((p,index)=>{
      const root=document.createElement('div'); root.className='panel';
      root.style.borderTop='3px solid '+g.colors[index];
      root.innerHTML='<button type="button" class="phase-title"></button><div class="moments"></div><svg role="img"></svg><div class="tail"></div>';
      const panel={root,p,index,button:root.querySelector('button'),moments:root.querySelector('.moments'),svg:root.querySelector('svg'),tail:root.querySelector('.tail')};
      panel.button.addEventListener('click',()=>{ state.overrides[`${g.key}:${index}`]=!ownScale(g,index); draw(); });
      g.panels.push(panel); grid.append(root);
    });
    groups.push(g); views.append(section);
  }
  Object.entries(controls).forEach(([key,control])=>control.addEventListener('change',()=>{
    state[key]=control.value;
    if(key==='mask') Object.keys(state.overrides).forEach(k=>delete state.overrides[k]);
    draw();
  }));
  let frame;
  const resize=new ResizeObserver(()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(draw);});
  resize.observe(views);
  draw();
})();
