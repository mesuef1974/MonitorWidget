"use strict";
// Actual appearance functions in a deterministic non-browser harness.
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict"),path=require("node:path");
const html=fs.readFileSync(path.join(__dirname,"..","index.html"),"utf8");
const script=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];new vm.Script(script);
const block=html.slice(html.indexOf("const AP_MODES="),html.indexOf("/* Scoped functional navigation:"));
function env(systemDark=false){
 const applied=[],calls=[],intervals=[],stored={};let handler=async()=>({schemaVersion:1,revision:0,mode:"auto",palette:"teal"});
 const root={dataset:{},style:{}},themeButton={setAttribute(k,v){this[k]=v}},mediaHandlers=[];
 const c=vm.createContext({applied,document:{hidden:false,documentElement:root,getElementById:id=>id==="theme"?themeButton:null,addEventListener:()=>{}},localStorage:{setItem:(k,v)=>stored[k]=v},tab:"home",renderStable:()=>{},esc:s=>String(s),setTab:()=>{},toast:()=>{},setInterval:(fn,ms)=>intervals.push({fn,ms}),matchMedia:()=>({matches:systemDark,addEventListener:(name,fn)=>mediaHandlers.push(fn)}),drawChart:()=>{},metrics:()=>({}),drawHist:()=>{},api:async(p,b)=>{calls.push({p,b});return handler(p,b)}});
 const actualTheme=html.slice(html.indexOf("function applyTheme(t)"),html.indexOf("/* Shared, server-confirmed appearance."));
 // This source slice includes scoped layout helpers. Register events without claiming layout.
 c.window={addEventListener:()=>{},removeEventListener:()=>{}};c.requestAnimationFrame=()=>{};
 // API/CAS unit tests use an identity layout adapter; the real adapter has its own DOM suite.
 c.AP_REGION_STAGE={shape:markup=>markup};
 vm.runInContext(actualTheme+"\nconst originalApplyTheme=applyTheme;applyTheme=t=>{applied.push(t);originalApplyTheme(t);};\n"+block+"\nthis.ap={validAppearance,acceptAppearance,pollAppearance,saveAppearance,viewAppearance,get:()=>AP,status:()=>appearanceMessage,error:()=>appearanceError};",c);
 return{c,ap:c.ap,root,themeButton,mediaHandlers,applied,calls,intervals,stored,setHandler:f=>handler=f};
}
let checks=0;function check(name,fn){fn();checks++;console.log("PASS "+name)}
(async()=>{
 let e=env();
 check("syntax and required fields reject malformed settings",()=>{assert.equal(e.ap.validAppearance({schemaVersion:1,revision:true,mode:"auto",palette:"teal"}),false);assert.equal(e.ap.validAppearance({schemaVersion:2,revision:1,mode:"auto",palette:"teal"}),false);assert.equal(e.ap.validAppearance({schemaVersion:1,revision:1,mode:"neon",palette:"teal"}),false)});
 await e.ap.pollAppearance();
 check("confirmed settings apply palette and follow-system mode",()=>{assert.equal(e.root.dataset.palette,"teal");assert.equal(e.applied.at(-1),"auto");assert.equal(e.ap.get().revision,0)});
 check("older settings cannot overwrite a newer confirmed revision",()=>{e.ap.acceptAppearance({schemaVersion:1,revision:4,mode:"light",palette:"graphite"});assert.equal(e.ap.acceptAppearance({schemaVersion:1,revision:3,mode:"dark",palette:"blue"}),false);assert.equal(e.root.dataset.palette,"graphite")});
 check("identical poll does not repeatedly redraw or reapply the theme",()=>{const n=e.applied.length;e.ap.acceptAppearance({schemaVersion:1,revision:4,mode:"light",palette:"graphite"});assert.equal(e.applied.length,n)});
 e.setHandler(async(p,b)=>({...b,schemaVersion:1,revision:5}));
 await e.ap.saveAppearance({palette:"violet"});
 check("save uses confirmed revision and applies only confirmed result",()=>{const call=e.calls.at(-1);assert.equal(call.b.revision,4);assert.equal(call.b.mode,"light");assert.equal(e.ap.get().palette,"violet");assert.match(e.ap.status(),/حُفظ/)});
 e.setHandler(async()=>{throw new Error("offline")});
 await e.ap.saveAppearance({mode:"dark"});
 check("failed save leaves the last confirmed appearance and shows failure",()=>{assert.equal(e.ap.get().mode,"light");assert.equal(e.root.dataset.palette,"violet");assert.equal(e.ap.error(),true);assert.match(e.ap.status(),/تعذّر حفظ/)});
 e.setHandler(async()=>({schemaVersion:1,revision:5,mode:"light",palette:"violet"}));await e.ap.pollAppearance(true);
 check("successful reconnection clears transport error",()=>assert.equal(e.ap.error(),false));
 e.setHandler(async(p,b)=>{if(b){const error=new Error("appearance changed");error.httpStatus=409;throw error}return{schemaVersion:1,revision:8,mode:"dark",palette:"blue"}});
 const before=e.calls.length;await e.ap.saveAppearance({palette:"teal"});
 check("CAS conflict reloads once and never replays a mutation",()=>{const calls=e.calls.slice(before);assert.equal(calls.filter(x=>x.b).length,1);assert.equal(calls.filter(x=>!x.b).length,1);assert.equal(e.ap.get().revision,8);assert.equal(e.ap.get().palette,"blue");assert.match(e.ap.status(),/نافذة أخرى/)});
 e=env();let resolvePoll;let saveCount=0;
 e.ap.acceptAppearance({schemaVersion:1,revision:1,mode:"light",palette:"teal"});
 e.setHandler((p,b)=>b?(saveCount++,Promise.resolve({schemaVersion:1,revision:2,mode:"dark",palette:"graphite"})):new Promise(r=>resolvePoll=r));
 const pending=e.ap.pollAppearance();await e.ap.saveAppearance({mode:"dark",palette:"graphite"});resolvePoll({schemaVersion:1,revision:1,mode:"light",palette:"teal"});await pending;
 check("read started before save cannot overwrite the saved result",()=>{assert.equal(e.ap.get().revision,2);assert.equal(e.root.dataset.palette,"graphite");assert.equal(saveCount,1)});
 e=env();let resolve; e.setHandler(()=>new Promise(r=>resolve=r));const one=e.ap.pollAppearance(),two=e.ap.pollAppearance();resolve({schemaVersion:1,revision:0,mode:"auto",palette:"teal"});await Promise.all([one,two]);
 check("appearance polling is single-flight and independent of metric polling mode",()=>{assert.equal(e.calls.length,1);assert.ok(e.intervals.some(i=>i.ms===1000));assert.ok(!block.slice(block.indexOf('async function pollAppearance'),block.indexOf('async function saveAppearance')).includes('allowAutomatic'))});
 e=env();e.setHandler(async(p)=>{if(p==="/api/appearance"){const error=new Error("not found");error.httpStatus=404;throw error}return{theme:"light"}});await e.ap.pollAppearance();
 check("old-server fallback reads legacy mode without claiming shared save readiness",()=>{assert.equal(e.ap.get(),null);assert.equal(e.applied.at(-1),"light");assert.match(e.ap.status(),/إصدار الخادم المحدث/);assert.match(e.ap.viewAppearance(),/disabled/)});
 e=env();e.setHandler(async()=>({schemaVersion:1,revision:0,mode:"auto",palette:"teal",recoveryRequired:true}));await e.ap.pollAppearance();
 check("corrupt or unsupported stored settings display fallback with saving disabled",()=>{assert.equal(e.ap.get(),null);assert.equal(e.ap.validAppearance({schemaVersion:1,revision:0,mode:"auto",palette:"teal",recoveryRequired:true}),false);assert.equal(e.root.dataset.palette,"teal");assert.match(e.ap.status(),/التعديل متوقف/);assert.match(e.ap.viewAppearance(),/disabled/)});
 e=env();e.ap.acceptAppearance({schemaVersion:1,revision:3,mode:"light",palette:"blue"});e.setHandler(async()=>{const error=new Error("appearance recovery required");error.httpStatus=409;error.serverCode="appearance recovery required";throw error});await e.ap.saveAppearance({palette:"violet"});
 check("write recovery failure is distinct from a concurrent-window conflict",()=>{assert.equal(e.ap.get(),null);assert.match(e.ap.status(),/إصلاح الإعدادات/);assert.doesNotMatch(e.ap.status(),/نافذة أخرى|حُفظ/);assert.match(e.ap.viewAppearance(),/disabled/)});
 check("all four palettes, native mode label and marked example values are rendered",()=>{for(const p of ["teal","blue","violet","graphite"])assert.ok(e.ap.viewAppearance().includes(`data-ap-palette="${p}"`));assert.match(e.ap.viewAppearance(),/label class="appearance-field"/);assert.match(e.ap.viewAppearance(),/قيم توضيحية فقط/)});
 check("final file remains LF and every existing toolbar control remains unique",()=>{assert.equal(html.includes("\r"),false);for(const id of ['auto','refresh','muteImportant','showWidget','boost','boostMode','workspaceTools','theme'])assert.equal((html.match(new RegExp('id="'+id+'"','g'))||[]).length,1)});
 for(const [initialMode,systemDark,expected] of [["dark",false,"light"],["light",true,"dark"],["auto",true,"light"],["auto",false,"dark"]]){
  e=env(systemDark);e.ap.acceptAppearance({schemaVersion:1,revision:1,mode:initialMode,palette:"graphite"});e.setHandler(async(p,b)=>({schemaVersion:1,revision:2,mode:b.mode,palette:b.palette}));await e.themeButton.onclick();
  check(`actual moon-button handler toggles ${initialMode} / systemDark=${systemDark} to ${expected}`,()=>{assert.equal(e.ap.get().mode,expected);assert.equal(e.root.dataset.resolvedTheme,expected);assert.equal(e.root.style.colorScheme,expected);assert.equal(e.root.dataset.palette,"graphite");assert.equal(e.calls.at(-1).b.revision,1)});
 }
 check("explicit light/dark token rules do not require light-dark() browser support",()=>{const css=html.match(/<style id="workspace-design-v2">([\s\S]*?)<\/style>/)[1];assert.ok(!css.includes("light-dark("));for(const palette of ['blue','violet','graphite'])assert.ok(css.includes(`:root[data-resolved-theme="dark"][data-palette="${palette}"]`));assert.match(css,/:root\[data-resolved-theme="dark"\]\{--paper:#101f1d;/)});
 e=env(false);e.ap.acceptAppearance({schemaVersion:1,revision:0,mode:"auto",palette:"teal"});e.c.matchMedia=()=>({matches:true});e.mediaHandlers.at(-1)();
 check("system change recomputes explicit colors in auto mode",()=>{assert.equal(e.ap.get().mode,"auto");assert.equal(e.root.dataset.resolvedTheme,"dark");assert.equal(e.root.style.colorScheme,"dark")});
 e=env(false);e.ap.acceptAppearance({schemaVersion:1,revision:0,mode:"light",palette:"teal"});e.c.matchMedia=()=>({matches:true});e.mediaHandlers.at(-1)();
 check("system change does not override manual light mode",()=>assert.equal(e.root.dataset.resolvedTheme,"light"));
 console.log(`RESULT ${checks} appearance checks passed (no browser rendering verified)`);
})().catch(e=>{console.error(e);process.exitCode=1});
