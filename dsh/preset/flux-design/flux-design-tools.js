module.exports={name:'flux-design-tools',inject:['subprocess','tools'],apply(ctx){
  const fs=require('node:fs'),path=require('node:path');
  // Captured when this module is evaluated, never read afresh to disguise cached code.
  const runtimeHash=require('node:crypto').createHash('sha256').update(fs.readFileSync(__filename)).digest('hex');
  const root=fs.readFileSync(path.join(__dirname,'flux-root.txt'),'utf8').replace(/^\uFEFF/,'').trim();
  const credentials={task_id:{type:'string'},call_id:{type:'string'},token:{type:'string'}};
  const paging={section:{type:'string'},offset:{type:'integer',minimum:0},step_id:{type:'string'}};
  const invoke=async(op,args)=>{
    const inbox=path.join(root,'data','dsh-inbox');fs.mkdirSync(inbox,{recursive:true});
    const filename=path.join(inbox,require('node:crypto').randomUUID().replaceAll('-','')+'.json');
    if(args===null||typeof args!=='object'||Array.isArray(args))return {status:'not_submitted',submitted:false,code:'TOOL_ARGUMENT_OBJECT',diagnostics:[{path:'/',message:'Tool arguments must be a complete JSON object. Raw malformed JSON text is not accepted.'}],stop_required:true};
    fs.writeFileSync(filename,JSON.stringify({...args,_tool_runtime_hash:runtimeHash}),{encoding:'utf8',flag:'wx'});
    try{
      const handle=ctx.subprocess.spawn({argv:[(fs.existsSync(root+'/runtime/python.exe')?root+'/runtime/python.exe':root+'/.venv/Scripts/python.exe'),root+'/bridge/flux_worker_client.py','design-'+op,'--file',filename],cwd:root,stdio:{stdin:'ignore',stdout:{maxBytes:4000000},stderr:{maxBytes:100000}},graceMs:45000});
      await handle.done;const text=handle.collected.stdout.readFrom(0).text.trim();
      try{return JSON.parse(text);}catch{return {status:'failed',error:'Invalid design bridge response',raw:text.slice(0,300)};}
    }finally{try{fs.unlinkSync(filename);}catch{}}

  };
  const envelopeFailures=new Map(),contextCredentials=new Map();
  // DSH can pass malformed raw tool text to execute when the model omits only
  // the final root brace. Recover that one bounded packaging case; never accept
  // a quoted JSON.stringify result or a string candidate.
  const repairMissingRootBrace=text=>{
    if(typeof text!=='string')return null;
    try{JSON.parse(text);return null;}catch{}
    for(let extra=1;extra<=3;extra++){
      try{
        const value=JSON.parse(text+'}'.repeat(extra));
        if(value&&typeof value==='object'&&!Array.isArray(value))return value;
      }catch{}
    }
    return null;
  };
  // A missing brace before module.outputs/source can make those two known
  // module members appear inside module.inputs. Move only those members back
  // to the module root; never change source text, ports or metadata.
  const repairModuleEnvelope=value=>{
    if(!value||typeof value!=='object'||Array.isArray(value)||value.kind!=='module')return value;
    const module=value.module;
    if(!module||typeof module!=='object'||Array.isArray(module))return value;
    const inputs=module.inputs;
    if(!inputs||typeof inputs!=='object'||Array.isArray(inputs))return value;
    if(module.outputs===undefined&&inputs.outputs&&typeof inputs.outputs==='object'&&!Array.isArray(inputs.outputs)){
      module.outputs=inputs.outputs;delete inputs.outputs;
    }
    if(module.source===undefined&&typeof inputs.source==='string'){
      module.source=inputs.source;delete inputs.source;
    }
    return value;
  };
  const readContext=async args=>{
    const value=await invoke('context',args);
    if(value&&!value.stop_required&&!value.http_status&&value.status!=='failed'&&value.status!=='not_submitted'&&args&&['task_id','call_id','token'].every(k=>typeof args[k]==='string'))contextCredentials.set(args.task_id+':'+args.call_id,{task_id:args.task_id,call_id:args.call_id,token:args.token});
    return value;
  };
  const submit=async args=>{
    if(typeof args==='string')args=repairMissingRootBrace(args);
    args=repairModuleEnvelope(args);
    if(args===null||typeof args!=='object'||Array.isArray(args)){
      // Without a valid root there is no task id. Never guess between concurrent calls.
      if(contextCredentials.size===1)await invoke('failure',{...contextCredentials.values().next().value,code:'TOOL_ARGUMENT_OBJECT'});
      return {status:'not_submitted',submitted:false,code:'TOOL_ARGUMENT_OBJECT',diagnostics:[{path:'/',message:'Malformed tool argument; no candidate accepted. The authenticated call is closed when context credentials are available.'}],stop_required:true};
    }
    const fields={plan:'plan',plan_blueprint:'blueprint',plan_delta:'delta',plan_patch:'patch',workflow:'workflow',module:'module',questions:'questions',extension_request:'extension_request'};
    const field=fields[args.kind];let problem=null;
    if(field&&(args[field]===null||typeof args[field]!=='object'||Array.isArray(args[field])))problem={path:'/'+field,message:'Submit a JSON object directly, never JSON.stringify or a quoted JSON string. Preserve the candidate contents; fix only the tool argument packaging.'};
    else if(args.kind==='module'&&typeof args.module.outputs?.source==='string'&&args.module.source===undefined)problem={path:'/module/source',message:'source belongs directly under module, not inside outputs. Every outputs member must be a Port object. Fix only this scope.'};
    if(problem){
      const key=args.task_id+':'+args.call_id,count=(envelopeFailures.get(key)||0)+1;envelopeFailures.set(key,count);
      if(count<2)return {status:'not_submitted',submitted:false,diagnostics:[problem],local_format_attempt:count,instruction:'The platform has NOT received this candidate; the current call remains open. Correct only the packaging and call design_result once more. Do not change code, formulas, contracts or expected answers.'};
    }
    return invoke('result',args);
  };
  const output={schema:{type:'object',additionalProperties:true},render:(args,v)=>[{type:'text',text:JSON.stringify(v)}]};
  ctx.tools.register({name:'design_context',description:'Read a short exact index first. Then read section=request/plan/step/schema/rules/diagnostics/formula/extensions/controls; Read controls for exact native grid/batch contracts (grid output rows, column dictionary, batch_rows); concatenate JSON text pages using next_offset. Never guess module refs.',parameters:{type:'object',properties:{...credentials,...paging},required:Object.keys(credentials)},output,execute:args=>readContext(args)});
  ctx.tools.register({name:'design_module',description:'Read an exact module_ref from design_context index only. Response is paged JSON text; follow next_offset. MODULE_NOT_FOUND means stop and report, never guess alternatives.',parameters:{type:'object',properties:{...credentials,...paging,module_ref:{type:'string'}},required:[...Object.keys(credentials),'module_ref']},output,execute:args=>invoke('module',args)});
  ctx.tools.register({name:'design_result',description:'Submit exactly the required_result_kind from design_context. For plan_blueprint use {kind:plan_blueprint,blueprint:COMPLETE_BLUEPRINT} at TOP LEVEL, never plan.blueprint. For plan_delta use {kind:plan_delta,delta:{base_hash,edits}}; preserve all other content. Read diagnostics and allowed_paths first. Submit only ONE payload matching kind. Never runs or publishes.',parameters:{type:'object',properties:{...credentials,kind:{type:'string',enum:['plan','plan_blueprint','plan_delta','plan_patch','workflow','module','extension_request','questions','blocked']},plan:{type:'object',additionalProperties:true},blueprint:{type:'object',additionalProperties:true},delta:{type:'object',additionalProperties:true},patch:{type:'object',additionalProperties:true},workflow:{type:'object',additionalProperties:true},module:{type:'object',additionalProperties:true},extension_request:{type:'object',additionalProperties:true},questions:{type:'object',additionalProperties:{type:'string'}},explanation:{type:'string'}},required:[...Object.keys(credentials),'kind']},output,execute:args=>submit(args)});
}};
