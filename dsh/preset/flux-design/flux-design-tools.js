module.exports={name:'flux-design-tools',inject:['subprocess','tools'],apply(ctx){
  const fs=require('node:fs'),path=require('node:path');
  const root=fs.readFileSync(path.join(__dirname,'flux-root.txt'),'utf8').replace(/^\uFEFF/,'').trim();
  const credentials={task_id:{type:'string'},call_id:{type:'string'},token:{type:'string'}};
  const invoke=async(op,args)=>{
    const inbox=path.join(root,'data','dsh-inbox');fs.mkdirSync(inbox,{recursive:true});
    const filename=path.join(inbox,require('node:crypto').randomUUID().replaceAll('-','')+'.json');
    fs.writeFileSync(filename,JSON.stringify(args),{encoding:'utf8',flag:'wx'});
    try{
      const handle=ctx.subprocess.spawn({argv:[(fs.existsSync(root+'/runtime/python.exe')?root+'/runtime/python.exe':root+'/.venv/Scripts/python.exe'),root+'/bridge/flux_worker_client.py','design-'+op,'--file',filename],cwd:root,stdio:{stdin:'ignore',stdout:{maxBytes:4000000},stderr:{maxBytes:100000}},graceMs:45000});
      await handle.done;const text=handle.collected.stdout.readFrom(0).text.trim();
      try{return JSON.parse(text);}catch{return {status:'failed',error:'Invalid design bridge response',raw:text.slice(0,300)};}
    }finally{try{fs.unlinkSync(filename);}catch{}}

  };
  const output={schema:{type:'object',additionalProperties:true},render:(args,v)=>[{type:'text',text:JSON.stringify(v)}]};
  ctx.tools.register({name:'design_context',description:'Read the frozen task, scene, contracts and diagnostics. Call first.',parameters:{type:'object',properties:credentials,required:Object.keys(credentials)},output,execute:args=>invoke('context',args)});
  ctx.tools.register({name:'design_module',description:'Inspect exact input/output contracts and code of one frozen module ref before selecting it.',parameters:{type:'object',properties:{...credentials,module_ref:{type:'string'}},required:[...Object.keys(credentials),'module_ref']},output,execute:args=>invoke('module',args)});
  ctx.tools.register({name:'design_result',description:'Submit plan only, or one batch of questions, or blocked. Never runs or publishes.',parameters:{type:'object',properties:{...credentials,kind:{type:'string',enum:['plan','workflow','module','extension_request','questions','blocked']},plan:{type:'object',additionalProperties:true},workflow:{type:'object',additionalProperties:true},module:{type:'object',additionalProperties:true},extension_request:{type:'object',additionalProperties:true},questions:{type:'object',additionalProperties:{type:'string'}},explanation:{type:'string'}},required:[...Object.keys(credentials),'kind']},output,execute:args=>invoke('result',args)});
}};
