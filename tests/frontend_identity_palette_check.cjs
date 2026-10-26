'use strict';
// Resolve the actual root-variable cascade. This is not a browser paint test.
const fs=require('fs'),assert=require('assert/strict'),path=require('path');
const html=fs.readFileSync(path.resolve(__dirname,'../index.html'),'utf8');
const css=html.match(/<style id="workspace-design-v2">([\s\S]*?)<\/style>/)[1];
function tokens(mode,palette){
 const state={'data-resolved-theme':mode,'data-theme':mode,'data-palette':palette},out={},weights={};
 for(const [,selector,body]of css.matchAll(/([^{}]+)\{([^{}]*)\}/g))for(let sel of selector.split(',')){
  sel=sel.trim().replace(/\/\*[\s\S]*?\*\//g,'').trim();
  if(!/^:root(?:\[[^\]]+\])*$/.test(sel))continue;
  const attrs=[...sel.matchAll(/\[([^=\]]+)="?([^"\]]+)"?\]/g)];
  if(attrs.some(([,key,value])=>state[key]!==value))continue;
  for(const [,key,value]of body.matchAll(/(--[\w-]+):([^;}]*)/g))if((weights[key]??-1)<=attrs.length){out[key]=value.trim();weights[key]=attrs.length;}
 }
 const resolve=(key,depth=0)=>{assert.ok(depth<12);return out[key]?.replace(/var\((--[\w-]+)\)/g,(_,k)=>resolve(k,depth+1))};
 return Object.fromEntries(Object.keys(out).map(k=>[k,resolve(k)]));
}
function luminance(hex){const rgb=hex.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722}
const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);return(Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
const expected={teal:['#e4f1ed','#f3faf7','#101f1d','#1b312c'],blue:['#e4edf9','#f1f6fe','#101d31','#1b304d'],violet:['#ede5f6','#f8f2fd','#241a32','#342746'],graphite:['#e6ebef','#f4f6f8','#191f25','#29333d']};
let pairs=0,min=100;
for(const mode of ['light','dark']){
 const palettes=Object.keys(expected).map(p=>tokens(mode,p));
 for(const property of ['--paper','--sheet','--selected','--commit'])assert.equal(new Set(palettes.map(p=>p[property])).size,4,mode+property+' must vary across all identities');
 for(const property of ['--ok','--warn','--bad'])assert.equal(new Set(palettes.map(p=>p[property])).size,1,'Semantic colors independent of palette');
 for(const [i,palette]of Object.keys(expected).entries()){
  const t=palettes[i],offset=mode==='dark'?2:0;
  assert.equal(t['--paper'],expected[palette][offset]);assert.equal(t['--sheet'],expected[palette][offset+1]);
  assert.equal(t['--c1'],t['--commit']);assert.equal(t['--seg1'],t['--commit']);
  for(const fg of ['--ink','--ink2','--ink3'])for(const bg of ['--paper','--sheet','--selected']){const r=contrast(t[fg],t[bg]);min=Math.min(min,r);pairs++;assert.ok(r>=4.5,`${mode}/${palette} ${fg}/${bg} ${r}`)}
  assert.ok(contrast(t['--commit'],t['--on-accent'])>=4.5,mode+palette+' accent text');
 }
}
const block=css.slice(css.indexOf('/* BEGIN_APPROVED_WORKSPACE_20261026'));
assert.match(block,/\.rail\{background:var\(--selected\)/);assert.match(block,/\.main\{[^}]*background:var\(--paper\)/);
assert.match(block,/#view \.card[^}]*background:var\(--sheet\)/);
const rail=html.slice(html.indexOf('<aside class="rail"'),html.indexOf('</aside>',html.indexOf('<aside class="rail"')));
assert.equal(new Set([...rail.matchAll(/data-tab="([^"]+)"/g)].map(x=>x[1])).size,19);
assert.match(html,/const on = b\.dataset\.group === "perf" \? perf : b\.dataset\.tab === t;/);
console.log(`PASS full identity cascade:8 theme/palette combinations,4 distinct canvas/card/navigation/chart palettes,${pairs} text pairs minimum${min.toFixed(2)}:1,semantic colors stable,19 direct live routes. No rendered CSS geometry claim.`);
