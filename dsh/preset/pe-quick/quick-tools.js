module.exports={name:'pe-quick-tools',inject:['subprocess','tools'],apply(ctx){
 const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
 const root=fs.readFileSync(path.join(__dirname,'flux-root.txt'),'utf8').replace(/^\uFEFF/,'').trim();
 const credentials={chat_id:{type:'string'},turn_id:{type:'string'},token:{type:'string'}};
 const output={schema:{type:'object',additionalProperties:true},render:(args,value)=>[{type:'text',text:JSON.stringify(value)}]};
 async function invoke(operation,args){
  // Unwrap only an unambiguous transport envelope; never repair source or JSON punctuation.
  try{
   if(typeof args==='string')args=JSON.parse(args);
   if(args&&typeof args==='object'&&Object.keys(args).length===1&&Object.hasOwn(args,'arguments'))args=typeof args.arguments==='string'?JSON.parse(args.arguments):args.arguments;
  }catch(e){return {status:'envelope_error',message:String(e),required:'Top-level {chat_id,turn_id,token,...}. JSON punctuation is invalid; platform received no candidate. Use quick_draft to send one small artifact at a time. Do not repeat unchanged arguments.'};}
  if(!args||typeof args!=='object'||Array.isArray(args))return {status:'envelope_error',message:'Pass a complete JSON object.'};
  if(!['chat_id','turn_id','token'].every(k=>typeof args[k]==='string'&&args[k]))return {status:'envelope_error',message:'Missing TOP-LEVEL chat_id/turn_id/token. No request sent. Use the current platform credentials; do not put them inside bundle or arguments.'};
  const inbox=path.join(root,'data','dsh-inbox');fs.mkdirSync(inbox,{recursive:true});
  const file=path.join(inbox,crypto.randomUUID().replaceAll('-','')+'.json');
  fs.writeFileSync(file,JSON.stringify({...args,operation}),{encoding:'utf8',flag:'wx'});
  try{
   const python=fs.existsSync(root+'/runtime/python.exe')?root+'/runtime/python.exe':root+'/.venv/Scripts/python.exe';
   const h=ctx.subprocess.spawn({argv:[python,root+'/bridge/quick_client.py',file],cwd:root,stdio:{stdin:'ignore',stdout:{maxBytes:2000000},stderr:{maxBytes:100000}},graceMs:95000});
   await h.done;const text=h.collected.stdout.readFrom(0).text.trim();
   try{return JSON.parse(text);}catch{return {status:'transport_error',message:'No valid platform receipt. Do not claim success.'};}
  }finally{try{fs.unlinkSync(file);}catch{}}
 }
 ctx.tools.register({name:'quick_context',description:'Read exact platform contracts and current artifacts. Read index first; follow next_offset for JSON text pages. Sections: schemas/rules/modules/module/current/diagnostics/example. diagnostics gives targeted failures; run gives complete frozen inputs/outputs/snapshot; run_trace gives full per-node/per-row trace when needed.',parameters:{type:'object',properties:{...credentials,section:{type:'string'},offset:{type:'integer',minimum:0},module_ref:{type:'string'}},required:Object.keys(credentials)},output,execute:a=>invoke('context',a)});
 ctx.tools.register({name:'quick_draft',description:'Stage one small artifact per call, without registration or execution. Prefer this to giant JSON bundles. put_module accepts one minimal Module; put_workflow accepts complete Workflow and summary; check/submit use staged artifacts. Always keep credentials at the top level. Omit schema default/null/empty metadata fields.',parameters:{type:'object',properties:{...credentials,action:{type:'string',enum:['put_module','put_workflow','check','submit']},module:{type:'object',additionalProperties:true},workflow:{type:'object',additionalProperties:true},summary:{type:'string'}},required:[...Object.keys(credentials),'action']},output,execute:a=>invoke('draft',a)});
 for(const operation of ['check','submit'])ctx.tools.register({name:'quick_'+operation,description:operation==='check'?'Check a complete {workflow,modules,summary} bundle without saving. Up to 3 checks; use exact diagnostics.':'Save complete {workflow,modules,summary} as a new versioned draft in the workflow library. Does not execute. One successful submission per user message.',parameters:{type:'object',properties:{...credentials,bundle:{type:'object',additionalProperties:true}},required:[...Object.keys(credentials),'bundle']},output,execute:a=>invoke(operation,a)});
}};
