'use strict';
// Exercise the real SVG renderer, metric selection, inspection and stale masking.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag='div'){this.tag=tag;this.children=[];this.attributes={};this.listeners={};this.textContent='';this.value='velocity_mm_s';}
 append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=nodes;}
 setAttribute(key,value){this.attributes[key]=value;} addEventListener(event,callback){this.listeners[event]=callback;}
}
const elements={},context=vm.createContext({document:{getElementById:id=>elements[id] ||= new Element(),createElementNS:(_,tag)=>new Element(tag)}});
vm.runInContext(fs.readFileSync('Raspberry.Pi/pendulum_pi/static/motion.js','utf8')+'\nglobalThis.Motion=PendulumMotion;',context);
const chart=new context.Motion();
const motion={available:true,count:300,learning:false,latest:{tick_velocity_mm_s:160,tock_velocity_mm_s:161,tick_amplitude_deg:2.35,tock_amplitude_deg:2.37},mean:{tick_velocity_mm_s:162,tock_velocity_mm_s:163,tick_amplitude_deg:2.38,tock_amplitude_deg:2.39},cycle:{complete_cycles:20,candidate_phase:10,points:Array.from({length:30},(_,phase)=>({phase,side:phase%2?'tock':'tick',velocity_mm_s:160+phase*.1,amplitude_deg:2.35+phase*.001,count:20}))}};
chart.update(motion,false,'PPS');
assert.equal(elements['motion-tick-velocity'].textContent,'160.0 mm/s');
assert.equal(elements['motion-tock-mean-amplitude'].textContent,'2.39°');
assert.match(elements['motion-cycle-note'].textContent,/20 complete cycles.*phase 10/);
const svg=elements['motion-cycle-chart'];
let dots=svg.children.filter(n=>n.tag==='circle');assert.equal(dots.length,30);
chart.update(JSON.parse(JSON.stringify(motion)),false,'PPS');
assert.equal(svg.children.find(n=>n.tag==='circle'),dots[0],'Unchanged polling preserves focused SVG points');
assert.equal(dots[0].attributes.fill,'#497eae');assert.equal(dots[15].attributes.fill,'#bb7541');
dots[5].listeners.focus();assert.match(elements['motion-cycle-cursor'].textContent,/Tick · phase 10.*candidate impulse feature/);
assert.equal(dots[5].attributes.tabindex,'0');
elements['motion-cycle-metric'].value='amplitude_deg';elements['motion-cycle-metric'].onchange();
assert.match(svg.children.find(n=>n.tag==='circle').attributes['aria-label'],/2.3500°/);
chart.update(motion,false,'HOLDOVER');assert.match(elements['motion-state'].textContent,/PPS holdover/);
for(const stale of [true,false]){
 chart.update({...motion,stale:true},stale,'PPS');
 assert.equal(elements['motion-tick-velocity'].textContent,'Stale');
 assert.equal(svg.children.filter(n=>n.tag==='circle').length,0);
}
chart.update(undefined,false,'WAIT');assert.equal(elements['motion-tick-velocity'].textContent,'—');
chart.update({...motion,count:1,learning:true,cycle:{complete_cycles:0,points:[]}},false,'PPS');
assert.match(elements['motion-mean-note'].textContent,/1\/300.*filling window/);
assert.equal(svg.children.filter(n=>n.tag==='circle').length,0);
const html=fs.readFileSync('Raspberry.Pi/pendulum_pi/static/index.html','utf8');
for(const id of Object.keys(elements))assert(html.includes(`id="${id}"`),`Missing HTML: ${id}`);
console.log('PASS: motion values, direction colours, cycle metric, candidate, keyboard inspection, filling, holdover and staleness.');
