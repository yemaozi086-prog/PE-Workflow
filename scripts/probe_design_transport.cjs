// No model invocation. Exercise the installed plugin and real Python bridge.
const fs=require('node:fs'),cp=require('node:child_process');
const config=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const tools={};
require(config.tool).apply({tools:{register(t){tools[t.name]=t;}},subprocess:{spawn(spec){
  const child=cp.spawn(spec.argv[0],spec.argv.slice(1),{cwd:spec.cwd,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let out='',err='';child.stdout.on('data',x=>out+=x);child.stderr.on('data',x=>err+=x);
  return {done:new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',code=>code===0?resolve():reject(new Error('Bridge failed with exit '+code)));}),collected:{stdout:{readFrom:()=>({text:out})}}};
}}});
(async()=>{const results=[];for(const args of config.payloads)results.push(await tools[args?.operation||config.operation||'design_result'].execute(args?.operation?args.args:args));process.stdout.write(JSON.stringify(results));})().catch(e=>{console.error(e.message);process.exitCode=1;});
