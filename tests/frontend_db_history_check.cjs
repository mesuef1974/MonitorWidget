'use strict';
// Owned synthetic DOM. No browser, live HTTP, database, settings or production mutation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const projectRoot=path.join(__dirname,'..');
let parseHTML;try{({parseHTML}=require(process.env.MONITOR_DOM_MODULE||path.join(projectRoot,'tests/dom-runtime/node_modules/linkedom')));}catch(error){console.error('MISSING frontend DOM test dependency. Run npm ci --prefix tests/dom-runtime --ignore-scripts before tests.');process.exit(77);}
const html=fs.readFileSync(path.join(projectRoot,'index.html'),'utf8');
const stage=html.slice(html.indexOf('const DH_STAGE ='),html.indexOf('/* END_DH_STAGE */'));
const {document}=parseHTML('<html><body><main id="view" data-page="dbx"><div id="dg" class="page"></div></main></body></html>');
const proto=Object.getPrototypeOf(document.createElement('div'));
Object.defineProperty(proto,'clientHeight',{get(){return this.classList?.contains('plist')?152:400},configurable:true});
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const entries=Array.from({length:17},(_,n)=>[n,'القيمة المطوّلة 🙂\n'.repeat(450),n%2?null:0]);
const calls=[],read=[];let failure=false;
const ctx=vm.createContext({document,console,UI:{hs:'ev',q:'',sev:'all',hr:'7d'},TIERS:{daily:'يومي'},SRC:{task:'مهمة مجدولة'},tab:'dbx',DBSEL:'table0',DBSIG:'',DBO:{file:'C:\\full\\path\\file.db',bytes:2048,walBytes:0,freeBytes:0,schema:12,tables:Array.from({length:100},(_,n)=>({name:'table'+n,rows:17,cols:[{name:'id',type:'INTEGER',pk:1},{name:'full_text',type:'TEXT'},{name:'nullable',type:'TEXT'}]}))},HI:null,H:null,esc,dbFmt:String,dbxBytes:String,fmtB:v=>String(v)+' B',tdt:String,tfull:String,pad:String,caRead:(text,title)=>read.push({text,title}),viewHistory:()=>'',loadHist:async()=>{},drawDbx(){},evRow(){},runRow(){},drawDb(){},renderStable:(e,s)=>e.innerHTML=s,api:async url=>{calls.push(url);if(failure)throw Error('synthetic');const u=new URL(url,'http://synthetic.invalid'),off=+u.searchParams.get('offset'),lim=+u.searchParams.get('limit');return {total:entries.length,rows:entries.slice(off,off+lim)}},getComputedStyle:()=>({getPropertyValue:()=> '76'}),ResizeObserver:class{observe(){}disconnect(){}},setTimeout:()=>0,clearTimeout(){}});
ctx.window={addEventListener(){}};ctx.requestAnimationFrame=()=>{};ctx.afterHistory=async()=>{};
const a=html.indexOf('const PGS ='),b=html.indexOf('// A wheel over',a);assert.ok(a>0&&b>a,'actual paging source located');
const stableStart=html.indexOf('function renderStable('),stableEnd=html.indexOf('\nfunction toast(',stableStart);
vm.runInContext(html.slice(stableStart,stableEnd),ctx);
const vs=html.indexOf('function viewHistory()'),ve=html.indexOf('\nfunction evRow(',vs);const ss=html.indexOf('const segHTML ='),se=html.indexOf('\nfunction bindSeg(',ss);vm.runInContext(html.slice(ss,se)+'\n'+html.slice(vs,ve),ctx);
vm.runInContext(html.slice(a,b)+'\n'+stage+'\nthis.stage=DH_STAGE;this.pagers=PGS;this.mem=PAGEMEM;',ctx);
let count=0;const check=(label,f)=>{f();count++;console.log('PASS '+label)};
const settle=()=>new Promise(r=>setImmediate(r));const click=el=>el.dispatchEvent(new document.defaultView.Event('click',{bubbles:true}));


(async()=>{
ctx.drawDbx(true);await settle();
check('actual paging engine computes 2 records from synthetic 152px budget',()=>{assert.equal(ctx.pagers.dbr.size,2);assert.equal(document.querySelectorAll('.dh-record').length,2);assert.equal(ctx.pagers.dbr.total,17);});
check('wide tables use complete record reader rather than min-width matrix',()=>{assert.equal(document.querySelectorAll('.dscroll,.dr,.dhd').length,0);assert.ok(document.querySelector('.dh-note').textContent.includes('كل الحقول'));});
click(document.querySelector('.dh-record [data-dh-read]'));
check('full record preserves every field name, type, null and Unicode value',()=>{assert.ok(read.at(-1).text.includes('id [مفتاح] · INTEGER\n0'));assert.ok(read.at(-1).text.includes(entries[0][1]));assert.ok(read.at(-1).text.includes('nullable · TEXT\n0'));});
await ctx.pagers.dbr.goto(8);await settle();
check('last record and explicit numbered page remain reachable',()=>{assert.equal(ctx.pagers.dbr.rows[0][0],16);assert.equal(document.querySelector('#drf [aria-current="page"]').textContent,'9');});
const last=document.querySelector('[data-dh-table="99"]');click(last);await settle();
check('100th table is reachable with selected table and record page reset',()=>{assert.equal(ctx.DBSEL,'table99');assert.equal(ctx.mem.dbr,0);assert.ok(calls.at(-1).includes('table=table99'));});
ctx.DBO.tables[99].cols.push(...Array.from({length:45},(_,n)=>({name:'extra'+n,type:'TEXT'})));entries[0].push(...Array.from({length:45},(_,n)=>'value'+n));ctx.drawDbx(true);await settle();click(document.querySelector('.dh-record [data-dh-read]'));
check('48-column record remains complete without column navigation',()=>{assert.ok(read.at(-1).text.includes('extra44 · TEXT\nvalue44'));assert.equal(document.querySelectorAll('[data-column-page]').length,0);});
failure=true;await ctx.pagers.dbr.paint();
check('remote failure is shown as a failure rather than empty database success',()=>assert.ok(document.querySelector('#drl').textContent.includes('تعذّر تحميل')));failure=false;
const event={ts:123,source:'task',severity:'warn',title:'عنوان',detail:'نص 🙂\n'.repeat(700)};document.getElementById('view').innerHTML=ctx.evRow(event);click(document.querySelector('[data-dh-read]'));
check('history event full detail preserves untruncated source',()=>assert.ok(read.at(-1).text.includes(event.detail)));
check('severity stays visibly marked on the event row',()=>assert.ok(document.querySelector('.dh-record.sv-warn')));
const run={ts:123,tier:'daily',dry:false,rules_run:8,rules_skip:2,rules_failed:1,files:40,bytes_freed:1024,duration:3.5,custom:'extra metadata'};document.getElementById('view').innerHTML=ctx.runRow(run);click(document.querySelector('[data-dh-read]'));
check('cleanup reader includes all eight former columns and extra metadata',()=>{for(const s of ['8','2','1','40','1024 B','3.5','extra metadata'])assert.ok(read.at(-1).text.includes(s));});
ctx.HI={sqlite:'3.test',bytes:2048,first:1,last:2,schema:12,path:'C:\\'+('a'.repeat(400))+'\\file.db',tables:{samples:10,events:20,other:9}};document.getElementById('view').innerHTML='<div id="dbg"></div>';ctx.drawDb();await settle();
check('all database-info fields and all table counts are paged',()=>{assert.equal(ctx.pagers.hdbinfo.total,11);assert.ok(ctx.pagers.hdbinfo.items.some(x=>x[0]==='جدول other'));assert.equal(ctx.pagers.hdbinfo.items.find(x=>x[0]==='المسار الكامل')[1],ctx.HI.path);});
const view=document.getElementById('view');view.dataset.page='history';ctx.UI.hs='sum';view.innerHTML='<div class="page"><div id="hv">الحالة كاملة</div><div id="kpis"><div>عدد 100</div><div>معلومة كاملة</div></div><div class="hsplit"><section class="hchart"></section><section class="dcard"></section></div></div>';ctx.drawHist=()=>{};ctx.stage.summaryFit();click(document.querySelector('[data-dh-summary="daily"]'));
check('history chart and daily groups remain explicitly reachable',()=>{assert.equal(document.querySelector('.hchart').hidden,true);assert.equal(document.querySelector('.dcard').hidden,false);click(document.querySelector('[data-dh-summary="chart"]'));assert.equal(document.querySelector('.hchart').hidden,false);});click(document.querySelector('[data-dh-read]'));
check('complete KPI summary remains readable after removing crowded KPI strip',()=>{assert.ok(read.at(-1).text.includes('الحالة كاملة'));assert.ok(read.at(-1).text.includes('عدد 100'));assert.equal(read.at(-1).title,'إحصاءات الفترة كاملة');});
const chartButton=document.querySelector('[data-dh-summary="chart"]'),summaryReader=document.getElementById('dhSummaryRead');
let syntheticFocus=chartButton;Object.defineProperty(document,'activeElement',{get:()=>syntheticFocus,configurable:true});ctx.stage.summaryFit();
check('real renderStable preserves nav button identity and synthetic focus on repeat',()=>{assert.equal(document.querySelector('[data-dh-summary="chart"]'),chartButton);assert.equal(document.activeElement,chartButton);});
syntheticFocus=summaryReader;document.querySelector('#kpis').firstElementChild.textContent='updated KPI';ctx.stage.summaryFit();
check('KPI updates preserve focused reader control while refreshing full body',()=>{assert.equal(document.getElementById('dhSummaryRead'),summaryReader);assert.equal(document.activeElement,summaryReader);click(summaryReader);assert.ok(read.at(-1).text.includes('updated KPI'));});
const hostile='<img src=x onerror="globalThis.compromised=true">'+('م🙂'.repeat(5000))+'\u202E';document.getElementById('view').innerHTML=ctx.stage.row(hostile,'summary','every original field');click(document.querySelector('[data-dh-read]'));
check('10000-character hostile title is short inert text with complete original in reader body',()=>{assert.ok(Array.from(read.at(-1).title).length<=57);assert.ok(!read.at(-1).title.includes('\u202E'));assert.ok(read.at(-1).text.includes(hostile));assert.ok(read.at(-1).text.includes('every original field'));assert.equal(document.querySelectorAll('img,script').length,0);assert.equal(ctx.compromised,undefined);});
check('stage changes only db/history view functions and no configuration writes',()=>{assert.ok(!/fetch\(|localStorage|writeFile|\/api\/(?:cleanup|appearance|shutdown)/.test(stage));});
const controlText='line\r\nzero\u0000\t🙂';document.getElementById('view').innerHTML=ctx.stage.row('control characters','preview',controlText);click(document.querySelector('[data-dh-read]'));check('reader preserves raw CR LF NUL tab and Unicode',()=>assert.ok(read.at(-1).text.includes(controlText)));
console.log('RESULT '+count+' isolated DOM behavior checks; synthetic dimensions only, not browser geometry.');
})().catch(e=>{console.error(e);process.exitCode=1});
