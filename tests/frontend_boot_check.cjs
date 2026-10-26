'use strict';
// Owned DOM only. Every network/timer/window API is a synthetic stub.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const ROOT=require('path').resolve(__dirname,'..');
let parseHTML;try{({parseHTML}=require(ROOT+'/tests/dom-runtime/node_modules/linkedom'));}catch(error){console.error('MISSING frontend DOM dependency. Run npm ci --prefix tests/dom-runtime --ignore-scripts before tests.');process.exit(77);}
const html=fs.readFileSync(ROOT+'/index.html','utf8'),scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
assert.equal(scripts.length,1);
const failures=[];process.on('unhandledRejection',error=>{failures.push(error);console.error('Unhandled synthetic bootstrap failure:',error);process.exitCode=1;});
async function boot(route){
const {document}=parseHTML(html),listeners=[],intervals=[],timers=new Map(),frames=[],requests=[];let id=0;
// Supply the browser's standard select.value setter, absent in Linkedom.
Object.defineProperty(Object.getPrototypeOf(document.createElement('select')),'value',{get(){return this.querySelector('option[selected]')?.value||this.querySelector('option')?.value||''},set(value){this.querySelectorAll('option').forEach(option=>option.toggleAttribute('selected',option.value===value));},configurable:true});
const media={matches:false,addEventListener(){},removeEventListener(){}};
const style={getPropertyValue:()=>'',overflowY:'visible',overflowX:'visible',fontSize:'14px'};
const storage=new Map();
const ctx={document,console,URL,URLSearchParams,AbortController,AbortSignal,Headers,Request,Response,DOMException,performance:{now:()=>0},location:{href:'http://synthetic.invalid/',search:'',hash:'#'+route,origin:'http://synthetic.invalid',pathname:'/'},history:{replaceState(){}},navigator:{clipboard:{writeText:async()=>{throw Error('Clipboard blocked in smoke fixture');}}},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},matchMedia:()=>media,getComputedStyle:()=>style,requestAnimationFrame:fn=>{frames.push(fn);return ++id;},cancelAnimationFrame(){},setInterval:(fn,ms)=>{intervals.push({fn,ms});return ++id;},clearInterval(){},setTimeout:(fn,ms)=>{timers.set(++id,{fn,ms});return id;},clearTimeout:handle=>timers.delete(handle),ResizeObserver:class{observe(){}disconnect(){}unobserve(){}},MutationObserver:class{observe(){}disconnect(){}},fetch:async(url,options)=>{requests.push({url:String(url),method:options?.method||'GET'});return {ok:false,status:503,json:async()=>({error:'synthetic offline'}),text:async()=>''};},innerWidth:1280,innerHeight:800,devicePixelRatio:1,Event:document.defaultView.Event};
ctx.window=ctx;ctx.addEventListener=(name,fn)=>listeners.push([name,fn]);ctx.removeEventListener=()=>{};ctx.window.scrollTo=()=>{};
document.fonts={ready:Promise.resolve(),addEventListener(){}};
document.createElement('dialog').constructor.prototype.showModal=function(){this.open=true;};
document.createElement('dialog').constructor.prototype.close=function(){this.open=false;};
vm.createContext(ctx);

 vm.runInContext(scripts[0],ctx,{timeout:1500,filename:'actual-index-inline.js'});
 for(let tick=0;tick<12;tick++)await Promise.resolve();
 // The document-ready path is part of the real bootstrap when present.
 document.dispatchEvent(new document.defaultView.Event('DOMContentLoaded'));
 for(let tick=0;tick<12;tick++)await Promise.resolve();
 await new Promise(setImmediate);await new Promise(setImmediate);
 assert.ok(document.getElementById('view'));assert.equal(document.getElementById('view').dataset.page,route,'Deep link did not select its own page');assert.ok(document.querySelectorAll('#rail .nav').length>=13,'Original navigation destinations were lost');
 assert.equal(vm.runInContext('typeof caRead',ctx),'function','The shared record/text reader is missing');
 assert.ok(document.getElementById('railTabs'));
 assert.ok(requests.every(r=>r.method==='GET'),'Bootstrap attempted a configuration/action request');
 assert.equal(failures.length,0,String(failures[0]));

 return {route,reads:requests.length};
}
(async()=>{
 const routes=['home','health','alerts','advice','cpu','gpu','mem','procs','wsl','power','space','io','startup','tasks','rules','history','dbx','ctrl','appearance'];
 const results=[];
 for(const route of routes){const result=await boot(route);assert.equal(failures.length,0,'Unhandled bootstrap rejection');results.push(result);}
 console.log('PASS full actual inline script boots all19 page deep links against owned DOM/offline network stubs, without uncaught or unhandled initialization errors.');
 console.log('PASS original navigation retained in each boot; '+results.reduce((n,r)=>n+r.reads,0)+' synthetic GETs; no real HTTP, timers, clipboard or window operations.');
 console.log('No browser layout, paint or live-data coverage is claimed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
