'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
let parseHTML;try{({parseHTML}=require(process.env.MONITOR_DOM_MODULE||'./dom-runtime/node_modules/linkedom'));}catch(error){console.error('MISSING frontend DOM test dependency. Run npm ci --prefix tests/dom-runtime --ignore-scripts before tests. No installation is performed by this test.');process.exit(77);}
const src=fs.readFileSync(require('path').join(__dirname,'..','index.html'),'utf8'),helper=src.slice(src.indexOf('/* Scoped functional navigation:'),src.indexOf('/* END_CA_NAV */'));
const {document}=parseHTML('<html><body><div id="view" data-page="ctrl"></div></body></html>');
// Linkedom omits the standard select.value setter. Supply only that platform contract.
const selectProto=Object.getPrototypeOf(document.createElement('select'));
Object.defineProperty(selectProto,'value',{get(){return this.querySelector('option[selected]')?.value||this.querySelector('option')?.value||''},set(v){this.querySelectorAll('option').forEach(o=>o.toggleAttribute('selected',o.value===v));},configurable:true});
const ctx=vm.createContext({document,TIERS:{daily:'يومي',weekly:'أسبوعي',monthly:'شهري',quarterly:'ربع سنوي'},M:{trim:{},tiers:{}},MT:{},WG:{running:true},esc:String,ago:String,fmtT:String,B:String,trimResult:()=>'',renderStable:(root,markup)=>root.innerHTML=markup,pgHead:()=>'',pgEmpty:t=>'<div>'+t+'</div>',PGSPEC:null,pgFitAll(){},pgBind(){},console});
const block=(a,b)=>src.slice(src.indexOf(a),src.indexOf(b,src.indexOf(a)));
ctx.window={addEventListener(){},removeEventListener(){}};ctx.requestAnimationFrame=()=>{};
vm.runInContext(helper+'\nthis.state=CA_UI;',ctx);
vm.runInContext(block('function widgetBlock()', '\n/* ---------------- render ---------------- */'),ctx);
let n=0;const check=(name,fn)=>{fn();n++;console.log('PASS '+name);};
const visible=el=>{for(let p=el;p;p=p.parentElement)if(p.hidden)return false;return true;};
const click=el=>el.onclick({preventDefault(){},stopPropagation(){}});
ctx.ResizeObserver=class{observe(){}disconnect(){}};ctx.setTimeout=()=>0;ctx.clearTimeout=()=>{};ctx.getComputedStyle=()=>({getPropertyValue:()=> '76'});vm.runInContext(block('const PGS =','// A wheel over'),ctx);
document.getElementById('view').innerHTML=ctx.viewCtrl({reaper:{},health:{}});ctx.caCtrl();
check('all existing Control actions survive grouping',()=>{for(const id of ['widget','boost','trimauto','maint','reaper','cleanup','tier'])assert.ok(document.querySelector('[data-act="'+id+'"]'));});
document.getElementById('view').dataset.page='alerts';document.getElementById('view').innerHTML='<div id="pg-alerts"></div>';
vm.runInContext(block('const AL_FIELDS = [','\nasync function alSave()'),ctx);
const cfg={mem:{ramWarn:80,ramBad:90,commitWarn:80,commitBad:90},cpu:{minCores:2,ignore:['one.exe','two.exe']},disk:{freeWarnGB:30,freeBadGB:10},gpu:{tempWarn:80,tempBad:90}};
ctx.drawAlerts({cfg,log:[{sev:'warn',source:'cpu',ts:0,title:'x'.repeat(700)},{sev:'bad',source:'memory',ts:1,title:'second'}]});
check('all numeric inputs remain present after category navigation',()=>{const before=[...document.querySelectorAll('input[type="number"]')].map(x=>[x.name,x.value]);const chooser=document.querySelector('#alForm>.ca-choice');for(const value of ['mem','cpu','disk','gpu','ign']){chooser.value=value;chooser.onchange();assert.equal([...document.querySelectorAll('fieldset.al-g')].filter(visible).length,1);}assert.deepEqual([...document.querySelectorAll('input[type="number"]')].map(x=>[x.name,x.value]),before);});
check('ignore navigation preserves all original names and updates only selected entry',()=>{const original=document.querySelector('textarea'),editor=document.querySelector('.ca-ignore');assert.equal(original.value,'one.exe\ntwo.exe');editor.value='changed.exe';editor.oninput();assert.equal(original.value,'changed.exe\ntwo.exe');const next=[...editor.nextElementSibling.querySelectorAll('button')].find(b=>b.textContent==='التالي');click(next);assert.equal(editor.value,'two.exe');});
check('multiline paste is retained in canonical full textarea',()=>{const editor=document.querySelector('.ca-ignore');editor.selectionStart=0;editor.selectionEnd=editor.value.length;editor.onpaste({clipboardData:{getData:()=> 'a.exe\nb.exe\nc.exe'},preventDefault(){}});assert.equal(document.querySelector('textarea').value,'changed.exe\na.exe\nb.exe\nc.exe');assert.deepEqual(JSON.parse(JSON.stringify(ctx.alRead().cpu.ignore)),['changed.exe','a.exe','b.exe','c.exe']);});
check('server refresh cannot overwrite dirty values after focus leaves form',()=>{const form=document.getElementById('alForm'),field=form.querySelector('input[type="number"]');field.value='87';form.dataset.caDirty='true';ctx.drawAlerts({cfg,log:[]});assert.equal(document.getElementById('alForm'),form);assert.equal(field.value,'87');assert.equal(document.querySelector('textarea').value,'changed.exe\na.exe\nb.exe\nc.exe');});
check('global Refresh renderView retains the dirty form instead of mounting a fresh view',()=>{const form=document.getElementById('alForm');Object.assign(ctx,{tab:'alerts',destroyPaged(){},clearInterval(){},healthTimer:0,spaceTimer:0,ioTimer:0,dbxTimer:0,cpuTimer:0,gpTimer:0,PERF:[],afterAlerts(){}});vm.runInContext(block('function renderView()', '\nfunction setLive('),ctx);ctx.renderView();assert.equal(document.getElementById('alForm'),form);assert.equal(form.querySelector('input[type="number"]').value,'87');});
vm.runInContext(block('async function alSave()', '\n/* ---------- recommendations'),ctx);
ctx.PGD={alerts:{defaults:cfg}};ctx.toast=()=>{};ctx.loadMini=()=>{};
(async()=>{
 const form=document.getElementById('alForm');ctx.api=async()=>({cfg});form.dataset.caDirty='true';await ctx.alSave();check('successful save clears dirty state only after accepted response',()=>assert.equal(form.dataset.caDirty,'false'));
 form.dataset.caDirty='true';ctx.api=async()=>{throw Error('rejected')};await ctx.alSave();check('rejected save retains dirty state and exposes failure',()=>{assert.equal(form.dataset.caDirty,'true');assert.equal(document.getElementById('alMsg').textContent,'rejected')});
 let release;ctx.api=()=>new Promise(resolve=>release=resolve);const pending=ctx.alSave();const input=form.querySelector('input[type="number"]');input.value='88';release({cfg});await pending;check('edits made during an in-flight save are not marked saved',()=>assert.equal(form.dataset.caDirty,'true'));
 ctx.alReset();check('reset updates canonical list and visible editor without submitting',()=>{assert.equal(document.querySelector('textarea').value,'');assert.equal(document.querySelector('textarea').value,'');assert.equal(form.dataset.caDirty,'true')});
 let payload;ctx.api=async(path,body)=>{payload=body;return {cfg:body}};ctx.pgRedraw=()=>{};form.querySelector('input[type="number"]').value='88';await ctx.caIgnoreAlert('backup.exe');check('Ignore from log merges into draft without submitting unsaved thresholds',()=>{assert.equal(payload.mem.ramWarn,80);assert.equal(ctx.alRead().mem.ramWarn,88);assert.deepEqual(JSON.parse(JSON.stringify(ctx.alRead().cpu.ignore)),['backup.exe']);assert.equal(document.querySelector('.ca-ignore').value,'backup.exe')});
 console.log('RESULT '+n+' isolated DOM behavior checks; no rendering/geometry/browser claim');
})().catch(e=>{console.error(e);process.exitCode=1});
