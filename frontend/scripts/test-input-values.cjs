// Test the actual TypeScript normalizer without adding a test framework.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const ts=require('typescript');
const file=path.join(__dirname,'../src/inputValues.ts');
const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const exportsObject={};vm.runInNewContext(code,{exports:exportsObject});
const {normaliseValues}=exportsObject;
const ports={design:{type:'flat_dict',fields:{C:{type:'number'},L:{type:'number',required:false}}},flag:{type:'boolean',required:false}};
const normalize=v=>JSON.parse(JSON.stringify(normaliseValues(v,ports)));
assert.deepEqual(normalize({design:{C:'0.0001',L:''}}),{design:{C:0.0001}});
assert.deepEqual(normalize({design:{C:'0.0001',L:'  '}}),{design:{C:0.0001}});
assert.deepEqual(normalize({design:{C:'0.0001',L:'0'},flag:false}),{design:{C:0.0001,L:0},flag:false});
assert.deepEqual(normalize({design:{C:'',L:'bad'}}),{design:{C:'',L:'bad'}});
assert.deepEqual(normalize({design:{C:'0.0001'}}),{design:{C:0.0001}});
console.log('5 optional input normalization cases passed');
