function sourceDeclaration(text,name){const found=new RegExp('^(?:async\\s+)?function\\s+'+name+'\\s*\\(','m').exec(text);if(!found)throw Error(name);for(let end=text.indexOf('}',found.index);end>=0;end=text.indexOf('}',end+1)){const code=text.slice(found.index,end+1);try{new vm.Script(code);return{start:found.index,end:end+1,code}}catch(e){if(!(e instanceof SyntaxError))throw e}}throw Error(name)}
'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const ROOT=require('path').join(__dirname,'..');const {parseHTML}=require(ROOT+'/tests/dom-runtime/node_modules/linkedom');
const source=fs.readFileSync(ROOT+'/index.html','utf8');
const {document}=parseHTML('<html><body><dialog id="dlg"><h2 id="dlgTitle">تأكيد العملية</h2><p id="dlgMsg"></p><div><button id="dlgOk">تأكيد</button><button id="dlgNo">إلغاء</button></div></dialog></body></html>');
const dialog=document.getElementById('dlg'),body=document.getElementById('dlgMsg'),listeners=new Map(),frames=[];
const lines=()=>String(body.textContent).split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil(Array.from(line).length/30)),0);
Object.defineProperty(body,'clientWidth',{get:()=>300});Object.defineProperty(body,'clientHeight',{get:()=>body.style.height==='auto'?Math.min(280,lines()*20):parseFloat(body.style.height)||0});Object.defineProperty(body,'scrollHeight',{get:()=>lines()*20});Object.defineProperty(body,'scrollWidth',{get:()=>300});
dialog.showModal=()=>dialog.open=true;dialog.close=()=>{dialog.open=false;dialog.onclose?.();};dialog.getBoundingClientRect=()=>({height:120+(parseFloat(body.style.height)||0)});
const window={innerHeight:640,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name,fn)=>{if(listeners.get(name)===fn)listeners.delete(name);}};
const ctx=vm.createContext({document,window,requestAnimationFrame:fn=>frames.push(fn)});
vm.runInContext(source.slice(source.indexOf('function caChunks('),source.indexOf('function caRead(')),ctx);
vm.runInContext(sourceDeclaration(source,'confirmDlg').code,ctx);
const next=()=>[...dialog.querySelectorAll('.dialog-page-nav button')].find(n=>n.textContent==='التالي');let count=0;
const check=(name,fn)=>{fn();count++;console.log('PASS '+name);};
(async()=>{
 let pending=ctx.confirmDlg('هل تريد المتابعة؟');check('short message stays compact and confirmation remains an explicit original action',()=>{assert.equal(body.textContent,'هل تريد المتابعة؟');assert.equal(body.style.height,'auto');assert.ok(dialog.querySelector('.dialog-page-nav').hidden);assert.equal(document.getElementById('dlgOk').textContent,'تأكيد');});document.getElementById('dlgOk').onclick();assert.equal(await pending,true);check('accepted dialog resolves true and removes resize listener',()=>assert.equal(listeners.size,0));
 pending=ctx.confirmDlg('إلغاء');document.getElementById('dlgNo').onclick();assert.equal(await pending,false);check('cancel resolves false without any second approval step',()=>assert.equal(dialog.open,false));
 pending=ctx.confirmDlg('Escape');dialog.open=false;dialog.onclose();assert.equal(await pending,false);check('native Escape/close cancels and cleans up',()=>assert.equal(listeners.size,0));
 const full=Array.from({length:120},(_,i)=>'سطر '+i+' 🙂\r\n\u0000\tتفاصيل أصلية').join('\n');pending=ctx.confirmDlg(full);const seen=[];do{seen.push(body.textContent);if(next().disabled)break;next().onclick();}while(true);
 check('long multiline Unicode message remains complete across measured pages',()=>assert.equal(seen.join(''),full));check('each displayed full-text page stays inside the owned synthetic text budget',()=>{assert.ok(seen.length>1);assert.ok(body.scrollHeight<=body.clientHeight+1);});document.getElementById('dlgNo').onclick();await pending;
 const unique=Array.from({length:1000},(_,i)=>String.fromCodePoint(0x4e00+i)).join('');pending=ctx.confirmDlg(unique);let position=body.textContent.length;next().onclick();position+=body.textContent.length;next().onclick();window.innerHeight=360;
 const resize=listeners.get('resize');resize();resize();resize();check('resize events coalesce to a single measurement',()=>assert.equal(frames.length,1));frames.shift()();const start=unique.indexOf(body.textContent);check('resize preserves the original text position within its new page',()=>assert.ok(start<=position&&start+body.textContent.length>position));
 const before=body.textContent;assert.equal(await ctx.confirmDlg('عملية ثانية'),false);check('concurrent call cannot overwrite or accept the already-open operation',()=>assert.equal(body.textContent,before));document.getElementById('dlgOk').onclick();assert.equal(await pending,true);
 pending=ctx.confirmDlg('<img src=x onerror=alert(1)>');check('message content remains inert text',()=>assert.equal(dialog.querySelectorAll('img,script').length,0));document.getElementById('dlgNo').onclick();await pending;
 check('every close path leaves no active resize handler',()=>assert.equal(listeners.size,0));
 console.log('RESULT '+count+' owned DOM confirmation cases; synthetic line geometry, no browser/API/action execution.');
})().catch(error=>{console.error(error);process.exitCode=1});
