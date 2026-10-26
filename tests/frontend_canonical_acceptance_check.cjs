'use strict';
// Independent acceptance of production composition using real renderer fixtures.
// Synthetic DOM/controller dimensions only. No browser paint or live operations.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const project=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(project,'index.html'),'utf8');
const reference=JSON.parse(fs.readFileSync(path.join(project,'docs/design-skills-preview-20261026/coverage.json'),'utf8'));
const start=html.indexOf('/* BEGIN_CANONICAL_WORKSPACE */'),end=html.indexOf('/* END_CANONICAL_WORKSPACE */',start);
assert.ok(start>=0&&end>start,'Actual canonical production module must exist');
const moduleSource=html.slice(start,end);
const styleSource=[...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(m=>m[1]).join('\n');
const densityStart=styleSource.indexOf('/* BEGIN_CANONICAL_DENSITY */'),densityEnd=styleSource.indexOf('/* END_CANONICAL_DENSITY */',densityStart);
let checks=0,populated=0;const results=[],reachabilityFailures=[],compositionFailures=[],densityCases=[],referenceTopologyCases=[];
const focusedByDocument=new WeakMap();
function check(name,fn){fn();checks++;results.push(name);console.log('PASS '+name)}
function fixture(file,names){const source=fs.readFileSync(path.join(__dirname,file),'utf8'),at=source.indexOf('(async()=>{');assert.ok(at>0,file);return Function('require','__dirname',source.slice(0,at)+';return{'+names+'};')(require,__dirname)}
function fixtureBefore(file,marker,names){const source=fs.readFileSync(path.join(__dirname,file),'utf8'),at=source.indexOf(marker);assert.ok(at>0,file);return Function('require','__dirname',source.slice(0,at)+';return{'+names+'};')(require,__dirname)}
const tick=()=>new Promise(r=>setImmediate(r));
function install(env){const {ctx,document}=env,frames=[];
 const proto=Object.getPrototypeOf(document.createElement('div'));
 Object.defineProperty(document,'activeElement',{get:()=>focusedByDocument.get(document)||null,configurable:true});proto.focus=function(){focusedByDocument.set(this.ownerDocument,this)};
 proto.getBoundingClientRect=function(){if(this.closest('[hidden]'))return{left:0,top:0,right:0,bottom:0,width:0,height:0};if(this.id==='view')return{left:0,top:0,right:1100,bottom:600,width:1100,height:600};if(this.matches('.canonical-card'))return{left:0,top:0,right:540,bottom:280,width:540,height:280};return{left:10,top:10,right:520,bottom:60,width:510,height:50}};
 Object.defineProperty(proto,'clientWidth',{get(){return this.closest('[hidden]')?0:this.id==='view'?1100:520},configurable:true});
 Object.defineProperty(proto,'clientHeight',{get(){return this.closest('[hidden]')?0:this.id==='view'?600:this.matches('.canonical-card')?280:152},configurable:true});
 ctx.window={addEventListener(){}};ctx.requestAnimationFrame=f=>{frames.push(f);return frames.length};
 ctx.MutationObserver=class{observe(){}disconnect(){}};
 for(const name of ['packWorkspace','mpMeasuredFit','pwaMeasuredFit','cgMeasuredFit','siMeasuredFit','strMeasuredFit','hhMeasuredFit','caFitWorkspace','dhFitWorkspace','renderView'])if(typeof ctx[name]!=='function')ctx[name]=()=>{};
 ctx.liveHomeChart=()=>{};
 if(vm.runInContext('typeof PGS',ctx)==='undefined')ctx.PGS={};
 if(vm.runInContext('typeof PAGEMEM',ctx)==='undefined')ctx.PAGEMEM={};
 vm.runInContext(moduleSource+'\nthis.canonical=CANONICAL_WORKSPACE;',ctx,{timeout:3000});
 env.canonical=ctx.canonical;env.frames=frames;
 env.flush=()=>{let n=0;while(frames.length){assert.ok(++n<100,'Unbounded canonical frame queue');frames.shift()()}};
 return env;
}
function actionNodes(document){return [...document.querySelectorAll('#view button,#view input,#view select,#view textarea')].filter(n=>!n.closest('.mp-nav,.pwa-nav,.cg-nav,.si-nav,.hh-home-nav,.ap-sections,.ca-fit-nav,.mp-fit-nav,.pwa-fit-nav,.cg-fit-nav,.si-fit-nav,.str-fit-nav,.hh-fit-nav,.ap-fit-nav,.card-packing-nav,.str-head,.dh-options-trigger,.ca-choice,.hl-tabs,.dh-summary-nav')).filter(n=>!n.matches('[data-sppg],[data-sppgd],[data-iopg],[data-iopgd],[data-pgto],textarea[data-ca-editor="true"][hidden]'))}
function visible(node){for(let n=node;n;n=n.parentElement){if(n.hidden||n.classList.contains('canonical-omit')||n.style.display==='none')return false;if(n.tagName==='DETAILS'&&!n.open&&!n.querySelector('summary')?.contains(node))return false}return true}
function actionSignature(n){return JSON.stringify([n.tagName,n.id,n.getAttribute('type'),[...n.attributes].filter(a=>a.name.startsWith('data-')&&!a.name.startsWith('data-live-')).map(a=>[a.name,a.value]).sort(),n.id?'':n.textContent.trim()])}
function rowCost(items,indices,cols,gap=12){let width=0,row=0,total=0,rows=0;for(const id of indices){const item=items[id],span=Math.max(1,Math.min(cols,item.span||1)),height=Math.max(24,item.height||120);if(width&&width+span>cols){total+=row;rows++;width=0;row=0}width+=span;row=Math.max(row,height);if(width===cols){total+=row;rows++;width=0;row=0}}if(width){total+=row;rows++}return total+Math.max(0,rows-1)*gap}
function boundaryInvariant(plan,items,cols,budget){const pages=JSON.parse(JSON.stringify(plan(items,cols,budget)));assert.deepEqual(pages.flat(),items.map((_,i)=>i));for(let i=0;i<pages.length;i++){assert.ok(rowCost(items,pages[i],cols)<=budget,'Synthetic page exceeds its height budget');if(i+1<pages.length)assert.ok(rowCost(items,[...pages[i],pages[i+1][0]],cols)>budget,'Next leading card fits the previous page: avoidable page split')}return pages}
async function inspect(env,route,render){env.ctx.tab=route;const view=env.document.getElementById('view');view.dataset.page=route;
 const mounts={cpu:'cpuv',gpu:'gpv',mem:'pg-mem',power:'pg-power',space:'sg',io:'pg-io',procs:'pg-procs',wsl:'pg-wsl',advice:'pg-advice'};
 view.innerHTML=mounts[route]?'<div id="'+mounts[route]+'"></div>':'';await render();await tick();
 const before=actionNodes(env.document),canonical=env.canonical;
 canonical.apply(route);await tick();env.flush();
 check(route+' production data nodes survive canonical composition',()=>{assert.equal(view.dataset.canonicalLive,route);for(const n of before)assert.ok(n.isConnected,route+' lost original input/action '+n.outerHTML.slice(0,110));assert.ok(view.querySelectorAll('[data-live-card]').length>0,route+' has no populated card')});
 check(route+' owns one external section bar and one footer',()=>{assert.equal(env.document.querySelectorAll('#liveSections').length,1);assert.equal(env.document.querySelectorAll('#livePager').length,1);assert.equal(env.document.getElementById('liveSections').parentElement,view.parentElement);assert.equal(env.document.getElementById('livePager').parentElement,view.parentElement);assert.ok(env.document.getElementById('livePageCount').textContent.includes(' من '))});
 const cards=[...view.querySelectorAll('[data-live-card]:not([hidden])')].map(n=>+n.dataset.liveCard);
 if(JSON.stringify(cards)!==JSON.stringify([...cards].sort((a,b)=>a-b))){compositionFailures.push({route,cards});console.error('FAIL '+route+' DOM card order: '+JSON.stringify(cards))}
 else check(route+' cards follow their declared logical order',()=>{});
 check(route+' repeated composition retains action and form ownership',()=>{const owners=before.map(n=>[n,n.closest('form')]);canonical.apply(route);for(const [n,owner]of owners){assert.ok(n.isConnected);assert.equal(n.closest('form'),owner)}});
 const metricGroups=[...view.querySelectorAll('.gp-kpis,.sp-tiles')].map(group=>({group,metrics:[...group.querySelectorAll('.gp-tile,.gt')]})).filter(item=>item.metrics.length>1);
 if(metricGroups.length)check(route+' related metrics share one compact summary rather than stretched individual cards',()=>{for(const {group,metrics}of metricGroups){const owners=new Set(metrics.map(n=>n.closest('[data-live-card]')));assert.equal(owners.size,1,route+' splits one metric group into independent cards');const owner=[...owners][0];assert.ok(owner&&owner.contains(group),route+' metric owner does not contain its summary wrapper');assert.equal(owner.dataset.liveKind,'summary',route+' metric wrapper is not classified for compact full-width summary');for(const metric of metrics)assert.ok(metric!==owner,route+' uses a metric itself as the full card')}});
 const expected=new Map(before.map(n=>[actionSignature(n),n.outerHTML.slice(0,170)])),seen=new Set();
 for(let section=0;section<canonical.profiles[route].length;section++){
  canonical.goSection(section);await tick();env.flush();
  const sectionUnits=canonical.collect(view,route)[section]||[],cap=canonical.capacity(1100,600),sizes=sectionUnits.map(node=>({height:node.getBoundingClientRect().height||180,span:['summary','toolbar','records'].includes(node.dataset.liveKind)?cap.cols:1}));
  densityCases.push({route,section:canonical.profiles[route][section][1],dimensions:{width:1100,height:600},measurement:'Synthetic DOM: canonical-card rectangles are280px, not rendered browser pixels',units:sectionUnits.map((node,i)=>({index:i,kind:node.dataset.liveKind||'content',title:(node.querySelector('h2,h3')?.textContent||node.getAttribute('aria-label')||node.id||node.tagName).trim().slice(0,80),...sizes[i]})),pages:JSON.parse(JSON.stringify(canonical.current(route).pages||[]))});
  for(let page=0;page<30;page++){
   for(const n of actionNodes(env.document))if(visible(n))seen.add(actionSignature(n));
   if(!canonical.goPage(1))break;
  }
 }
 const missing=[...expected].filter(([key])=>!seen.has(key)).map(([,description])=>description);
 if(missing.length){reachabilityFailures.push({route,missing});console.error('FAIL '+route+' unreachable controls: '+JSON.stringify(missing))}
 else check(route+' original action signatures are reachable through canonical sections and pages',()=>{});
 populated++;
}
(async()=>{
 const cpu=install(fixture('frontend_cpu_gpu_check.cjs','ctx,document,cpu,gpu,tick'));
 check('canonical registry matches all19 approved routes and33 explicit section labels',()=>{assert.deepEqual(Object.keys(cpu.canonical.profiles).sort(),reference.routes.map(r=>r.route).sort());let count=0;for(const r of reference.routes){const live=cpu.canonical.profiles[r.route];if(['gpu','procs','io'].includes(r.route)){assert.equal(live.length,1,r.route+' sections are merged into one footer-paged flow');count+=r.subviews.length;continue}assert.deepEqual(Array.from(live,x=>x[1]),r.subviews.map(s=>s.title),r.route);count+=live.length}assert.equal(count,33)});
 check('canonical uses height-budget sequential pages instead of legacy greedy or fixed-count selection',()=>{assert.doesNotMatch(moduleSource,/cardPackingPages\(/);assert.match(moduleSource,/canonicalSequentialPages\(sizes,cap\.cols,budget,12\)/);assert.match(moduleSource,/packWorkspace=function\(\)\{CANONICAL_WORKSPACE\.schedule\(\);return true\}/)});
 check('density CSS contains valid selector starts and overrides uniform card stretching',()=>{assert.ok(!/^\s*\+/m.test(styleSource),'Literal patch markers invalidate CSS selectors');assert.ok(densityStart>=0&&densityEnd>densityStart);const density=styleSource.slice(densityStart,densityEnd);assert.ok(/grid-auto-rows:max-content!important/.test(density));assert.ok(/align-content:start!important/.test(density));assert.ok(/\.canonical-card\{height:auto!important/.test(density));for(const kind of ['summary','toolbar'])assert.ok(new RegExp('data-live-kind="'+kind+'"\\]\\{grid-column:1/-1!important').test(density),kind+' must span the content width')});
 const plan=cpu.canonical.plan;
 const plain=value=>JSON.parse(JSON.stringify(value));
 check('compact summaries toolbar and related content share a single page when their measured rows fit',()=>{assert.deepEqual(plain(plan([{height:80,span:3},{height:44,span:3},...Array.from({length:3},()=>({height:180,span:1}))],3,500)),[[0,1,2,3,4]])});
 check('shorter work areas paginate complete ordered rows without skipping ahead',()=>{assert.deepEqual(plain(plan([{height:80,span:3},{height:44,span:3},...Array.from({length:3},()=>({height:180,span:1}))],3,250)),[[0,1],[2,3,4]]);assert.deepEqual(plain(plan([{height:200,span:3},{height:250,span:3},{height:50,span:3}],3,400)),[[0],[1,2]])});
 check('small useful content is not restricted to the earlier uniform two-row card count',()=>{assert.equal(plan(Array.from({length:12},()=>({height:80,span:1})),2,500)[0].length,10)});
 check('deterministic mixed-width height fixtures retain every item and stay within their row budget',()=>{for(const cols of [1,2,3])for(const budget of [260,400,600]){const items=Array.from({length:30},(_,i)=>({height:40+(i*47)%180,span:1+i%cols})),pages=plain(plan(items,cols,budget));assert.deepEqual(pages.flat(),items.map((_,i)=>i));for(const page of pages){let width=0,row=0,total=0,rows=0;for(const id of page){const item=items[id];if(width&&width+item.span>cols){total+=row;rows++;width=0;row=0}width+=item.span;row=Math.max(row,item.height);if(width===cols){total+=row;rows++;width=0;row=0}}if(width){total+=row;rows++}total+=Math.max(0,rows-1)*12;assert.ok(total<=budget,'Planner exceeded synthetic row budget')}}});
 check('every sequential page boundary is necessary under the same mixed-span height model',()=>{for(const cols of [1,2,3,4])for(const budget of [260,340,500,700])for(let seed=1;seed<=12;seed++){const items=Array.from({length:3+seed*3},(_,i)=>({height:40+(i*47+seed*31)%180,span:1+(i*seed+seed)%cols}));boundaryInvariant(plan,items,cols,budget)}for(const items of [[{height:80,span:3},{height:40,span:3},{height:150,span:1},{height:140,span:1},{height:130,span:1}],[{height:180,span:2},{height:80,span:1},{height:200,span:3},{height:50,span:3}]])boundaryInvariant(plan,items,3,400)});
 check('all33 reference section topologies satisfy the planner boundary invariant with labelled synthetic heights',()=>{for(const route of reference.routes)for(const section of route.subviews){const items=section.panels.map((title,i)=>({height:70+(i*53)%150,span:i===0?3:1})),pages=boundaryInvariant(plan,items,3,500);referenceTopologyCases.push({route:route.route,section:section.title,measurement:'Reference topology stress only; synthetic70–219px heights, not populated production rendering',items:section.panels.map((title,i)=>({title,...items[i]})),pages})}assert.equal(referenceTopologyCases.length,33)});
 await inspect(cpu,'cpu',()=>{cpu.ctx.CPUD=cpu.cpu;cpu.ctx.drawCpu()});
 await inspect(cpu,'gpu',()=>{cpu.ctx.GPD=cpu.gpu;cpu.ctx.drawGpu()});
 const mp=install(fixture('frontend_mem_power_check.cjs','ctx,document,mem,power,tick'));
 await inspect(mp,'mem',()=>mp.ctx.drawMem(mp.mem));
 await inspect(mp,'power',()=>mp.ctx.drawPower(mp.power));
 const si=install(fixture('frontend_space_io_check.cjs','ctx,document,space,io,tick'));
 si.ctx.SP=si.space;si.ctx.SPV={};
 await inspect(si,'space',()=>vm.runInContext('SPPG=1;drawSpace()',si.ctx));
 await inspect(si,'io',()=>{si.ctx.IOD=si.io;si.ctx.drawIo(si.io)});
 const pwa=install(fixture('frontend_procs_wsl_advice_check.cjs','ctx,document,procs,wsl,advice,tick'));
 await inspect(pwa,'procs',()=>pwa.ctx.drawProcs(pwa.procs));
 await inspect(pwa,'wsl',()=>pwa.ctx.drawWsl(pwa.wsl));
 await inspect(pwa,'advice',()=>pwa.ctx.drawAdvice(pwa.advice));
 const str=install(fixture('frontend_startup_tasks_rules_check.cjs','ctx,document,state,startup,tick'));
 for(const route of ['tasks','rules'])await inspect(str,route,()=>{str.ctx.destroyPaged();str.document.getElementById('view').innerHTML=route==='tasks'?str.ctx.viewTasks(str.state):str.ctx.viewRules(str.state);(route==='tasks'?str.ctx.afterTasks:str.ctx.afterRules)(str.state)});
 const control=install(fixtureBefore('frontend_ctrl_alerts_check.js',"document.getElementById('view').innerHTML=ctx.viewCtrl",'ctx,document'));
 await inspect(control,'ctrl',()=>{control.document.getElementById('view').innerHTML=control.ctx.viewCtrl({reaper:{},health:{}});control.ctx.caCtrl()});
 const alertStart=html.indexOf('const AL_FIELDS = ['),alertEnd=html.indexOf('\nasync function alSave()',alertStart);assert.ok(alertStart>0&&alertEnd>alertStart);
 vm.runInContext(html.slice(alertStart,alertEnd),control.ctx);
 const cfg={mem:{ramWarn:80,ramBad:90,commitWarn:80,commitBad:90},cpu:{minCores:2,ignore:['one.exe','two.exe']},disk:{freeWarnGB:30,freeBadGB:10},gpu:{tempWarn:80,tempBad:90}};
 await inspect(control,'alerts',()=>{control.document.getElementById('view').innerHTML='<div id="pg-alerts"></div>';control.ctx.drawAlerts({cfg,log:[{sev:'warn',source:'cpu',ts:0,title:'تنبيه عينة'},{sev:'bad',source:'memory',ts:1,title:'ذاكرة'}]})});
 const appearance=install(fixtureBefore('frontend_appearance_regions_check.cjs','ctx.syncAppearancePage();let count=0','ctx,document'));
 await inspect(appearance,'appearance',()=>appearance.ctx.syncAppearancePage());
 check('alerts hidden canonical textarea remains editable through its visible linked editor',()=>{control.ctx.tab='alerts';control.canonical.goSection(0);const editor=control.document.querySelector('.ca-ignore'),backing=control.document.querySelector('textarea[data-ca-editor="true"]');assert.ok(editor&&backing);const total=control.canonical.collect(control.document.getElementById('view'),'alerts')[0].length;for(let i=0;i<total&&!visible(editor);i++)control.canonical.goPage(1);assert.ok(visible(editor),'Ignore editor is unreachable');editor.value='replacement.exe';assert.equal(typeof editor.oninput,'function');editor.oninput();assert.ok(backing.value.includes('replacement.exe'));control.canonical.apply('alerts');assert.ok(backing.value.includes('replacement.exe'));assert.ok(backing.isConnected)});
 check('dirty control value focus selection and form owner survive same-route apply',()=>{pwa.ctx.tab='procs';pwa.ctx.__fixtureProcs=pwa.procs;pwa.ctx.__assert=assert;try{vm.runInContext(`
 this.__step='drawProcs';document.getElementById('view').innerHTML='<div id="pg-procs"></div>';drawProcs(__fixtureProcs);
 this.__step='first apply';CANONICAL_WORKSPACE.apply('procs');
 this.__step='find input';const dirtyInput=document.querySelector('#bareMin');__assert.ok(dirtyInput);dirtyInput.value='177';dirtyInput.selectionStart=1;dirtyInput.selectionEnd=2;dirtyInput.focus();const dirtyOwner=dirtyInput.closest('form');
 this.__step='second apply';CANONICAL_WORKSPACE.apply('procs');
 this.__step='assert';__assert.ok(document.querySelector('#bareMin')===dirtyInput,'Input identity changed');__assert.equal(dirtyInput.value,'177');__assert.ok(document.activeElement===dirtyInput,'Input lost focus');__assert.ok(dirtyInput.closest('form')===dirtyOwner,'Form ownership changed');__assert.equal(dirtyInput.selectionStart,1);__assert.equal(dirtyInput.selectionEnd,2);
 `,pwa.ctx,{timeout:2000})}catch(error){error.message+=' [stage '+pwa.ctx.__step+']';throw error}});
 assert.deepEqual(compositionFailures,[],'Canonical DOM order disagrees with declared card order');
 assert.deepEqual(reachabilityFailures,[],'Original controls hidden from all canonical sections/pages');
 fs.writeFileSync(path.join(__dirname,'canonical-density-report-20261026.json'),JSON.stringify({sourceSha256:crypto.createHash('sha256').update(html).digest('hex'),checks,populatedRoutes:populated,populatedControlledSections:densityCases.length,browserRendered:false,measurementScope:'Controller/DOM fixtures only; no visual fit or actual scroll dimensions claimed',cases:densityCases,referenceTopologyCases},null,2));
 console.log(`RESULT ${checks} independent canonical checks;${populated} populated original-renderer routes;19 reference routes/33sections. Synthetic controller geometry, no browser paint or real operations.`);
})().catch(error=>{console.error(error);process.exitCode=1});
