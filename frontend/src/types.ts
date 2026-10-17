export type Port = {catalog_ref?:{id:string;version:number;name:string}; chinese_name?:string; semantic_id?:string; semantic_evidence?:string; device?:string; instance_id?:string; statistic?:string; event?:string; entry_type?:string; type:'number'|'string'|'boolean'|'array'|'flat_dict'|'collection'; kind?:'bare'|'flat_dict'|'collection'; value_type?:{dtype:string;shape:(number|string)[]}; axes?:{name:string;coordinate_ref?:string;monotonic?:boolean}[]; definition_ref?:{id:string;version:number}; quantity?:string; characteristic?:string; scope?:string; role?:string; index?:string; unit?:string; required?:boolean; default?:unknown; description?:string; fields?:Record<string,Port>; item?:Port};
export type Binding = {source:'input'|'node'|'constant'; node?:string; port?:string; field?:string; value?:unknown;field_map?:Record<string,string>};
export type Module = {contract_version?:string;project_id?:string;id:string; version:number; name:string; description?:string; inputs:Record<string,Port>; outputs:Record<string,Port>; implementation:'expressions'|'python_subset'|'python_function'|'trusted_builtin'; builtin_id?:string;builtin_hash?:string;entrypoint?:string; adapter?:unknown; capabilities?:string[]; transforms?:{operation:string;preconditions:string;rationale:string}[]; expressions:Record<string,string>; source:string; provenance:string};
export type WorkflowNode = {id:string; label?:string; kind:'compute'|'switch'|'foreach'|'aggregate'|'batch'; inputs:Record<string,Binding>; module_id?:string; module_version?:number; cases?:Record<string,Workflow>; body?:Workflow; operation?:'sum'|'mean'|'max'|'min'; field?:string};
export type Workflow = {schema_version:'1.0'|'2.0'|'3.0'; id:string; name:string; description?:string; inputs:Record<string,Port>; nodes:WorkflowNode[]; outputs:Record<string,Binding>; output_ports:Record<string,Port>; layout:Record<string,{x:number;y:number}>};
export type Diagnostic = {code:string; message:string; path:string; severity:'error'|'warning'; node_id?:string; port?:string; suggestion?:string};
export type Trace = {node_id:string; path:string; kind:string; status:string; inputs:Record<string,unknown>; outputs:Record<string,unknown>; duration_ms:number; diagnostic?:Diagnostic; selected_case?:string};
export type Run = {id:string; status:string; created_at:string; workflow_name?:string; snapshot_hash:string; output_ports?:Record<string,Port>; outputs:Record<string,unknown>; trace:Trace[]; diagnostics?:Diagnostic[]; optimization?:{best_parameters:Record<string,number>; evaluations:number; converged:boolean; history:{evaluation:number; objective:number}[]}};
export type Location = {id:string; kind:'body'|'cases'; key?:string};
export const pretty = (v:unknown) => JSON.stringify(v,null,2);
export function at(w:Workflow,path:Location[]):Workflow { for(const p of path){ const n=w.nodes.find(n=>n.id===p.id)!; w=p.kind==='body'? n.body!: n.cases![p.key!]; }return w; }
export function updateAt(w:Workflow,path:Location[],change:(w:Workflow)=>Workflow):Workflow {
  if(!path.length)return change(w);
  const [first,...rest]=path;
  return {...w,nodes:w.nodes.map(n=>n.id!==first.id?n: first.kind==='body'? {...n,body:updateAt(n.body!,rest,change)}: {...n,cases:{...n.cases,[first.key!]:updateAt(n.cases![first.key!],rest,change)}})};
}
export function ports(n:WorkflowNode,modules:Module[],w:Workflow):{inputs:Record<string,Port>;outputs:Record<string,Port>} {
  if(n.kind==='compute'){const m=modules.find(m=>m.id===n.module_id&&m.version===n.module_version);return {inputs:m?.inputs||{},outputs:m?.outputs||{}};}
  if(n.kind==='switch'){const c=Object.values(n.cases||{})[0];return {inputs:{selector:{type:'string'},...c?.inputs},outputs:c?.output_ports||{}};}
  if(n.kind==='batch'){
    const {item,...others}=n.body?.inputs||{};
    const columns=(p:Port):Port=>({...p,fields:Object.fromEntries(Object.entries(p.fields||{}).map(([key,f])=>[key,{...f,type:'array',kind:'bare',value_type:{dtype:f.value_type?.dtype||'float64',shape:['batch_rows',...(f.value_type?.shape||[])]},axes:[{name:'batch_rows'},...(f.axes||[])]}]))});
    return {inputs:{rows:columns(item||{type:'flat_dict'}),...others},outputs:Object.fromEntries(Object.entries(n.body?.output_ports||{}).map(([k,p])=>[k,columns(p)]))};
  }
  if(n.kind==='foreach'){const {item,...others}=n.body?.inputs||{};return {inputs:{items:{type:'collection',item},...others},outputs:{results:{type:'collection',item:{type:'flat_dict',fields:n.body?.output_ports}}}};}
  const b=n.inputs.items;let source:Port|undefined;
  if(b?.source==='input')source=w.inputs[b.port!];
  if(b?.source==='node'){const parent=w.nodes.find(p=>p.id===b.node);if(parent&&parent.kind==='foreach')source={type:'collection',item:{type:'flat_dict',fields:parent.body?.output_ports}};}
  const output=n.field?source?.item?.fields?.[n.field]:source?.item;
  return {inputs:{items:source||{type:'collection'}},outputs:{value:output||{type:'number'}}};
}
