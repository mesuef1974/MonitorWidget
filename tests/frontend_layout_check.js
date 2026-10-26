"use strict";
// Layout-dependent production functions with controlled measurements; no browser-render claim.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
const css=html.match(/<style id="workspace-design-v2">([\s\S]*?)<\/style>/)[1];
let checks=0;function check(name,f){f();checks++;console.log('PASS '+name)}
const source=(a,b)=>html.slice(html.indexOf(a),html.indexOf(b,html.indexOf(a)));
const lists={},paintCalls=[],timers=[];
const c=vm.createContext({document:{documentElement:{},fonts:null},getComputedStyle:()=>({getPropertyValue:()=>"40"}),ResizeObserver:class{observe(){}disconnect(){}},setTimeout:f=>{timers.push(f);return timers.length},clearTimeout:()=>{},renderStable:(el,h)=>{paintCalls.push(el);el.markup=h},pagerHTML:()=>'<nav>pages</nav>'});
vm.runInContext('const PGS={},PAGEMEM={};'+source('function makePaged(','\ndocument.addEventListener("click", e => { const t = e.target.closest("[data-pgto]")')+'\nthis.makePaged=makePaged;',c);
(async()=>{
 const list={isConnected:true,clientHeight:0,dataset:{},markup:'',closest:()=>null},foot={};let requests=0;
 const paged=c.makePaged('measured',list,foot,v=>`<button data-row="${v}">${v}</button>`,{remote:async(off,size)=>{requests++;return{total:9,rows:Array.from({length:Math.min(size,9-off)},(_,i)=>off+i)}}});
 await paged.paint();
 check('hidden or unmeasured list defers remote read instead of locking to one row',()=>{assert.equal(requests,0);assert.equal(paintCalls.length,0)});
 list.clientHeight=200;await paged.paint();
 check('visible bounded list derives five rows from actual height and row size',()=>{assert.equal(paged.size,5);assert.equal(paged.rows.length,5);assert.equal(list.dataset.h,200);assert.equal(paintCalls[0],list)});
 paged.page=1;list.clientHeight=80;await paged.paint();
 check('resizing a paginated list recomputes capacity and offset',()=>{assert.equal(paged.size,2);assert.equal(paged.rows[0],2)});
 paged.page=4;list.clientHeight=400;await paged.paint();
 check('larger viewport clamps obsolete page and keeps records reachable',()=>{assert.equal(paged.page,0);assert.equal(paged.rows.length,9)});
 list.isConnected=false;const n=requests;await paged.paint();
 check('detached page cannot repopulate or request its old dataset',()=>assert.equal(requests,n));
 check('paginated shells retain definite height; overview and appearance use measured groups',()=>{assert.match(css,/\.page,\.pg,\.hl\{height:100%;min-height:0;overflow:hidden\}/);assert.match(css,/\.pb\{overflow:hidden\}/);assert.match(css,/\.hm\{gap:16px;height:auto/);assert.match(css,/\.appearance-page\{display:flex/)});
 check('wheel routing is scoped and does not select the last visible paginator',()=>{assert.match(html,/addEventListener\("wheel",onPagedWheel,\{passive:false\}\)/);const block=html.slice(html.indexOf('function onPagedWheel('),html.indexOf('document.addEventListener("keydown"',html.indexOf('function onPagedWheel(')));assert.doesNotMatch(block,/\.pop\(\)/);});
 const rows=[{style:{display:'none'}},{style:{display:'none'}},{style:{display:'none'}}],removed=[];
 const rowList={querySelectorAll:q=>q.includes('more')?[{remove:()=>removed.push(1)}]:rows};
 const gpuContext=vm.createContext({});vm.runInContext(source('function gpFitRows(','\nfunction fitGpu(')+'\nthis.fit=gpFitRows;',gpuContext);gpuContext.fit(rowList,'more');
 check('legacy GPU row helper removes an inert +N marker without discarding rows',()=>{assert.ok(rows.every(r=>r.style.display===''));assert.equal(removed.length,1)});
 rows.forEach(r=>r.style.display='none');
 const root={querySelector:()=>null,querySelectorAll:()=>[rowList]};
 check('CPU fit delegates record pages and measured sections while retaining its observer declaration',()=>{const fit=source('function fitCpu(','\nlet cpuRO');assert.ok(fit.includes('CG_STAGE.paint'));assert.ok(fit.includes('cgSchedule'));assert.ok(html.includes('let cpuRO = null;'))});
 const chartContext=vm.createContext({document:{querySelector:()=>({clientWidth:180})}});vm.runInContext(source('function cpuSpark(','\n/* Fit without')+'\nthis.spark=cpuSpark;',chartContext);
 const svg=chartContext.spark({v:[1,3,2],t:[100,160,220]},180);
 check('CPU SVG uses measured 180px width without scaling labels from a 360px floor',()=>{assert.match(svg,/width="180"/);assert.match(svg,/viewBox="0 0 180 180"/);assert.match(svg,/font-size="11"/);assert.doesNotMatch(svg,/NaN|Infinity/)});
 check('IO chart height overrides its older higher-specificity flex growth rule',()=>assert.match(css,/\.io \.gp-ch \.gp-plot\{flex:none;height:144px;min-height:144px;max-height:180px\}/));
 check('overview charts have explicit measured groups and a guarded compact rendering surface',()=>{assert.ok(html.includes('const HH_STAGE='));assert.ok(html.includes('hhCompactHomeChart'));assert.match(html,/if\(box.clientWidth<=0 \|\| box.clientHeight<=0\)return/)});
 console.log(`RESULT ${checks} layout-function checks passed; browser rendering remains unverified`);
})().catch(e=>{console.error(e);process.exitCode=1});
