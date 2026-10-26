function sourceDeclaration(text,name){const expression=new RegExp('^(?:async\\s+)?function\\s+'+name+'\\s*\\(','m'),found=expression.exec(text);if(!found)return null;const start=found.index;for(let end=text.indexOf('}',start);end>=0;end=text.indexOf('}',end+1)){const code=text.slice(start,end+1);try{new vm.Script(code);return{start,end:end+1,code};}catch(error){if(!(error instanceof SyntaxError))throw error;}}throw Error('Cannot parse declaration '+name);}
'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const ROOT=require('path').join(__dirname,'..');
const {parseHTML}=require(ROOT+'/tests/dom-runtime/node_modules/linkedom');
const source=fs.readFileSync(ROOT+'/index.html','utf8');
const {document}=parseHTML('<html><body><div id="view" data-page="appearance"></div></body></html>');
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const ctx={document,tab:'appearance',AP:{mode:'auto',palette:'teal'},appearanceSaving:false,appearanceError:false,appearanceMessage:'محفوظ',esc};
ctx.api=()=>{throw Error('Layout cannot invoke the API');};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function renderStable('),source.indexOf('function toast(')),ctx);
vm.runInContext(source.slice(source.indexOf('function viewAppearance('),source.indexOf('function syncAppearancePage(')),ctx);
vm.runInContext((source.slice(source.indexOf('const AP_REGION_STAGE='),source.indexOf('/* END_AP_REGION_STAGE */'))+[].map(name=>sourceDeclaration(source,name).code).join('\n'))+'\nthis.apLayout=AP_REGION_STAGE;',ctx);
ctx.syncAppearancePage=()=>ctx.renderStable(document.getElementById('view'),ctx.apLayout.shape(ctx.viewAppearance()));


ctx.syncAppearancePage();let count=0;const check=(name,fn)=>{fn();count++;console.log('PASS '+name);};
const visible=n=>!n.closest('[hidden]');const click=n=>n.dispatchEvent(new document.defaultView.Event('click',{bubbles:true}));
check('all original mode options and every palette action remain intact',()=>{assert.equal(document.querySelectorAll('#appearanceMode option').length,3);assert.equal(document.querySelectorAll('[data-ap-palette]').length,4);assert.ok(document.querySelector('[data-ap-reset]'));});
check('three named groups replace the long one-column appearance stack',()=>{assert.equal(document.querySelectorAll('[data-ap-section]').length,3);assert.equal([...document.querySelectorAll('[data-ap-region]')].filter(visible).length,1);assert.equal(ctx.apLayout.selected,'mode');});
check('mode, palette and preview remain explicitly reachable',()=>{for(const group of ['mode','palette','preview']){click(document.querySelector('[data-ap-section="'+group+'"]'));assert.equal(ctx.apLayout.selected,group);assert.equal(document.querySelector('[data-ap-region="'+group+'"]').hidden,false);}});
check('navigation is view-only and preserves the confirmed shared appearance',()=>assert.deepEqual(JSON.parse(JSON.stringify(ctx.AP)),{mode:'auto',palette:'teal'}));
check('status remains visible outside all content groups',()=>{const status=document.getElementById('appearanceStatus');assert.ok(visible(status));assert.equal(status.getAttribute('role'),'status');assert.equal(status.getAttribute('aria-live'),'polite');});
check('both illustrative previews remain intact with their home-navigation button',()=>{assert.equal(document.querySelectorAll('.appearance-mini').length,2);assert.ok(document.querySelector('[data-ap-open="home"]'));assert.match(document.querySelector('.appearance-previewlabel').textContent,/توضيحية/);});
check('every measurable leaf and region has a unique id where present',()=>{assert.ok(document.querySelectorAll('[data-ap-leaf]').length>=10);const ids=[...document.querySelectorAll('[id]')].map(n=>n.id);assert.equal(new Set(ids).size,ids.length);});
check('layout preserves selected group through palette and mode refresh',()=>{click(document.querySelector('[data-ap-section="palette"]'));for(const palette of ['teal','blue','violet','graphite'])for(const mode of ['auto','light','dark']){ctx.AP={palette,mode};ctx.syncAppearancePage();assert.equal(ctx.apLayout.selected,'palette');const selected=document.querySelector('[data-ap-palette][aria-pressed="true"]');assert.equal(selected.dataset.apPalette,palette);assert.equal(document.querySelector('#appearanceMode option[selected]').value,mode);}});
check('in-flight and unavailable appearance controls remain disabled',()=>{ctx.appearanceSaving=true;ctx.syncAppearancePage();assert.ok(document.querySelector('#appearanceMode').disabled);assert.ok([...document.querySelectorAll('[data-ap-palette]')].every(n=>n.disabled));ctx.appearanceSaving=false;ctx.AP=null;ctx.syncAppearancePage();assert.ok(document.querySelector('#appearanceMode').disabled);assert.ok(document.querySelector('[data-ap-reset]').disabled);});
check('raw status error remains inert text after markup shaping',()=>{ctx.appearanceMessage='<img src=x onerror=alert(1)>\nخطأ';ctx.appearanceError=true;ctx.syncAppearancePage();assert.equal(document.getElementById('appearanceStatus').textContent,ctx.appearanceMessage);assert.equal(document.querySelectorAll('img,script').length,0);});
check('actual renderStable retains selected navigation and palette button identity',()=>{const nav=document.querySelector('[data-ap-section="palette"]'),palette=document.querySelector('[data-ap-palette="blue"]');ctx.AP={mode:'light',palette:'blue'};ctx.syncAppearancePage();assert.equal(document.querySelector('[data-ap-section="palette"]'),nav);assert.equal(document.querySelector('[data-ap-palette="blue"]'),palette);});
check('only a verified view may hide overflow',()=>{const css=source.slice(source.indexOf('/* AP_REGION_STYLE_BEGIN */'),source.indexOf('/* END_AP_REGION_STYLE */'));const rules=[...css.matchAll(/([^{}]+)\{[^{}]*overflow\s*:\s*hidden[^{}]*\}/g)];assert.equal(rules.length,1);assert.match(rules[0][1],/data-ap-fit-verified="true"/);});
// Exercise the real controller using an explicit synthetic flow model.
// This is deliberately not described as a browser geometry measurement.
let available=260,active=null;Object.defineProperty(document,'activeElement',{get:()=>active});
const proto=Object.getPrototypeOf(document.createElement('div'));
proto.focus=function(){active=this;};
const rect=(top,height)=>({top,bottom:top+height,left:0,right:420,width:420,height});
proto.getBoundingClientRect=function(){
 if(this.id==='view')return rect(0,available);
 const root=this.closest('.ap-bounded')||document.querySelector('.ap-bounded'),bar=root?.querySelector('.ap-fit-nav'),top=48+(bar&&!bar.hidden?36:0);
 if(this.classList?.contains('ap-work'))return rect(top,available-24-top);
 if(this.id==='appearanceStatus')return rect(available-24,24);
 const leaf=this.matches?.('[data-ap-leaf]')?this:this.closest('[data-ap-leaf]');
 if(leaf){if(leaf.closest('[hidden]'))return rect(0,0);const peers=[...leaf.parentElement.children].filter(n=>n.hasAttribute('data-ap-leaf')&&!n.hidden),at=peers.indexOf(leaf);return rect(top+14+peers.slice(0,at).reduce((sum,n)=>sum+Number(n.dataset.mockHeight||50)+8,0),Number(leaf.dataset.mockHeight||50));}
 return rect(0,32);
};
ctx.AP={mode:'light',palette:'teal'};ctx.appearanceMessage='محفوظ';ctx.appearanceError=false;ctx.syncAppearancePage();
check('measured leaf pagination preserves access to all palette controls',()=>{ctx.apLayout.fit();const root=document.querySelector('.ap-bounded'),leaves=[...root.querySelector('[data-ap-region="palette"]').children],seen=new Set();let next;do{leaves.forEach((n,i)=>{if(!n.hidden)seen.add(i);});next=[...root.querySelectorAll('.ap-fit-nav button')].find(n=>n.textContent==='التالي');if(!next.disabled)next.onclick({preventDefault(){},stopPropagation(){}});else break;}while(true);assert.equal(seen.size,leaves.length);assert.equal(document.getElementById('view').dataset.apFitVerified,'true');});
check('probe preserves the active control and chooses its measured page',()=>{const target=document.querySelector('[data-ap-palette="blue"]');active=target;ctx.apLayout.fit();assert.equal(active,target);assert.equal(target.closest('[hidden]'),null);});
check('refresh retains the focused pager until the next measurement can restore it',()=>{vm.runInContext('apLayout.fit()',ctx,{timeout:1000});const before=document.querySelector('.ap-fit-nav'),button=[...before.querySelectorAll('button')].find(n=>n.textContent==='التالي');active=button;vm.runInContext('syncAppearancePage()',ctx,{timeout:1000});assert.ok(document.querySelector('.ap-fit-nav')===before,'Refresh replaced the focused pager before measurement');assert.ok(active===button,'Refresh lost the focused pager');ctx.apLayout.fit();assert.ok(active.isConnected);assert.equal(active.textContent,'التالي');});
check('larger available height reunites the complete group',()=>{available=800;ctx.apLayout.fit();const region=document.querySelector('[data-ap-region="palette"]');assert.ok([...region.children].every(n=>!n.hidden));assert.ok(document.querySelector('.ap-fit-nav').hidden);});
check('oversized singleton is not hidden or falsely verified',()=>{const leaf=document.querySelector('[data-ap-region="palette"]>[data-ap-leaf]');leaf.dataset.mockHeight='2000';available=260;ctx.apLayout.fit();assert.equal(document.getElementById('view').dataset.apFitVerified,undefined);assert.ok([...leaf.parentElement.children].every(n=>!n.hidden));assert.match(document.querySelector('.ap-fit-nav').textContent,/كبّر/);delete leaf.dataset.mockHeight;});
console.log('RESULT '+count+' isolated appearance-layout cases; no browser layout or API/settings changes.');
