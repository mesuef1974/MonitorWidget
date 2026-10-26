'use strict';
// A fast compatibility guard against broad replacement deleting adjacent loaders,
// helpers or delegated actions. Behavioral and owned-DOM checks remain separate.
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const source=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'frontend_surface_manifest.json'),'utf8'));
const names=new Set([...source.matchAll(/\b(?:async\s+)?function\s+(\w+)\s*\(/g)].map(m=>m[1]));
const selectors=new Set([...source.matchAll(/\.closest\(\s*(['"])(.*?)\1\s*\)/gs)].map(m=>m[2]));
const missingNames=manifest.declaredFunctionNames.filter(name=>!names.has(name));
const missingSelectors=manifest.delegatedDataSelectors.filter(selector=>!selectors.has(selector));
assert.deepEqual(missingNames,[],'Original loader/helper declarations disappeared; review the function replacement boundary.');
assert.deepEqual(missingSelectors,[],'Original delegated action selectors disappeared; review adjacent handlers.');
console.log('PASS '+manifest.declaredFunctionNames.length+' original named declarations and '+manifest.delegatedDataSelectors.length+' delegated data-action selectors remain.');
console.log('Compatibility/source inventory only; no browser execution, action execution or layout claim.');
