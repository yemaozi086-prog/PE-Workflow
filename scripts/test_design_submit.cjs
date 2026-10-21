const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dsh/preset/flux-design/flux-design-tools.js'),'utf8');
const registered={};let sent=0,captured=null;
const sandbox={module:{exports:{}},__dirname:'preset',__filename:'preset/tools.js',require(name){
 if(name==='node:fs')return {readFileSync:p=>p==='preset/tools.js'?source:'/workspace',mkdirSync(){},existsSync:()=>false,writeFileSync(p,data){captured=JSON.parse(data);},unlinkSync(){}};
 return require(name);
}};
vm.runInNewContext(source,sandbox);
sandbox.module.exports.apply({tools:{register(t){registered[t.name]=t;}},subprocess:{spawn(){sent++;return {done:Promise.resolve(),collected:{stdout:{readFrom:()=>({text:'{"status":"accepted"}'})}}};}}});
(async()=>{
 const base={task_id:'task',call_id:'call',token:'secret',kind:'module'};
 for(const invalid of ['{"broken":',null,[],42]){
   const rejected=await registered.design_result.execute(invalid);
   assert.equal(rejected.code,'TOOL_ARGUMENT_OBJECT');assert.equal(rejected.stop_required,true);assert.equal(sent,0);
 }
 const value=await registered.design_result.execute({...base,module:'{"source":"unchanged"}'});
 assert.equal(value.submitted,false);assert.equal(sent,0);assert.equal(value.diagnostics[0].path,'/module');
 const good={source:'def compute(inputs):\n    return {}',outputs:{}};
 await registered.design_result.execute({...base,module:good});assert.equal(sent,1);assert.equal(good.source,'def compute(inputs):\n    return {}');
 const validText=JSON.stringify({...base,module:good});
 const quoted=await registered.design_result.execute(validText);
 assert.equal(quoted.code,'TOOL_ARGUMENT_OBJECT');assert.equal(sent,1);
 const nestedText=JSON.stringify({...base,module:{
  id:'nested',version:1,implementation:'python_subset',input_checks:[],
  inputs:{limits:{},outputs:{result:{}},source:'def compute(inputs):\n    return {}\n'}
 }});
 const repaired=await registered.design_result.execute(nestedText.slice(0,-1));
 assert.notEqual(repaired.code,'TOOL_ARGUMENT_OBJECT');assert.equal(sent,2);
 assert.deepEqual(captured.module.outputs,{result:{}});
 assert.equal(captured.module.source,'def compute(inputs):\n    return {}\n');
 assert.equal('outputs' in captured.module.inputs,false);
 assert.equal('source' in captured.module.inputs,false);
 const misplaced=await registered.design_result.execute({...base,call_id:'other',module:{outputs:{source:'code'}}});
 assert.equal(misplaced.submitted,false);assert.equal(misplaced.diagnostics[0].path,'/module/source');
 await registered.design_result.execute({...base,call_id:'other',module:'broken'});assert.equal(sent,3);
 assert.equal(registered.design_result.parameters.properties.implementation.type,'object');
 const code={implementation:'python_subset',source:'def compute(inputs):\n    return {}'};
 await registered.design_precheck.execute({task_id:'task',call_id:'call',token:'secret',contract_hash:'frozen',code});
 assert.deepEqual(captured.code,code);assert.equal(captured.contract_hash,'frozen');assert.equal(captured._tool_runtime_hash,require('node:crypto').createHash('sha256').update(source).digest('hex'));
 await registered.design_result.execute({task_id:'task',call_id:'call',token:'secret',kind:'module_code',implementation:{contract_hash:'frozen',code,precheck_id:'checked'}});
 assert.equal(captured.kind,'module_code');assert.deepEqual(captured.implementation.code,code);assert.equal(captured.module,undefined);
 console.log('Design submission preflight passed: exact scope diagnostic, bounded local retry, missing-root-brace and misplaced-module-member packaging recovered, source unchanged.');
})().catch(e=>{console.error(e);process.exitCode=1;});
