'use strict';
// No browser packages needed: exercise the real SVG renderer and interactions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
 constructor(tag='div') {this.tag=tag;this.children=[];this.attributes={};this.listeners={};this.textContent='';}
 append(...nodes) {this.children.push(...nodes);}
 replaceChildren(...nodes) {this.children=nodes;}
 setAttribute(key,value) {this.attributes[key]=value;}
 addEventListener(event,callback) {this.listeners[event]=callback;}
}
const elements = {};
const context = vm.createContext({document:{createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)}});
vm.runInContext(fs.readFileSync('Raspberry.Pi/pendulum_pi/static/phase.js','utf8')+'\nglobalThis.Phase=ObservatoryPhase;',context);
const chart=Object.create(context.Phase.prototype);
chart.el=id=>elements[id] ||= new Element();
function data(metric) {
 const bins=metric==='full'?15:30;
 return {metric,bins,references_s:metric==='full'?[2]:[.99,1.01],points:Array.from({length:bins},(_,phase)=>({phase,median_s:2+phase/1e6,deviation_us:phase-7,count:10,excluded:1,records:11}))};
}
const full=data('full');full.points[3]={phase:3,median_s:null,deviation_us:null,count:0,excluded:11,records:11};
chart.draw('full',full);
assert.equal(elements['phase-full'].children.filter(n=>n.tag==='path').length,14);
assert.equal(elements['phase-full-rows'].children.length,15);
assert(elements['phase-full'].children.some(n=>n.textContent==='×'));
const bar=elements['phase-full'].children.find(n=>n.tag==='path');
bar.listeners.focus();assert.match(elements['phase-cursor'].textContent,/Phase 0.*10 eligible, 1 excluded/);
assert.equal(bar.attributes.tabindex,'0');
assert.match(bar.attributes['aria-label'],/median/);
chart.draw('half',data('half'));
const halves=elements['phase-half'].children.filter(n=>n.tag==='path');
assert.equal(halves.length,30);
assert.equal(halves[0].attributes.fill,'#497eae');
assert.equal(halves[1].attributes.fill,'#bb7541');
assert.match(elements['phase-half-reference'].textContent,/Tick reference 0.990000000 s.*tock reference 1.010000000 s/);
halves[1].listeners.click();assert.match(elements['phase-cursor'].textContent,/Phase 1.*Tock/);
const empty=data('half');for(const p of empty.points){p.median_s=null;p.deviation_us=null;p.count=0;}
chart.draw('half',empty);
assert.equal(elements['phase-half'].children.filter(n=>n.tag==='path').length,0);
assert.equal(elements['phase-half-rows'].children.length,30,'Excluded counts remain inspectable without valid PPS');
chart.render({charts:[],state:'updating',updating:true});
assert.equal(elements['phase-badge'].textContent,'Updating');
assert.equal(elements['phase-full-rows'].children.length,0,'A new session clears previous data');
chart.render({charts:[full],state:'unavailable',stale:true,message:'Refresh failed',segment:'test/segment-000001',generated_at:123,live:false});
assert.equal(elements['phase-badge'].textContent,'Previous snapshot');
assert.match(elements['phase-state'].textContent,/Refresh failed/);
assert.match(elements['phase-coverage'].textContent,/has not been refreshed/);
chart.render({charts:[],state:'unavailable',message:'Missing analysis dependency',segment:'test/segment-000001',source:'serial',live:true});
assert.match(elements['phase-coverage'].textContent,/Recording detected.*Capture is active/);
assert.doesNotMatch(elements['phase-coverage'].textContent,/Waiting for a recording/);
assert(elements['phase-full'].children.some(n=>n.textContent==='Phase analysis unavailable'));
assert(!elements['phase-full'].children.some(n=>n.textContent==='Waiting for valid PPS coverage'));
assert.match(elements['phase-full-reference'].textContent,/Analysis unavailable/);
console.log('PASS: phase medians, empty bins, tick/tock colours, accessible inspection and stale snapshots.');
