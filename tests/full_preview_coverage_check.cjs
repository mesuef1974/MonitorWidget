'use strict';
// Independent route/content reachability check. Synthetic DOM, not rendered layout.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert/strict');
const {parseHTML}=require('./dom-runtime/node_modules/linkedom');
const base=path.resolve(__dirname,'../docs/design-skills-preview-20261026');
const {document,HTMLElement}=parseHTML(fs.readFileSync(path.join(base,'index.html'),'utf8'));
HTMLElement.prototype.getBoundingClientRect=()=>({width:360,height:150});
const listeners={};const context={document,window:null,innerWidth:1280,innerHeight:800,location:{hash:''},matchMedia:()=>({matches:false,addEventListener(){}}),addEventListener:(n,f)=>listeners[n]=f,Date,setTimeout,clearTimeout,requestAnimationFrame:f=>f(),console};
context.window=context;vm.createContext(context);
for(const name of ['content.js','preview.js'])vm.runInContext(fs.readFileSync(path.join(base,name),'utf8'),context);
const api=context.previewReview,inventory=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../docs/preview-source-routes-20261026.json'),'utf8'));
assert.deepEqual(Array.from(api.routes,r=>r.id).sort(),inventory.routes.map(r=>r.id).sort());
let sections=0,panels=0;
for(const [width,height] of [[1280,800],[1600,1000],[900,650]]){
 context.innerWidth=width;context.innerHeight=height;
 for(const route of api.routes)for(let section=0;section<route.sections.length;section++){
  api.go(route.id,section);const seen=new Set();let safety=0;
  do{for(const node of document.querySelectorAll('#content [data-panel]')){assert.ok(node.textContent.trim().length>10);seen.add(Number(node.dataset.panel))}assert.ok(++safety<30)}while(api.page(1));
  assert.equal(seen.size,route.sections[section].cards.length,route.id+':'+section);
  if(width===1280){sections++;panels+=seen.size}
 }
}
api.go('widget');assert.equal(document.getElementById('widgetPreview').hidden,false);
assert.equal(document.querySelectorAll('#tierRows [role="switch"]').length,4);
assert.equal(document.querySelectorAll('#tierRows [data-demo]').length,4);
assert.equal(document.querySelectorAll('.ring-item').length,3);
assert.ok(document.querySelector('.widget-trim'));
console.log(`PASS independent coverage: ${api.routes.length} routes, ${sections} sections, ${panels} panels, 3 synthetic viewport sizes; full widget retained. No browser rendering.`);
