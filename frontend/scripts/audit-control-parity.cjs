// Report disagreements; this audit deliberately does not fix contracts.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),ts=require('typescript');
const code=ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/types.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const exportsObject={};vm.runInNewContext(code,{exports:exportsObject});
const folder=path.join(__dirname,'../../data/protocol-audit-20261020');
const cases=JSON.parse(fs.readFileSync(path.join(folder,'frontend-control-cases.json'),'utf8'));
const result=cases.map(c=>{const actual=exportsObject.ports(c.node,[],c.workflow).outputs;return {name:c.name,backend_output_names:Object.keys(c.expected),frontend_output_names:Object.keys(actual),...(c.name==='batch_scalar_text'?{backend_reason_dtype:c.expected.assessment.fields.reason.value_type.dtype,frontend_reason_dtype:actual.assessment.fields.reason.value_type.dtype}:{})};});
fs.writeFileSync(path.join(folder,'frontend-control-parity.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
