module.exports={name:'flux-worker-tools',inject:['subprocess','tools'],apply(ctx){
  const fs=require('node:fs'),path=require('node:path');
  const root=fs.readFileSync(path.join(__dirname,'flux-root.txt'),'utf8').replace(/^\uFEFF/,'').trim();
  const credentials={task_id:{type:'string'},call_id:{type:'string'},token:{type:'string'}};
  const invoke=async(op,args)=>{
    const handle=ctx.subprocess.spawn({argv:[(fs.existsSync(root+'/runtime/python.exe')?root+'/runtime/python.exe':root+'/.venv/Scripts/python.exe'),root+'/bridge/flux_worker_client.py',op,Buffer.from(JSON.stringify(args),'utf8').toString('base64')],cwd:root,stdio:{stdin:'ignore',stdout:{maxBytes:8000000},stderr:{maxBytes:100000}},graceMs:45000});
    await handle.done;
    const text=handle.collected.stdout.readFrom(0).text.trim();
    try{return JSON.parse(text);}catch{return {status:'failed',error:'Invalid worker bridge response',raw:text.slice(0,300)};}
  };
  const output={schema:{type:'object',additionalProperties:true},render:(args,v)=>[{type:'text',text:JSON.stringify(v)}]};
  ctx.tools.register({name:'flux_worker_context',description:'Read only this task frozen context. Call first.',parameters:{type:'object',properties:credentials,required:Object.keys(credentials)},output,execute:args=>invoke('context',args)});
  ctx.tools.register({name:'flux_worker_result',description:'Read worker_protocol: implementation_patch_v1 MUST submit kind=candidate, patch={source,entrypoint,adapter,provenance,added_inputs}; DO NOT use module. Legacy module_v1 uses module. Never publishes.',parameters:{type:'object',properties:{...credentials,kind:{type:'string',enum:['candidate','questions','blocked']},module:{type:'object',additionalProperties:true},patch:{type:'object',additionalProperties:true},questions:{type:'object',additionalProperties:{type:'string'}},explanation:{type:'string'}},required:[...Object.keys(credentials),'kind']},output,execute:args=>invoke('result',args)});
}};
