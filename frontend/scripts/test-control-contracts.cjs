const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path'),ts=require('typescript');
const source=ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/types.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const exported={};vm.runInNewContext(source,{exports:exported});
function contract(p){return {type:p.type,...(p.value_type?{dtype:p.value_type.dtype,shape:Array.from(p.value_type.shape)}:{}),...(p.axes?.length?{axes:Array.from(p.axes,a=>a.name)}:{}),...(p.fields&&Object.keys(p.fields).length?{fields:Object.fromEntries(Object.entries(p.fields).map(([k,v])=>[k,contract(v)]))}:{}),...(p.item?{item:contract(p.item)}:{})};}
for(const c of require('./fixtures/control-contracts.json')){
 const actual=exported.ports(c.node,[],c.workflow).outputs;
 const normalize=ps=>JSON.parse(JSON.stringify(Object.fromEntries(Object.entries(ps).map(([k,p])=>[k,contract(p)]))));
 assert.deepEqual(normalize(actual),normalize(c.expected),c.name);
}
console.log('Backend contract parity passed: batch text dtype/shape/axes, foreach v3 and aggregate v3.');
