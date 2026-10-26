'use strict';
// Pure production planner with a controlled capacity oracle, not browser geometry.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const stage=process.argv.indexOf('--stage');
const file=stage<0?path.join(__dirname,'../index.html'):process.argv[stage+1];
assert.ok(file,'Explicit --stage path required');
const source=fs.readFileSync(file,'utf8'),start=source.search(/^function cardPackingPages\(/m);
assert.ok(start>=0,'Production cardPackingPages missing');
let code;
for(let end=source.indexOf('}',start);end>=0;end=source.indexOf('}',end+1)){
 const candidate=source.slice(start,end+1);
 try{new vm.Script(candidate);code=candidate;break}catch(error){if(!(error instanceof SyntaxError))throw error}
}
assert.ok(code,'Cannot parse planner');
const ctx=vm.createContext({});vm.runInContext(code+';this.planCards=cardPackingPages',ctx);
const plan=(weights,budget)=>JSON.parse(JSON.stringify(ctx.planCards(weights.length,items=>items.reduce((total,i)=>total+weights[i],0)<=budget)));
let count=0;const check=(name,run)=>{run();count++;console.log('PASS '+name)};
function validate(weights,budget){
 const result=plan(weights,budget);
 if(weights.some(value=>value>budget)){assert.equal(result.verified,false);assert.deepEqual(result.pages,[]);return result}
 assert.equal(result.verified,true);
 assert.deepEqual(result.pages.flat().slice().sort((a,b)=>a-b),weights.map((_,i)=>i));
 result.pages.forEach((page,p)=>{
  assert.ok(page.length);const used=page.reduce((total,i)=>total+weights[i],0);assert.ok(used<=budget);
  const later=result.pages.slice(p+1).flat();for(const i of later)assert.ok(used+weights[i]>budget,'A later card still fits on this page');
 });
 return result;
}
check('planner searches later cards instead of wasting a gap after the next oversized candidate',()=>assert.deepEqual(validate([70,70,30,30],100).pages,[[0,2],[1,3]]));
check('exact capacity is admitted and all cards remain reachable once',()=>assert.deepEqual(validate(Array(14).fill(10),60).pages.map(p=>p.length),[6,6,2]));
check('one oversized singleton rejects the whole packed layout for the existing fallback',()=>validate([130,20,30],100));
check('empty collection terminates without a phantom page',()=>assert.deepEqual(plan([],100),{pages:[],verified:true}));
check('wider controlled capacity admits more uniform cards and fewer pages',()=>{const a=validate(Array(14).fill(10),40),b=validate(Array(14).fill(10),70);assert.ok(b.pages[0].length>a.pages[0].length);assert.ok(b.pages.length<a.pages.length)});
check('zero-cost items terminate and are retained without duplication',()=>assert.deepEqual(validate([0,0,50,50],100).pages,[[0,1,2,3]]));
check('deterministic mixed cases cover maximal pages, finite bounds and complete reachability',()=>{
 let seed=20261026;const next=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed};
 for(let trial=0;trial<120;trial++){const weights=Array.from({length:1+next()%24},()=>1+next()%90),budget=40+next()%150;const a=validate(weights,budget);assert.deepEqual(plan(weights,budget),a)}
});
console.log('RESULT '+count+' pure planner cases including120 deterministic scenarios; '+(stage<0?'actual index source':'explicit development stage')+'; no browser layout, network or UI operations.');
