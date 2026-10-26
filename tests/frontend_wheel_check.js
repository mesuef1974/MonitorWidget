"use strict";
// Invoke the production wheel listener with measured, controlled DOM ancestors.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
const source=html.slice(html.indexOf('const pagerWheelGestures='),html.indexOf('\ndocument.addEventListener("keydown", e => {',html.indexOf('const pagerWheelGestures=')));
class Node {
 constructor(name,parent=null){this.name=name;this.parentElement=parent;this.nodeType=1;this.isConnected=true;this.clientHeight=200;this.scrollHeight=200;this.overflowY='hidden';this.input=false}
 getClientRects(){return this.isConnected&&this.clientHeight>0?[{}]:[]}
 closest(){for(let n=this;n;n=n.parentElement)if(n.input)return n;return null}
}
let checks=0;function check(name,fn){fn();checks++;console.log('PASS '+name)}
function setup(fast=false){
 const view=new Node('view'),a=new Node('A',view),b=new Node('B',view),fa=new Node('A-footer',view),child=new Node('A-row',a),bchild=new Node('B-row',b);
 const pager=(list,foot=null)=>({listEl:list,footEl:foot,page:1,totalPages:3,moves:[],go(d){this.moves.push(d);this.page=Math.max(0,Math.min(this.totalPages-1,this.page+d))}});
 const A=pager(a,fa),B=pager(b),PGS={A,B};let now=1000,listener,options;
 const c=vm.createContext({WeakMap,Date:{now:()=>now},PGS,getComputedStyle:n=>({overflowY:n.overflowY}),document:{documentElement:{classList:{contains:name=>fast&&name==='fast-access'}},getElementById:()=>({addEventListener:(type,fn,opt)=>{assert.equal(type,'wheel');listener=fn;options=opt}})}});
 // The listener compares ancestor identity to the view element, so registration and lookup share it.
 view.addEventListener=(type,fn,opt)=>{listener=fn;options=opt};c.document.getElementById=()=>view;
 vm.runInContext(source,c);
 const send=(target,deltaY,extra={})=>{now+=200;const e={target,deltaY,deltaX:0,deltaMode:0,cancelable:true,defaultPrevented:false,prevented:0,preventDefault(){this.prevented++;this.defaultPrevented=true},...extra};listener(e);return e};
 return{view,a,b,fa,child,bchild,A,B,PGS,pager,send,options,advance:ms=>now+=ms};
}
let e=setup();let event=e.send(e.child,100);
check('wheel down pages only the pointed list, not the last registered list',()=>{assert.equal(e.A.page,2);assert.equal(e.B.page,1);assert.equal(event.prevented,1);assert.equal(e.options.passive,false)});
event=e.send(e.child,-100);
check('wheel up returns the same pointed list to its previous page',()=>{assert.equal(e.A.page,1);assert.equal(e.B.page,1);assert.equal(event.prevented,1)});
event=e.send(e.bchild,-100);
check('a separate pointed list owns its own upward gesture',()=>{assert.equal(e.B.page,0);assert.equal(e.A.page,1);assert.equal(event.prevented,1)});
event=e.send(e.fa,100);
check('explicit footer routes to its associated list',()=>{assert.equal(e.A.page,2);assert.equal(event.prevented,1)});
event=e.send(e.child,100);
check('at a paging boundary wheel is not canceled and outer scrolling remains available',()=>{assert.equal(e.A.page,2);assert.equal(event.prevented,0)});
e=setup();const background=new Node('background',e.view);event=e.send(background,120);
check('page background and ordinary outer scrolling do not change any dataset page',()=>{assert.equal(e.A.page,1);assert.equal(e.B.page,1);assert.equal(event.prevented,0)});
e=setup();const scroll=new Node('native-scroll',e.a);scroll.scrollHeight=800;scroll.overflowY='auto';const nested=new Node('nested-text',scroll);event=e.send(nested,100);
check('a nested native scroll region consumes scrolling before list paging',()=>{assert.equal(e.A.moves.length,0);assert.equal(event.prevented,0)});
scroll.scrollTop=600;event=e.send(nested,100);
check('native scroll boundary remains native scroll chaining, not an unexpected data page',()=>{assert.equal(e.A.moves.length,0);assert.equal(event.prevented,0)});
e=setup();e.a.overflowY='auto';e.a.scrollHeight=900;event=e.send(e.child,100);
check('wrapped rows scroll within the actual list without changing the stable dataset page',()=>{assert.equal(e.A.page,1);assert.equal(event.prevented,0)});
event=e.send(e.fa,100);
check('explicit footer still changes the stable page when its rows use native vertical scrolling',()=>{assert.equal(e.A.page,2);assert.equal(event.prevented,1)});
e=setup();for(const extra of [{ctrlKey:true},{metaKey:true},{altKey:true},{shiftKey:true},{deltaX:150},{cancelable:false},{defaultPrevented:true}]){event=e.send(e.child,100,extra);assert.equal(event.prevented,0)}
check('zoom, modifiers, horizontal gestures and already handled events are left alone',()=>assert.equal(e.A.moves.length,0));
e=setup();const input=new Node('input',e.a);input.input=true;event=e.send(input,100);
check('input/select/text editing retains native wheel behavior',()=>{assert.equal(e.A.moves.length,0);assert.equal(event.prevented,0)});
e=setup();const inner=new Node('inner-list',e.a),innerChild=new Node('inner-row',inner),innerPager=e.pager(inner);e.PGS.inner=innerPager;event=e.send(innerChild,100);
check('nearest nested registered list wins over its outer page',()=>{assert.equal(innerPager.page,2);assert.equal(e.A.page,1);assert.equal(event.prevented,1)});
e=setup();e.a.clientHeight=0;event=e.send(e.child,100);
check('hidden/unmeasured list cannot intercept wheel input',()=>{assert.equal(e.A.moves.length,0);assert.equal(event.prevented,0)});
e=setup();e.send(e.child,1);e.send(e.child,1);e.send(e.child,1);event=e.send(e.child,1);
check('small trackpad deltas accumulate for the pointed list',()=>{assert.equal(e.A.page,2);assert.equal(e.A.moves.length,1);assert.equal(event.prevented,1)});
e=setup();e.send(e.child,1,{deltaMode:1});
check('line-mode wheel deltas work in both directions',()=>{assert.equal(e.A.page,2);e.send(e.child,-1,{deltaMode:1});assert.equal(e.A.page,1)});
e=setup();const host=new Node('custom-page',e.view),foot=new Node('custom-footer',e.view);let state=0;e.PGS.custom={listEl:host,footEl:foot,wheelState:()=>state,go:d=>state=Math.max(0,Math.min(2,state+d))};event=e.send(foot,100);
check('custom health/space/IO page state supports scoped footer paging',()=>{assert.equal(state,1);assert.equal(event.prevented,1);assert.equal(e.A.page,1)});
e=setup();const spBody=new Node('space-body',e.view),spList=new Node('space-coordinated-list',spBody),spRow=new Node('space-row',spList);let spState=1;e.PGS.sp={listEl:spBody,wheelState:()=>spState,go:d=>spState+=d};const childPage=e.pager(spList);childPage.wheelOwner='sp';e.PGS.childPage=childPage;event=e.send(spRow,-100);
check('coordinated space child list pages its declared owner and keeps the global footer consistent',()=>{assert.equal(spState,0);assert.equal(childPage.page,1);assert.equal(event.prevented,1);assert.equal(e.A.page,1)});
e=setup();e.A.totalPages=5;e.send(e.child,100);e.advance(-150);event=e.send(e.child,100);
check('rapid repeated gesture is bounded and not canceled when no page changed',()=>{assert.equal(e.A.page,2);assert.equal(event.prevented,0)});
e.advance(-150);event=e.send(e.child,-100);
check('rapid reversal works immediately in the opposite direction',()=>{assert.equal(e.A.page,1);assert.equal(event.prevented,1)});
const nav={},oldNav=nav,PGS={io:{footEl:nav}};let pageDraws=0;
const ioContext=vm.createContext({IOPG:0,IO_PAGES:['a','b','c'],PGS,document:{documentElement:{classList:{contains:name=>fast&&name==='fast-access'}},getElementById:()=>nav,createElement:()=>({content:{firstElementChild:{innerHTML:'updated navigation'}}})},renderStable:(target,markup)=>target.markup=markup,ioPgNav:()=>'<footer>',ioPage:()=>pageDraws++});
vm.runInContext(html.slice(html.indexOf('function ioGo('),html.indexOf('\nfunction ioCleanup(',html.indexOf('function ioGo(')))+'\nthis.go=ioGo;',ioContext);ioContext.go(1);
check('IO section navigation preserves the footer node and its paging association',()=>{assert.equal(nav,oldNav);assert.equal(PGS.io.footEl,nav);assert.equal(nav.markup,'updated navigation');assert.equal(pageDraws,1)});
console.log(`RESULT ${checks} scoped wheel-event checks passed; native browser input remains unverified`);

check("bounded workspace wheel changes only its pointed record page in both directions",()=>{const f=setup();assert.equal(f.send(f.child,100).prevented,1);assert.equal(f.A.page,2);assert.equal(f.B.page,1);assert.equal(f.send(f.fa,-100).prevented,1);assert.equal(f.A.page,1)});
