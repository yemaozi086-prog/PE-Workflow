import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react';
import { Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, ReactFlowProvider, useReactFlow, useNodesInitialized, useNodeId, useUpdateNodeInternals, type Connection, type Edge, type Node, type NodeProps } from '@xyflow/react';
import { Activity, ArrowDownToLine, ArrowLeft, ArrowRight, Box, Braces, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Code2, Copy, FileJson, FolderOpen, GitBranch, History, Layers3, Loader2, Maximize2, MoreHorizontal, Play, Plus, RefreshCw, Save, Search, Settings2, ShieldCheck, Sigma, Trash2, Undo2, Upload, X, Zap } from 'lucide-react';
import type { Binding, Diagnostic, Location, Module, Port, Run, Workflow, WorkflowNode } from './types';
import { at, ports, pretty, updateAt } from './types';
import ChangeDialog from './ChangeDialog';
import AutomationDialog from './AutomationDialog';
const DesignDialog=lazy(()=>import('./DesignDialog'));
import ValueForm,{ValueEditor,normaliseValues} from './ValueForm';
import DeclarationDialog from './DeclarationDialog';
import WorkspaceDialog,{type WorkspaceItem} from './WorkspaceDialog';
import useDraft,{recoveryFor} from './useDraft';
import {moduleDisplayName,nodeDisplayName} from './domainNames';
import RunSummary from './RunSummary';
import ModuleLibrary, {type ModuleFamily} from './ModuleLibrary';
import {layeredLayout} from './graphLayout';
import WorkflowEdge from './WorkflowEdge';
import FormulaView,{type FormulaRecord} from './FormulaView';

type CanvasData = {dictionaryOnly?:boolean;label:string; subtitle:string; kind:string; inputs:Record<string,Port>; outputs:Record<string,Port>; status?:string; invalid?:boolean; duration?:number};
const kindName:Record<string,string> = {input:'工作流输入',output:'结果输出',compute:'计算模块',switch:'条件分支',foreach:'工况遍历',batch:'数值批量子图',grid:'有限工况网格',aggregate:'指标汇总'};
const kindIcons:Record<string,typeof Box> = {input:ArrowRight,output:ArrowDownToLine,compute:Box,switch:GitBranch,foreach:Layers3,batch:Layers3,grid:Layers3,aggregate:Sigma};

function PortList({ports,side,dictionaryOnly}:{ports:Record<string,Port>;side:'in'|'out';dictionaryOnly?:boolean}) {
  const [open,setOpen]=useState<Record<string,boolean>>({});
  const nodeId=useNodeId();const updateInternals=useUpdateNodeInternals();
  useEffect(()=>{if(nodeId)updateInternals(nodeId);},[open,nodeId,updateInternals]);
  return <div className={`node-ports ${side}`}>{Object.entries(ports).map(([key,p])=><div key={key}><div className="port-row"><Handle type={side==='in'?'target':'source'} position={side==='in'?Position.Left:Position.Right} id={key}/>{p.type==='flat_dict'?<button className="nodrag nopan" onClick={()=>setOpen({...open,[key]:!open[key]})}>{open[key]?'▾':'▸'} {key} · {Object.keys(p.fields||{}).length} 字段</button>:<span>{key}</span>}<small>{p.unit}</small></div>{open[key]&&Object.entries(p.fields||{}).map(([field,fp])=><div className="port-row field" key={field}>{side==='out'&&!dictionaryOnly&&<Handle type="source" position={Position.Right} id={`${key}/${field}`}/>}<span>{field}</span><small>{fp.unit||fp.type}</small></div>)}</div>)}</div>;
}
function DictionaryDeclaration({name,port}:{name:string;port:Port}) {return port.type==='flat_dict'?<details open className="contract-details"><summary>{name} · {Object.keys(port.fields||{}).length} 字段</summary>{Object.entries(port.fields||{}).map(([k,p])=><details key={k}><summary>{k} · {p.chinese_name||p.quantity} · {p.unit||p.type}</summary><pre>{pretty(p)}</pre></details>)}</details>:null;}

function DictionaryConstant({value,onChange}:{value:unknown;onChange:(value:unknown)=>void}) {
  const [text,setText]=useState(pretty(value));const [error,setError]=useState('');
  useEffect(()=>setText(pretty(value)),[value]);
  return <><textarea aria-label="字典常量 JSON" value={text} onChange={e=>setText(e.target.value)} onBlur={()=>{try{const v=JSON.parse(text);if(v===null||Array.isArray(v)||typeof v!=='object')throw Error('请输入 JSON 字典');onChange(v);setError('');}catch(e){setError(String(e));}}}/>{error&&<small role="alert">{error}</small>}</>;
}

function FlowNode({data,selected}:NodeProps<Node<CanvasData>>) {
  const Icon=kindIcons[data.kind]||Box;
  return <div className={`flow-card ${data.kind} ${selected?'selected':''} ${data.invalid?'invalid':''}`}>
    <div className="node-eyebrow"><span>{kindName[data.kind]}</span>{data.status==='succeeded'?<CheckCircle2 size={12}/>:data.invalid?<Circle size={12}/>:<MoreHorizontal size={14}/>}</div>
    <div className="node-title"><span className="node-icon"><Icon size={17}/></span><strong>{data.label}</strong></div>
    <div className="node-subtitle">{data.subtitle}</div>
    <div className="node-port-columns"><PortList ports={data.inputs} side="in" dictionaryOnly={data.dictionaryOnly}/><PortList ports={data.outputs} side="out" dictionaryOnly={data.dictionaryOnly}/></div>
    <div className="node-footer"><span className={`status-dot ${data.status==='succeeded'?'green':''}`}/>{data.status==='succeeded'?'已完成':data.invalid?'需要检查':'就绪'}{data.duration!==undefined&&<span>{data.duration.toFixed(1)} ms</span>}</div>
  </div>;
}
const nodeTypes={contract:FlowNode};
const edgeTypes={routed:WorkflowEdge};

async function api(path:string,body?:unknown,method?:string) {
  const res=await fetch('/api'+path,{method:method||(body===undefined?'GET':'POST'),headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await res.json();
  if(!res.ok)throw data;
  return data;
}
function errorDiagnostics(error:unknown):Diagnostic[]{const e=error as {diagnostics?:Diagnostic[];message?:string};return e?.diagnostics||[{code:'REQUEST_FAILED',message:e?.message||String(error),path:'/',severity:'error'}];}
function download(name:string,data:unknown) {const url=URL.createObjectURL(new Blob([pretty(data)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=name;link.click();URL.revokeObjectURL(url);}
function bindingId(b?:Binding){return !b?'':b.source==='constant'?'constant':`${b.source==='input'?'$input':b.node}|${b.port}${b.field?'/'+b.field:''}`;}
function fromId(id:string):Binding {const [node,handle]=id.split('|');const [port,field]=handle.split('/');return {source:node==='$input'?'input':'node',...(node==='$input'?{}:{node}),port,...(field?{field}:{})};}

function Workbench() {
  const flow=useReactFlow();
  const nodesInitialized=useNodesInitialized();
  const [workflow,setWorkflow]=useState<Workflow|null>(null);
  const [inputs,setInputs]=useState<Record<string,unknown>>({});
  const [modules,setModules]=useState<Module[]>([]);
  const [revision,setRevision]=useState(0);
  const [saved,setSaved]=useState<WorkspaceItem[]>([]);
  const [trash,setTrash]=useState<WorkspaceItem[]>([]);
  const [sources,setSources]=useState<Record<string,string>>({});
  const [workspaceMode,setWorkspaceMode]=useState<'new'|'rename'|'copy'|'trash'|'archive'|'snapshot'|null>(null);
  const [workspaceTarget,setWorkspaceTarget]=useState<WorkspaceItem|null>(null);
  const [declaration,setDeclaration]=useState<'input'|'output'|null>(null);
  const [recovery,setRecovery]=useState<Awaited<ReturnType<typeof recoveryFor>>>(undefined);
  const [path,setPath]=useState<Location[]>([]);
  const [selection,setSelection]=useState('$input');
  const [edgeFocus,setEdgeFocus]=useState(''),[selectedEdge,setSelectedEdge]=useState('');
  const [layoutUndo,setLayoutUndo]=useState<{scope:string;before:Workflow['layout']}|null>(null);
  const autoLaidOut=useRef(new Set<string>());
  const [diagnostics,setDiagnostics]=useState<Diagnostic[]>([]);
  const [report,setReport]=useState<'unvalidated'|'valid'|'invalid'>('unvalidated');
  const [run,setRun]=useState<Run|null>(null);
  const [tracePage,setTracePage]=useState(0);
  const [runSignature,setRunSignature]=useState('');
  const [history,setHistory]=useState<Run[]>([]);
  const [busy,setBusy]=useState('');
  const [tab,setTab]=useState('results');
  const [query,setQuery]=useState('');
  const [workflowQuery,setWorkflowQuery]=useState('');
  const [sidebarTab,setSidebarTab]=useState<'workflows'|'modules'>('workflows');
  const [notice,setNotice]=useState('');
  const [editor,setEditor]=useState<null|{kind:'json'|'inputs'|'module'|'revision'|'optimize'|'definition';title:string;text:string;module?:Module}>(null);
  const [editError,setEditError]=useState('');
  const [changeTarget,setChangeTarget]=useState<{module:Module;path:Location[];nodeId:string}|null>(null);
  const [autoTarget,setAutoTarget]=useState<{module:Module;path:Location[];nodeId:string}|null>(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  const [homeOpen,setHomeOpen]=useState(true);
  const [designMode,setDesignMode]=useState<'workflow'|'tool'>('workflow');
  const openDesign=(mode:'workflow'|'tool')=>{setDesignMode(mode);setAssemblyOpen(true);};
  const [assemblyOpen,setAssemblyOpen]=useState(false);
  const [inspectorOpen,setInspectorOpen]=useState(false);
  const [sidebarOpen,setSidebarOpen]=useState(()=>window.innerWidth>=760);
  const [resultsOpen,setResultsOpen]=useState(false);
  const [resultHeight,setResultHeight]=useState(200);
  const [canvasFocus,setCanvasFocus]=useState(false);
  const [versions,setVersions]=useState<{revision:number;created_at:string}[]>([]);
  const importFile=useRef<HTMLInputElement>(null);
  const current=workflow?at(workflow,path):null;
  const draft=useDraft(workflow,inputs,revision,sources);
  const draftSignature=JSON.stringify({workflow,inputs},(key,value)=>key==='layout'?undefined:value);
  const resultForDraft=runSignature===draftSignature;
  const selected=current?.nodes.find(n=>n.id===selection);
  const currentModule=selected?.kind==='compute'?modules.find(m=>m.id===selected.module_id&&m.version===selected.module_version):undefined;
  const [families,setFamilies]=useState<ModuleFamily[]>([]);
  const [formulaRecords,setFormulaRecords]=useState<{workflow_id?:string;items:Record<string,FormulaRecord>;nodes:Record<string,FormulaRecord>}>({items:{},nodes:{}});
  useEffect(()=>{let cancelled=false;if(!workflow)return;void api('/module-formulas?workflow_id='+encodeURIComponent(workflow.id)).then(r=>{if(!cancelled)setFormulaRecords(r);}).catch(()=>{if(!cancelled)setFormulaRecords({items:{},nodes:{}});});return()=>{cancelled=true;};},[workflow?.id,modules]);
  const latestModules=modules.filter(m=>!modules.some(x=>x.id===m.id&&x.version>m.version));

  const refreshWorkspace=useCallback(async()=>{const [w,t]=await Promise.all([api('/workspace/workflows'),api('/workspace/workflows?archived=true')]);setSaved(w.items);setTrash(t.items);return {workflows:w.items as WorkspaceItem[],trash:t.items as WorkspaceItem[]};},[]);
  const refresh=useCallback(async()=>{const [m,r,f,workspace]=await Promise.all([api('/modules'),api('/runs'),api('/module-families'),refreshWorkspace()]);setModules(m);setHistory(r);setFamilies(f.items);return workspace;},[refreshWorkspace]);
  const adopt=(data:{workflow:Workflow;inputs:Record<string,unknown>;revision?:number;draft_revision?:number;base_revision?:number;provenance?:Record<string,string>;display_name?:string})=>{
    const wf={...data.workflow,name:data.display_name||data.workflow.name};const provenance=data.provenance||{};const base=data.base_revision??data.revision??0;
    draft.hydrate({workflow:wf,inputs:data.inputs,provenance},data.draft_revision||0,base);
    setLayoutUndo(null);setSelectedEdge('');setEdgeFocus('');setWorkflow(wf);if(!Object.keys(wf.inputs).length)setInspectorOpen(true);setInputs(data.inputs);setSources(provenance);setRevision(base);setPath([]);setSelection('$input');setRun(null);setDiagnostics([]);setReport('unvalidated');
  };
  useEffect(()=>{void refresh().then(async resources=>{
    let last='';try{last=localStorage.getItem('flux-last-workflow')||'';}catch{}
    const id=resources.workflows.some(w=>w.id===last)?last:resources.workflows[0]?.id;
    if(id)adopt(await api('/workspace/workflows/'+id));else adopt({workflow:{schema_version:'3.0',id:'wf_'+crypto.randomUUID().replaceAll('-',''),name:'新的工作流',inputs:{},nodes:[],outputs:{},output_ports:{},layout:{}},inputs:{}});
    if(last&&!resources.trash.some(w=>w.id===last)){const cache=await recoveryFor(last).catch(()=>undefined);if(cache&&!cache.saved)setRecovery(cache);}
  }).catch(e=>setDiagnostics(errorDiagnostics(e)));},[refresh]);
  useEffect(()=>{if(!notice)return;const timer=setTimeout(()=>setNotice(''),3500);return()=>clearTimeout(timer);},[notice]);
  useEffect(()=>setTracePage(0),[run?.id]);
  useEffect(()=>{if(nodesInitialized)void flow.fitView({padding:.12,minZoom:.12,maxZoom:1});},[nodesInitialized,path,workflow?.id]);
  useEffect(()=>{const frame=requestAnimationFrame(()=>void flow.fitView({padding:.12,minZoom:.12,maxZoom:1}));return ()=>cancelAnimationFrame(frame);},[homeOpen,canvasFocus,sidebarOpen,inspectorOpen]);
  const changed=()=>{setReport('unvalidated');setRun(null);setDiagnostics([]);};
  const mutate=(change:(w:Workflow)=>Workflow)=>{if(workflow){setWorkflow(updateAt(workflow,path,change));changed();}};
  const select=(id:string)=>{setSelectedEdge('');setSelection(id);setEdgeFocus(id);setInspectorOpen(Boolean(id));};
  const openEditor=(value:NonNullable<typeof editor>)=>{setEditError('');setEditor(value);};
  const load=async(id:string,rev?:number)=>{setHomeOpen(false);try{await draft.flush();if(rev){const old=await api(`/workflows/${id}?revision=${rev}`);const data=await api('/workspace/workflows/'+id+'/copy',{name:old.workflow.name+' · r'+rev+' 历史副本',revision:rev,use_draft:false});adopt(data);await refresh();}else adopt(await api('/workspace/workflows/'+id));setHistoryOpen(false);setNotice(rev?'历史版本已复制为独立草稿，当前原流程编辑保留':'已恢复流程与自动保存草稿');}catch(e){setDiagnostics(errorDiagnostics(e));setNotice('切换未完成；当前编辑保留，请先处理草稿保存问题');}};
  const fromTemplate=async(kind:string)=>{try{await draft.flush();const data=await api('/examples/'+kind);adopt({workflow:{...data.workflow,id:'wf_'+crypto.randomUUID().replaceAll('-',''),name:data.workflow.name+' · 模板副本'},inputs:data.inputs,provenance:Object.fromEntries(Object.keys(data.inputs).map(k=>[k,'用户明确选择的模板示例（待核对）']))});setNotice('已创建模板副本；请核对示例值，原流程保持不变');}catch(e){setDiagnostics(errorDiagnostics(e));}};

  const openWorkspace=async(mode:NonNullable<typeof workspaceMode>,target?:WorkspaceItem)=>{document.querySelectorAll<HTMLDetailsElement>('.workflow-menu[open]').forEach(menu=>menu.open=false);try{setNotice('正在读取草稿并打开管理面板…');const item=target||saved.find(w=>w.id===workflow?.id)||{id:workflow!.id,name:workflow!.name,revision,draft_revision:0};if(mode!=='snapshot'){await draft.flush();const data=await api('/workspace/workflows/'+item.id);setWorkspaceTarget({...item,draft_revision:data.draft_revision});}else setWorkspaceTarget(item);setWorkspaceMode(mode);}catch(e){setDiagnostics(errorDiagnostics(e));setNotice('请先处理草稿保存失败；可从提示栏另存本页编辑');}};


  const graph=useMemo(()=>{
    if(!current)return {nodes:[] as Node<CanvasData>[],edges:[] as Edge[]};
    const nodes:Node<CanvasData>[]=[];const edges:Edge[]=[];
    const make=(id:string,kind:string,label:string,subtitle:string,ins:Record<string,Port>,outs:Record<string,Port>,fallback:{x:number;y:number})=>{
      const t=resultForDraft?run?.trace?.filter(t=>t.node_id===id).at(-1):undefined;
      nodes.push({id,type:'contract',position:current.layout?.[id]||fallback,selected:selection===id,deletable:!id.startsWith('$'),data:{dictionaryOnly:current.schema_version==='3.0',kind,label,subtitle,inputs:ins,outputs:outs,status:t?.status,duration:t?.duration_ms,invalid:diagnostics.some(d=>d.node_id===id)}});
    };
    make('$input','input','设计输入',`${Object.keys(current.inputs).length} 个声明参数`,{},current.inputs,{x:-30,y:100});
    current.nodes.forEach((n,i)=>{const p=ports(n,modules,current);make(n.id,n.kind,nodeDisplayName(n,modules),n.kind==='compute'?`${n.module_id} · v${n.module_version}`:n.kind==='grid'?'有限工况网格 · 显式笛卡尔积':n.kind==='batch'?'子图 · 数值字典逐行计算':n.kind==='foreach'?'子图 · 有界工况遍历':n.kind==='switch'?`${Object.keys(n.cases||{}).length} 个显式分支`: `${n.operation} · ${n.field||'value'}`,p.inputs,p.outputs,{x:330+i*330,y:120});});
    make('$output','output','计算结果',`${Object.keys(current.output_ports).length} 项输出`,current.output_ports,{}, {x:330+current.nodes.length*330,y:160});
    const add=(target:string,bindings:Record<string,Binding>)=>Object.entries(bindings).forEach(([key,b])=>{if(b.source==='constant')return;const source=b.source==='input'?'$input':b.node!;const invalid=diagnostics.some(d=>d.node_id===target&&d.port===key);const edgeId=`${target}:${key}`,focus=edgeFocus,focused=focus===source||focus===target||selectedEdge===edgeId,color=invalid?'#d75a69':focused?'#6958b5':'#8795aa';edges.push({id:edgeId,selected:selectedEdge===edgeId,source,target,sourceHandle:`${b.port}${b.field?'/'+b.field:''}`,targetHandle:key,type:'routed',data:{lane:edges.length,focused},zIndex:invalid?6:focused?5:0,animated:busy==='运行中'&&focused,markerEnd:{type:MarkerType.ArrowClosed,width:14,height:14,color},style:{stroke:color,strokeWidth:focused?2.2:1.4,opacity:invalid?1:focused?1:focus?.trim()?0.32:0.8},label:`${b.port}${b.field?'/'+b.field:''} → ${key}`});});
    current.nodes.forEach(n=>add(n.id,n.inputs));add('$output',current.outputs);
    return {nodes,edges};
  },[current,modules,run,resultForDraft,selection,diagnostics,busy,edgeFocus,selectedEdge]);

  const layoutScope=workflow?.id+':'+JSON.stringify(path);
  const fitLayout=()=>requestAnimationFrame(()=>requestAnimationFrame(()=>void flow.fitView({padding:.15,minZoom:.12,maxZoom:1})));
  function beautify(automatic=false){
    if(!workflow||!current)return;
    const measured=new Map(graph.nodes.map(n=>[n.id,flow.getInternalNode(n.id)]));
    const result=layeredLayout(graph.nodes.map(n=>({id:n.id,width:measured.get(n.id)?.measured?.width||245,height:measured.get(n.id)?.measured?.height||180})),graph.edges);
    setLayoutUndo({scope:layoutScope,before:structuredClone(current.layout||{})});
    setWorkflow(updateAt(workflow,path,w=>({...w,layout:result.positions})));
    autoLaidOut.current.add(layoutScope);fitLayout();
    if(!automatic)setNotice(result.cyclic?'布局已整理；图中存在循环，请另行检查连接。':'已按依赖美化当前画布；计算和连接未改变，可撤销或保存草稿。');
  }
  function undoLayout(){
    if(!workflow||!layoutUndo||layoutUndo.scope!==layoutScope)return;
    setWorkflow(updateAt(workflow,path,w=>({...w,layout:layoutUndo.before})));
    autoLaidOut.current.add(layoutScope);setLayoutUndo(null);fitLayout();setNotice('已撤销本次布局，其他修改保留。');
  }
  useEffect(()=>{
    if(homeOpen||!nodesInitialized||!current||autoLaidOut.current.has(layoutScope))return;
    const existing=Object.values(current.layout||{}).some(p=>Number.isFinite(p.x)&&Number.isFinite(p.y));
    if(!existing)beautify(true);
  },[nodesInitialized,homeOpen,layoutScope,current?.layout]);
  useEffect(()=>{setSelectedEdge('');setEdgeFocus('');},[layoutScope]);

  const connect=(c:Connection)=>{if(!c.source||!c.target||!c.sourceHandle||!c.targetHandle||c.target==='$input'||c.source==='$output')return;
    const b=fromId(`${c.source}|${c.sourceHandle}`);mutate(w=>c.target==='$output'?{...w,outputs:{...w.outputs,[c.targetHandle!]:b}}:{...w,nodes:w.nodes.map(n=>n.id===c.target?{...n,inputs:{...n.inputs,[c.targetHandle!]:b}}:n)});
  };
  const updateBinding=(key:string,b?:Binding)=>mutate(w=>{
    const map={...(selection==='$output'?w.outputs:w.nodes.find(n=>n.id===selection)!.inputs)};if(b)map[key]=b;else delete map[key];
    return selection==='$output'?{...w,outputs:map}:{...w,nodes:w.nodes.map(n=>n.id===selection?{...n,inputs:map}:n)};
  });
  const deleteNodes=(ids:string[])=>mutate(w=>({...w,nodes:w.nodes.filter(n=>!ids.includes(n.id))}));
  const removeEdges=(list:Edge[])=>mutate(w=>{let next={...w,outputs:{...w.outputs},nodes:w.nodes.map(n=>({...n,inputs:{...n.inputs}}))};for(const e of list){if(e.target==='$output')delete next.outputs[e.targetHandle!];else {const n=next.nodes.find(n=>n.id===e.target);if(n)delete n.inputs[e.targetHandle!];}}return next;});

  const check=async()=>{if(!workflow)return;setBusy('试读中');try{const parsed=normaliseValues(inputs,workflow.inputs);setInputs(parsed);await draft.flush(parsed);const r=await api('/validate',{workflow,inputs:parsed});setDiagnostics(r.diagnostics);setReport(r.valid?'valid':'invalid');setTab('diagnostics');setResultsOpen(true);}catch(e){setDiagnostics(errorDiagnostics(e));setReport('invalid');setTab('diagnostics');setResultsOpen(true);}finally{setBusy('');}};
  const execute=async()=>{if(!workflow)return;setBusy('运行中');const parsed=normaliseValues(inputs,workflow.inputs);setInputs(parsed);setRunSignature(JSON.stringify({workflow,inputs:parsed},(key,value)=>key==='layout'?undefined:value));setRun(null);try{await draft.flush(parsed);const r=await api('/runs',{workflow,inputs:parsed,timeout_seconds:60});setRun(r);setDiagnostics(r.diagnostics||[]);setReport(r.status==='succeeded'?'valid':'invalid');setTab(r.status==='succeeded'?'results':'diagnostics');setResultsOpen(true);await refresh();}catch(e){setDiagnostics(errorDiagnostics(e));setReport('invalid');setTab('diagnostics');setResultsOpen(true);}finally{setBusy('');}};
  const save=async()=>{if(!workflow)return;setBusy('保存中');try{const parsed=normaliseValues(inputs,workflow.inputs);setInputs(parsed);const dr=await draft.flush(parsed);const r=await api('/workflows',{workflow,inputs:parsed,expected_revision:revision,expected_draft_revision:dr});draft.hydrate({workflow,inputs:parsed,provenance:sources},0,r.revision);setRevision(r.revision);setDiagnostics(r.validation.diagnostics);setNotice(`已保存 r${r.revision} · 本地 SQLite`);await refresh();}catch(e){setDiagnostics(errorDiagnostics(e));setTab('diagnostics');setResultsOpen(true);}finally{setBusy('');}};
  const replay=async(id:string)=>{setRunSignature('');setBusy('回放中');try{const r=await api(`/runs/${id}/replay`,{});setRun(r);setDiagnostics(r.diagnostics||[]);setTab('results');setResultsOpen(true);await refresh();setNotice('已用原始快照回放，当前草稿不受影响');}catch(e){setDiagnostics(errorDiagnostics(e));}finally{setBusy('');}};
  const addModule=(m:Module)=>{const id=`${m.id}_${Date.now().toString(36)}`;mutate(w=>({...w,nodes:[...w.nodes,{id,label:moduleDisplayName(m),kind:'compute',module_id:m.id,module_version:m.version,inputs:{}}],layout:{...w.layout,[id]:{x:370,y:400}}}));setSelection(id);setNotice('已添加节点；连接声明输入后试读');};
  const addControl=(kind:'switch'|'foreach'|'aggregate')=>{
    const id=kind+'_'+Date.now().toString(36);
    const scalar:Port={type:'number'};
    const identity=(name:string,key:string):Workflow=>({schema_version:'1.0',id:name,name,inputs:{[key]:scalar},nodes:[],outputs:{value:{source:'input',port:key}},output_ports:{value:scalar},layout:{}});
    let node:WorkflowNode={id,label:kindName[kind],kind,inputs:{}};
    if(kind==='switch')node={...node,inputs:{selector:{source:'constant',value:'A'}},cases:{A:identity('branch_a','x'),B:identity('branch_b','x')}};
    if(kind==='foreach')node={...node,body:identity('loop_body','item')};
    if(kind==='aggregate')node={...node,operation:'mean'};
    mutate(w=>({...w,nodes:[...w.nodes,node],layout:{...w.layout,[id]:{x:340,y:420+w.nodes.length*45}}}));
    setSelection(id);setInspectorOpen(true);setNotice('已添加控制节点；请连接输入并检查子图契约');
  };
  const nested=(n:WorkflowNode,kind:'body'|'cases',key?:string)=>{setPath([...path,{id:n.id,kind,key}]);setSelection('$input');};
  const reviseModule=(m:Module)=>{const next=latestModules.find(x=>x.id===m.id)!;
    openEditor({kind:'revision',title:`修改公式 · ${m.id} v${next.version} → v${next.version+1}`,module:next,text:pretty({base_version:next.version,module:{...next,version:next.version+1},anchors:[{inputs:next.id==='switch_loss'?{i_rms:8,fs:100000,vce:1.7,e_sw:0.001,Tj:25}:scopedTrace?.inputs||Object.fromEntries(Object.entries(next.inputs).map(([k,p])=>[k,p.default??(p.type==='number'?0:p.type==='string'?'':p.type==='boolean'?false:{})])),expected:next.id==='switch_loss'?{p_cond:6.8,p_sw:100,p_total:106.8}:scopedTrace?.outputs||Object.fromEntries(Object.entries(next.outputs).map(([k,p])=>[k,p.type==='number'?0:p.type==='string'?'':p.type==='boolean'?false:{}])),tolerance:1e-8}]})});};

  const commitEditor=async()=>{if(!editor||!workflow)return;setEditError('');try{const data=JSON.parse(editor.text);
    if(editor.kind==='json'){await draft.flush();const next=data.workflow||data;if(next.id!==workflow.id){next.id='wf_'+crypto.randomUUID().replaceAll('-','');next.name=(next.name||'导入流程')+' · 导入副本';}if(!['1.0','2.0','3.0'].includes(next.schema_version)||!Array.isArray(next.nodes))throw Error('请导入 schema_version=1.0、2.0 或 3.0 的工作流');const checked=await api('/validate',{workflow:next,inputs:data.inputs||inputs});setWorkflow(checked.workflow);if(data.inputs)setInputs(data.inputs);setRevision(next.id===workflow.id?revision:0);setPath([]);setSelection('$input');changed();}
    if(editor.kind==='inputs'){setInputs(data);changed();}
    if(editor.kind==='module'){const r=await api(data.module?'/harness/submit-candidate':'/modules',data);if(r.status==='rejected'){setEditError(pretty(r.regression));return;}await refresh();setNotice('模块已登记，可从左侧添加到画布');}
    if(editor.kind==='definition'){await api('/parameter-definitions',data);setNotice('参数定义版本已登记；已保存的声明保持不变');}
    if(editor.kind==='revision'){
      setBusy('锚点回归中');const r=await api(`/modules/${editor.module!.id}/revisions`,data);
      if(r.status!=='applied'){setEditError(pretty(r.regression));return;}
      const replacement=r.module as Module;
      setWorkflow(updateAt(workflow,path,w=>({...w,nodes:w.nodes.map(n=>n.id===selection&&n.module_id===replacement.id?{...n,module_version:replacement.version}:n)})));changed();await refresh();setNotice(`锚点通过，已自动切换到 v${replacement.version}`);
    }
    if(editor.kind==='optimize'){setRunSignature(draftSignature);setBusy('DE 优化中');const r=await api('/optimize',{workflow,inputs,timeout_seconds:60,...data});setRun(r);setDiagnostics(r.diagnostics||[]);setTab('results');setResultsOpen(true);await refresh();}
    setEditor(null);
  }catch(e){setEditError(e instanceof Error?e.message:pretty(e));}finally{setBusy('');}};

  const jumpDiagnostic=(d:Diagnostic)=>{
    if(!workflow)return;
    const parts=d.path.split('/').filter(Boolean);const next:Location[]=[];let graph=workflow;let selectedId=d.node_id||(d.path.startsWith('/inputs')?'$input':'');
    for(let i=0;i<parts.length;i++){
      if(parts[i]!=='nodes')continue;const id=parts[++i];const n=graph.nodes.find(n=>n.id===id);if(!n)break;selectedId=id;
      if(parts[i+1]==='body'&&n.body){next.push({id,kind:'body'});graph=n.body;i++;}
      else if(parts[i+1]==='cases'&&n.cases?.[parts[i+2]]){const key=parts[i+2];next.push({id,kind:'cases',key});graph=n.cases[key];i+=2;}
    }
    setPath(next);setSelection(selectedId);setInspectorOpen(true);
  };

  const bindingOptions=useMemo(()=>{if(!current)return [];
    const options:{id:string;label:string;type:string}[]=[];
    const push=(id:string,ps:Record<string,Port>)=>Object.entries(ps).forEach(([key,p])=>{options.push({id:`${id}|${key}`,label:`${id==='$input'?'输入':id}.${key} ${p.unit||''}`,type:p.type});if(p.type==='flat_dict')Object.entries(p.fields||{}).forEach(([f,fp])=>options.push({id:`${id}|${key}/${f}`,label:`${id}.${key}.${f} ${fp.unit||''}`,type:fp.type}));});
    push('$input',current.inputs);current.nodes.filter(n=>n.id!==selection).forEach(n=>push(n.id,ports(n,modules,current).outputs));return options;
  },[current,modules,selection]);

  if(!workflow||!current)return <div className="loading-screen"><Zap/><h2>Power Electronics Workflow</h2>{diagnostics.length?<p>{diagnostics[0].message}</p>:<p>正在连接本地计算服务…</p>}<button onClick={()=>location.reload()}>重新连接</button></div>;
  const selectedPorts=selection==='$output'?current.output_ports:selected?ports(selected,modules,current).inputs:{};
  const selectedBindings=selection==='$output'?current.outputs:selected?.inputs||{};
  const scopedTrace=resultForDraft?run?.trace?.filter(t=>t.node_id===selection).at(-1):undefined;

  return <div className={`app-shell ${homeOpen?'home-shell':''} ${sidebarOpen?'':'sidebar-collapsed'} ${canvasFocus?'canvas-focus':''}`}>
    <aside className="sidebar"><div className="brand"><span className="brand-mark"><Zap size={22} fill="currentColor"/></span><div><b>PE-Workflow</b><span>POWER ELECTRONICS WORKFLOW</span></div></div>
      <div className="workspace-caption">电力电子计算工作空间</div>
      <nav className="workspace-nav" aria-label="主要导航"><button aria-current={homeOpen?'page':undefined} onClick={()=>setHomeOpen(true)}><Layers3 size={16}/>首页</button><button id="workflows-tab" aria-current={!homeOpen&&sidebarTab==='workflows'?'page':undefined} aria-controls="workflows-panel" onClick={()=>{setHomeOpen(false);setSidebarTab('workflows');}}><GitBranch size={16}/>工作流<span>{saved.length+(saved.some(w=>w.id===workflow.id)?0:1)}</span></button><button id="modules-tab" aria-current={!homeOpen&&sidebarTab==='modules'?'page':undefined} aria-controls="modules-panel" onClick={()=>{setHomeOpen(false);setSidebarTab('modules');}}><Box size={16}/>工具库<span>{latestModules.length}</span></button></nav>
      <section id="workflows-panel" role="region" aria-labelledby="workflows-tab" className="sidebar-panel" hidden={sidebarTab!=='workflows'}>
      <div className="section-heading sidebar-section-heading"><span>我的工作流</span><button className="new-workflow-button" onClick={()=>openWorkspace('new')}><Plus size={16}/>新建工作流</button><button title="回收站" onClick={()=>openWorkspace('trash')}><Trash2 size={16}/></button></div>
      <div className="catalog-search workflow-search"><Search size={14}/><input aria-label="搜索工作流" placeholder="搜索工作流…" value={workflowQuery} onChange={e=>setWorkflowQuery(e.target.value)}/></div>
      <nav className="workflow-scroll" aria-label="工作流列表" tabIndex={0}>
      {[{id:workflow.id,name:workflow.name,revision,draft_revision:0},...saved.filter(w=>w.id!==workflow.id)].filter(w=>w.name.toLowerCase().includes(workflowQuery.toLowerCase())).map(w=><div className={'workflow-row '+(w.id===workflow.id?'active':'')} key={w.id}><button title={w.name} className={'workflow-link '+(w.id===workflow.id?'active':'')} onClick={()=>{setHomeOpen(false);if(w.id===workflow.id){setPath([]);select('$input');}else void load(w.id);}}><GitBranch size={15}/><span>{w.name}</span><span className="workflow-revision">{w.revision?'r'+w.revision:'草稿'}{w.draft_revision?' · 编辑中':''}</span></button><details className="workflow-menu"><summary aria-label={w.name+' 操作'}><MoreHorizontal size={15}/></summary><div><button onClick={()=>openWorkspace('rename',w)}>重命名</button><button onClick={()=>openWorkspace('copy',w)}>复制独立方案</button><button onClick={()=>openWorkspace('archive',w)}>移入回收站</button></div></details></div>)}
      {!saved.some(w=>w.name.toLowerCase().includes(workflowQuery.toLowerCase()))&&!workflow.name.toLowerCase().includes(workflowQuery.toLowerCase())&&<p className="sidebar-empty">没有匹配的工作流</p>}
      </nav>
      <details className="sidebar-utilities"><summary><FolderOpen size={14}/>示例与参数工具<ChevronDown size={13}/></summary>
      <button className="workflow-link" onClick={()=>void fromTemplate('dbsrc')}><Layers3 size={16}/><span>DBSRC EPS 模板副本</span><span className="mini-tag">真实链</span></button>
      <button className="workflow-link" onClick={()=>void fromTemplate('dictionaries')}><Braces size={16}/><span>双字典电容模板副本</span><span className="mini-tag">v3</span></button>
      <button className="workflow-link" onClick={()=>api('/parameter-catalogs/team_parameters/versions/1').then(d=>download('parameter-catalog.json',d)).catch(e=>setDiagnostics(errorDiagnostics(e)))}><Search size={16}/><span>导出参数对齐词典</span></button>
      <button className="workflow-link" onClick={()=>void fromTemplate('waveform')}><Activity size={16}/><span>波形积分模板副本</span><span className="mini-tag">v2</span></button>
      <button className="workflow-link" onClick={()=>openEditor({kind:'definition',title:'新增参数定义版本',text:pretty({id:'current.rms',version:1,name:'有效值电流',port:{kind:'bare',type:'number',quantity:'current',characteristic:'rms',unit:'A'},aliases:[],provenance:'用户明确声明'})})}><Braces size={16}/><span>参数定义登记</span></button>
      </details></section>
      <section id="modules-panel" role="region" aria-labelledby="modules-tab" className="sidebar-panel" hidden={sidebarTab!=='modules'}>
      <div className="section-heading catalog-heading"><span>计算工具</span></div>
      <button className="workflow-link module-upload" onClick={()=>openDesign('tool')}><Upload size={16}/><span>上传并注册计算工具</span></button>
      <div className="catalog-search"><Search size={14}/><input aria-label="搜索模块" placeholder="搜索模块…" value={query} onChange={e=>setQuery(e.target.value)}/></div>
      <ModuleLibrary formulas={formulaRecords.items} modules={modules} families={families} query={query} onAdd={addModule} onRefresh={()=>void refresh().catch(e=>setDiagnostics(errorDiagnostics(e)))}/>
      <details className="control-catalog"><summary><Layers3 size={14}/>高级与流程控制<ChevronDown size={13}/></summary><div className="control-grid"><button onClick={()=>addControl('foreach')}><Layers3 size={16}/>工况遍历</button><button onClick={()=>addControl('switch')}><GitBranch size={16}/>条件分支</button><button onClick={()=>addControl('aggregate')}><Sigma size={16}/>指标汇总</button></div><button className="advanced-module-register" title="高级：手动登记模块 JSON" onClick={()=>openEditor({kind:'module',title:'登记纯计算模块',text:pretty({module:{contract_version:'3.0',id:'square_model',version:1,name:'平方计算 · 字典接口',inputs:{data:{type:'flat_dict',fields:{x:{type:'number',quantity:'scalar',unit:'1'}}}},outputs:{result:{type:'flat_dict',fields:{y:{type:'number',quantity:'scalar',unit:'1'}}}},implementation:'python_function',entrypoint:'square',expressions:{},source:'def square(x):\n    return x * x\n',adapter:{arguments:{x:{from:['inputs','data','x']}},returns:{result:{assemble:{y:{from:['return']}}}}},provenance:'用户提供的纯计算代码'},anchors:[{inputs:{data:{x:2}},expected:{result:{y:4}}},{inputs:{data:{x:3}},expected:{result:{y:9}}}]})})}><Braces size={14}/>手动登记工具（JSON）</button></details>
      </section>
      <div className="sidebar-bottom"><ShieldCheck size={16}/><div><strong>计算在本地完成</strong><small>公式、数据与结果可追溯</small></div><span className="online-dot"/></div>
    </aside>
    <div className="main-shell"><header className="topbar"><button className="icon-button" title={sidebarOpen?'收起模块库':'展开模块库'} aria-expanded={sidebarOpen} onClick={()=>setSidebarOpen(!sidebarOpen)}><Layers3 size={18}/></button><button className="outline-button" onClick={()=>setHomeOpen(true)}>首页</button><div className="crumb"><span>工作流</span><ChevronRight size={14}/><strong>{workflow.name}</strong><span className="draft-badge">{revision?'r'+revision:'未保存'}</span></div><div className="top-actions"><span className={'draft-save-state '+(draft.error?'error':'')}>草稿{draft.state}</span><span className="local-status"><span/>本地运行</span><button className="icon-button" title="导入 JSON" onClick={()=>importFile.current?.click()}><Upload size={17}/></button><button className="icon-button" title="导出 JSON" onClick={()=>download(workflow.id+'.json',{workflow,inputs})}><ArrowDownToLine size={17}/></button><button className="outline-button" disabled={!!busy} onClick={save}><Save size={15}/>保存为版本</button></div></header>
      {homeOpen&&<section className="task-home" aria-label="任务首页"><div className="home-heading"><small>Power Electronics Workflow · 电力电子计算工作空间</small><h1>今天要完成什么？</h1><p>先选择任务。已有流程、计算工具和当前画布也可以从侧栏直接打开。</p></div><div className="home-routes"><button onClick={()=>{setHomeOpen(false);setSidebarTab('workflows');setSidebarOpen(true);select('$input');}}><Play/><strong>运行已有流程</strong><span>选择流程 → 核对输入与单位 → 运行并查看结果</span></button><button onClick={()=>openDesign('workflow')}><GitBranch/><strong>用自然语言搭建流程</strong><span>描述目标 → 确认计算方案 → 自动连接并保存</span></button><button onClick={()=>openDesign('tool')}><Upload/><strong>上传计算工具</strong><span>提供纯计算函数 → 核对声明与样例 → 注册到工具库</span></button></div><section className="home-recent"><h2>选择一个工作流开始</h2><p>打开后先核对输入。示例数据不代表你的实际工况。</p>{saved.slice(0,6).map(w=><button key={w.id} onClick={()=>void load(w.id)}><GitBranch size={17}/><span>{w.name}</span><small>打开流程 →</small></button>)}{!saved.length&&<p>暂时没有保存的流程，可先描述需求搭建，或从侧栏创建空白流程。</p>}</section></section>}
      <div className={homeOpen?'workbench-stage hidden-stage':'workbench-stage'}>
      <div className="project-header"><div><div className="project-kicker">WORKFLOW STUDIO <span>01 / LOCAL LAB</span></div><h1>{workflow.name}{workflow.id==='thermal_demo'&&<span className="title-badge">DEMO</span>}</h1><p>{current.nodes.length} 个计算步骤 · {Object.keys(workflow.inputs).length} 组运行输入 · 点击节点查看用途</p>{workflow.description&&<details className="workflow-purpose"><summary>流程用途与适用范围</summary><p>{workflow.description}</p></details>}</div><div className="run-actions"><button className="outline-button" disabled={!!busy} onClick={()=>openDesign('workflow')}><GitBranch size={16}/>AI 搭建工作流</button><button className="outline-button" disabled={!!busy} onClick={check}><ShieldCheck size={16}/>检查输入与连接</button><button className="outline-button" onClick={()=>{setPath([]);select('$input');}}>填写运行输入</button><button className="primary-button" disabled={!!busy} onClick={execute}>{busy?<Loader2 size={16} className="spin"/>:<Play size={16} fill="currentColor"/>}{busy||'运行计算'}</button></div></div>
      <div className="workspace-body"><div className="canvas-and-results"><div className="canvas-toolbar"><div className="canvas-tabs"><button className="active" onClick={()=>{setPath([]);setSelection('$input');}}><GitBranch size={14}/>画布</button><button onClick={()=>openEditor({kind:'json',title:`JSON 工作流 · 版本 ${workflow.schema_version}`,text:pretty({workflow,inputs})})}><Braces size={14}/>JSON</button></div><div className="canvas-meta"><div className="focus-run"><button className="outline-button" disabled={!!busy} onClick={check}><ShieldCheck size={14}/>试读</button><button className="primary-button" disabled={!!busy} onClick={execute}><Play size={14}/>{busy||'运行'}</button></div><button className="layout-action" title="按实际依赖分层排列当前画布，不改变连接" onClick={()=>beautify()}><Layers3 size={15}/>一键美化布局</button><button className="icon-button" title="撤销本次布局" aria-label="撤销本次布局" disabled={!layoutUndo||layoutUndo.scope!==layoutScope} onClick={undoLayout}><Undo2 size={15}/></button><button className="icon-button" title="适应视图" onClick={()=>void flow.fitView({padding:.12,minZoom:.12,maxZoom:1})}><Maximize2 size={16}/></button><button className="icon-button" title={canvasFocus?'退出专注画布':'专注画布'} aria-pressed={canvasFocus} onClick={()=>setCanvasFocus(!canvasFocus)}><Maximize2 size={16}/></button><button className="icon-button inspector-toggle" title="参数与节点属性" onClick={()=>setInspectorOpen(!inspectorOpen)}><Settings2 size={16}/></button><span className={`validation-state ${report}`}>{report==='valid'?<CheckCircle2 size={13}/>:<Circle size={12}/>} {report==='valid'?'试读通过':report==='invalid'?'需要修复':'尚未试读'}</span><span>{current.nodes.length} 个节点</span><button className="icon-button" title="版本历史" onClick={async()=>{setVersions(await api(`/workflows/${workflow.id}/history`));setHistoryOpen(true);}}><History size={16}/></button></div></div>
        {path.length>0&&<div className="subgraph-nav"><button onClick={()=>{setPath(path.slice(0,-1));setSelection('$input');}}><ArrowLeft size={14}/>返回上层</button><span>{workflow.name}{path.map(p=><span key={p.id}> / {p.id}{p.key?' · '+p.key:''}</span>)}</span><span className="subgraph-tag">子图</span></div>}
        <div className="canvas"><ReactFlow nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onEdgeClick={(_,e)=>{setSelectedEdge(e.id);setEdgeFocus('');}} zoomOnDoubleClick={false} onConnect={connect} onNodeClick={(_,n)=>select(n.id)} onPaneClick={()=>select('')} onNodeDragStop={(_,n)=>{setLayoutUndo(null);if(workflow)setWorkflow(updateAt(workflow,path,w=>({...w,layout:{...w.layout,[n.id]:n.position}})));}} onNodesDelete={ns=>deleteNodes(ns.map(n=>n.id))} onEdgesDelete={removeEdges} onNodeDoubleClick={(_,n)=>{const item=current.nodes.find(x=>x.id===n.id);if(item?.body)nested(item,'body');else if(item?.cases)nested(item,'cases',Object.keys(item.cases)[0]);}} fitView fitViewOptions={{padding:.12,minZoom:.12,maxZoom:1}} minZoom={.1} maxZoom={2} proOptions={{hideAttribution:true}} deleteKeyCode={['Backspace','Delete']}><Background variant={BackgroundVariant.Dots} color="#d4d8e7" gap={22} size={1}/><Controls showInteractive={false}/><MiniMap nodeColor={n=>n.id==='$input'?'#a8b6df':n.data.kind==='aggregate'?'#91bcb1':'#b0a2d6'} pannable zoomable/></ReactFlow>
          <div className="canvas-caption"><span className="caption-icon"><Layers3 size={14}/></span><span>点击空白显示全部连线 · 双击控制节点展开子图</span></div>
        </div>
        <section className={`results-panel ${resultsOpen?'':'collapsed'}`} style={{height:resultsOpen?resultHeight:44}}>{resultsOpen&&<div className="results-resize" role="separator" aria-label="调整结果区高度" aria-orientation="horizontal" tabIndex={0} aria-valuemin={120} aria-valuemax={480} aria-valuenow={resultHeight} onKeyDown={e=>{if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();setResultHeight(h=>Math.max(120,Math.min(480,h+(e.key==='ArrowUp'?20:-20))));}}} onPointerDown={e=>{e.currentTarget.setPointerCapture(e.pointerId);e.currentTarget.dataset.start=String(e.clientY);e.currentTarget.dataset.height=String(resultHeight);}} onPointerMove={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))setResultHeight(Math.max(120,Math.min(480,Number(e.currentTarget.dataset.height)+Number(e.currentTarget.dataset.start)-e.clientY)));}} onPointerUp={e=>e.currentTarget.releasePointerCapture(e.pointerId)}/>}<div className="results-tabs"><div>{[['results','运行结果',Activity],['diagnostics','诊断',ShieldCheck],['trace','节点追踪',Layers3],['history','运行记录',History]].map(([id,label,Icon])=>{const I=Icon as typeof Activity;return <button key={id as string} className={tab===id?'active':''} onClick={()=>{setTab(id as string);setResultsOpen(true);}}><I size={14}/>{label as string}{id==='diagnostics'&&diagnostics.length>0&&<span className="count-badge">{diagnostics.length}</span>}</button>;})}</div><button className="result-toggle" title={resultsOpen?'收起结果区':'展开结果区'} aria-expanded={resultsOpen} onClick={()=>setResultsOpen(!resultsOpen)}>{resultsOpen?<ChevronDown size={16}/>:<ChevronRight size={16}/>}</button><span className="result-status">{run?.status==='succeeded'?<><span className="online-dot"/>计算完成</>:run?.status==='failed'?'计算失败':'等待运行'}</span></div>
          <div className="results-content">
            {tab==='results'&&(run?<><RunSummary run={run} current={resultForDraft}/><details><summary>工况明细、优化记录与完整输出</summary><div className="metric-grid">{Object.entries(run.outputs||{}).filter(([,v])=>typeof v==='number').map(([k,v])=><div className="metric" key={k}><span>{k==='mean_loss'?'平均总损耗':k==='worst_loss'?'最大总损耗':k}</span><strong>{(v as number).toFixed(3)}<small>{run.output_ports?.[k]?.unit||workflow.output_ports[k]?.unit}</small></strong><em>{k}</em></div>)}</div>{Array.isArray(run.outputs?.workpoint_results)&&<table className="result-table"><thead><tr><th>工况</th><th>导通损耗 / W</th><th>开关损耗 / W</th><th>总损耗 / W</th></tr></thead><tbody>{(run.outputs.workpoint_results as Record<string,number>[]).map((r,i)=><tr key={i}><td><span className="table-index">{String(i+1).padStart(2,'0')}</span>工况 {i+1}</td><td>{r.p_cond?.toFixed(3)}</td><td>{r.p_sw?.toFixed(3)}</td><td className="accent-text">{r.p_total?.toFixed(3)}</td></tr>)}</tbody></table>}{run.optimization&&<div className="optimization-summary"><b>DE · {run.optimization.evaluations} 次评价</b><span>{run.optimization.converged?'达到收敛判据':'达到本次预算；不代表已全局收敛'}</span><code>{pretty(run.optimization.best_parameters)}</code></div>}{Object.values(run.outputs||{}).some(v=>typeof v!=='number')&&<pre className="actual-values">{pretty(run.outputs)}</pre>}</details><div className="run-provenance">{!resultForDraft&&<b>历史快照结果 · </b>}<ShieldCheck size={13}/><span>快照 {run.snapshot_hash?.slice(0,12)} · 模块版本与输入已冻结</span><button onClick={()=>download('run-'+run.id+'.json',run)}>导出结果</button></div>{run.status==='failed'&&<p className="failure-text">运行失败，没有生成有效结果。查看诊断定位问题。</p>}</>:<div className="empty-results"><span><Activity size={22}/></span><div><strong>每一次计算，都有据可查</strong><p>先试读输入与连接，再运行计算。这里将显示指标、工况明细和版本快照。</p></div><button onClick={execute} disabled={!!busy}><Play size={14}/>运行当前流程</button></div>)}
            {tab==='diagnostics'&&<p>检查未通过时请先修正下方问题，再运行。草稿保存状态见顶部；可在节点追踪中检查执行到哪一步。</p>}{tab==='diagnostics'&&!!diagnostics.length&&<details><summary>查看原始诊断与错误路径</summary><pre>{pretty(diagnostics)}</pre></details>}{tab==='diagnostics'&&(diagnostics.length?diagnostics.map((d,i)=><button className={`diagnostic ${d.severity}`} key={i} onClick={()=>jumpDiagnostic(d)}><span>{d.severity==='error'?'!':'i'}</span><div><strong>{d.message}</strong><small>{d.node_id?'步骤：'+d.node_id:d.path.startsWith('/inputs/')?'参数：'+d.path.slice(8).split('/').join('.'):'输入或流程检查'} · 点击定位处理</small>{d.suggestion&&<p>{d.suggestion}</p>}</div><ChevronRight size={14}/></button>):<div className="empty-inline"><ShieldCheck size={22}/>{report==='valid'?'试读通过，未发现阻断项。':'试读后显示格式、连接、类型与语义诊断。'}</div>)}
            {tab==='trace'&&(run?.trace?.length?<><div className="run-provenance"><span>共 {run.trace.length} 步 · 第 {tracePage+1}/{Math.ceil(run.trace.length/200)} 页</span><button disabled={!tracePage} onClick={()=>setTracePage(p=>p-1)}>上一页</button><button disabled={(tracePage+1)*200>=run.trace.length} onClick={()=>setTracePage(p=>p+1)}>下一页</button></div><table className="result-table"><thead><tr><th>节点路径</th><th>状态</th><th>耗时</th><th>输出</th></tr></thead><tbody>{run.trace.slice(tracePage*200,(tracePage+1)*200).map((t,i)=><tr key={tracePage*200+i} onClick={()=>setSelection(t.node_id)}><td><code>{t.path}</code></td><td><span className={t.status==='succeeded'?'success-text':'failure-text'}>{t.status==='succeeded'?'完成':'失败'}</span></td><td>{t.duration_ms} ms</td><td><code>{JSON.stringify(t.outputs).slice(0,100)}</code></td></tr>)}</tbody></table></>:<div className="empty-inline"><Layers3 size={22}/>运行后逐节点查看实际输入、输出与耗时。</div>)}
            {tab==='history'&&(history.length?<div className="history-list">{history.map(r=><div key={r.id}><span className={`status-dot ${r.status==='succeeded'?'green':''}`}/><div><strong>{r.workflow_name||workflow.name}</strong><small>{new Date(r.created_at).toLocaleString('zh-CN')} · {r.snapshot_hash.slice(0,10)} · {r.status}</small></div><button disabled={!!busy} onClick={()=>replay(r.id)}><RefreshCw size={13}/>快照回放</button></div>)}</div>:<div className="empty-inline"><History size={22}/>运行记录将自动保存在本地。</div>)}
          </div>
        </section>
      </div>
      <aside className={`inspector ${inspectorOpen?'inspector-open':''}`} aria-hidden={!inspectorOpen}><div className="inspector-heading"><Settings2 size={16}/><strong>{selection==='$input'?'设计输入':selection==='$output'?'输出绑定':selected?'节点属性':'工作流信息'}</strong><button className="icon-button inspector-toggle" title="收起属性" onClick={()=>setInspectorOpen(false)}><X size={16}/></button>{selected&&<button className="icon-button" title="删除节点" onClick={()=>{deleteNodes([selected.id]);setSelection('');}}><Trash2 size={15}/></button>}</div>
        <div className="inspector-content">
          {selection==='$input'?<><div className="inspector-intro"><span className="soft-icon"><ArrowRight size={21}/></span><h2>{path.length?'子图输入契约':'设计参数'}</h2><p>{path.length?'值由父图显式传入。返回上层连接参数。':'填写本次工况与设计值；必须与标注单位一致。没有填写的值不会自动按零计算。'}</p></div>{!path.length?<><details><summary>修改工作流输入输出声明</summary><div className="form-inline"><button onClick={()=>setDeclaration('input')}>添加输入字典</button><button onClick={()=>setDeclaration('output')}>添加输出字典</button></div></details><ValueForm key={workflow.id} focusable ports={current.inputs} values={inputs} sources={sources} onChange={v=>{setInputs(v);changed();}} onSourceChange={key=>setSources(old=>({...old,[key]:'用户编辑（原有值请一并核对）'}))}/><details><summary>高级：完整输入 JSON</summary><button className="wide-button" onClick={()=>openEditor({kind:'inputs',title:'编辑运行输入 · 高级 JSON',text:pretty(inputs)})}>打开 JSON 编辑器</button></details></>:Object.entries(current.inputs).map(([k,p])=><DictionaryDeclaration key={k} name={k} port={p}/>)}{['thermal_demo','waveform_demo'].includes(workflow.id)&&<div className="assumption-note"><ShieldCheck size={16}/><strong>演示模型假设</strong><p>{workflow.id==='waveform_demo'?'10 V 恒压、0–2 A 线性电流、持续 2 ms，积分应为 0.02 J；仅验证接口与数值执行。':'初始演示系数为 1.8 mV/℃、导通系数为 0.5。修改后以模块版本内的公式为准；这些假设不代表真实器件标定。'}</p></div>}</>:
          <>{selected?<><div className={`inspector-intro ${selected.kind}`}><span className="soft-icon">{(()=>{const I=kindIcons[selected.kind];return <I size={22}/>;})()}</span><h2>{nodeDisplayName(selected,modules)}</h2><p>{selected.module_id?`${selected.module_id} · v${selected.module_version}`:kindName[selected.kind]}</p></div><div className="input-field"><label>显示名称</label><input value={nodeDisplayName(selected,modules)} onChange={e=>mutate(w=>({...w,nodes:w.nodes.map(n=>n.id===selected.id?{...n,label:e.target.value}:n)}))}/></div>{selected.body&&<button className="wide-button" onClick={()=>nested(selected,'body')}><Layers3 size={14}/>展开遍历子图<ChevronRight size={14}/></button>}{selected.cases&&<div className="branch-list">{Object.keys(selected.cases).map(k=><button key={k} onClick={()=>nested(selected,'cases',k)}><GitBranch size={14}/>{k}<ChevronRight size={14}/></button>)}</div>}{selected.kind==='aggregate'&&<><div className="input-field"><label>汇总方式</label><select value={selected.operation} onChange={e=>mutate(w=>({...w,nodes:w.nodes.map(n=>n.id===selected.id?{...n,operation:e.target.value as WorkflowNode['operation']}:n)}))}>{['mean','max','min','sum'].map(op=><option key={op}>{op}</option>)}</select></div><div className="input-field"><label>汇总字段</label><input value={selected.field||''} onChange={e=>mutate(w=>({...w,nodes:w.nodes.map(n=>n.id===selected.id?{...n,field:e.target.value||undefined}:n)}))}/></div></>}{currentModule&&<><section className="node-overview"><strong>这个模块做什么</strong><p>{currentModule.description||moduleDisplayName(currentModule)}</p><p>需要：{Object.keys(currentModule.inputs).join('、')||'无输入'}<br/>输出：{Object.keys(currentModule.outputs).join('、')||'无输出'}</p><FormulaView record={(!path.length&&formulaRecords.workflow_id===workflow?.id&&formulaRecords.nodes[selected!.id]?.module_ref===`${currentModule.id}@${currentModule.version}`?formulaRecords.nodes[selected!.id]:formulaRecords.items[`${currentModule.id}@${currentModule.version}`])}/><details><summary>查看实际计算代码</summary><pre>{currentModule.source||pretty(currentModule.expressions)||'冻结内置实现，请查看模型来源。'}</pre></details></section><details className="module-provenance"><summary>模型来源、实现与适配依据</summary><span>模型来源</span><p>{currentModule.provenance}</p><p>实现：{currentModule.implementation}{currentModule.entrypoint?` · ${currentModule.entrypoint}`:''}</p>{!!currentModule.capabilities?.length&&<p>能力：{currentModule.capabilities.join('、')}</p>}{currentModule.adapter!==undefined&&currentModule.adapter!==null&&<details className="contract-details"><summary>查看输入 / 输出适配声明</summary><pre>{pretty(currentModule.adapter)}</pre></details>}{!!currentModule.transforms?.length&&<details className="contract-details"><summary>查看显式变换与前提</summary><pre>{pretty(currentModule.transforms)}</pre></details>}</details><button className="wide-button" onClick={()=>reviseModule(currentModule)}><Code2 size={14}/>派生新版本：手动修改代码</button>{currentModule.contract_version==='3.0'&&<button className="wide-button" onClick={()=>setAutoTarget({module:currentModule,path:[...path],nodeId:selected!.id})}><Upload size={14}/>上传并自动适配</button>}<button className="wide-button" onClick={()=>setChangeTarget({module:currentModule,path:[...path],nodeId:selected!.id})}><Code2 size={14}/>派生新版本：用语言修改</button><button className="wide-button" onClick={async()=>{try{const r=await api('/harness/prepare-change',{workflow:current,inputs:path.length?undefined:inputs,node_id:selected!.id,instruction:'保留此节点的外部接口，依据明确声明修改单个计算函数；缺少场景信息时集中提问一次。'});if(r.status!=='prepared'){setDiagnostics(r.validation?.diagnostics||[]);return;}download('dsh-context-'+selected!.id+'.json',r);setNotice('DSH 任务上下文已导出；接口尚未连接模型');}catch(e){setDiagnostics(errorDiagnostics(e));}}}><Braces size={14}/>导出 DSH 修改上下文</button></>}</>:selection==='$output'?<div className="inspector-intro"><span className="soft-icon"><ArrowDownToLine size={22}/></span><h2>结果输出</h2><p>声明结果来源与单位，输出契约会在运行后再次校验。</p><button className="wide-button" onClick={()=>setDeclaration('output')}>添加输出字典</button></div>:<><div className="inspector-intro"><span className="soft-icon"><GitBranch size={22}/></span><h2>声明驱动的计算</h2><p>选择节点检查参数绑定，双击控制节点进入子图。</p></div><button className="wide-button" onClick={()=>setSelection('$input')}><Settings2 size={14}/>编辑设计输入</button></>}
          {(selected||selection==='$output')&&<><div className="inspector-section-title">输入来源 <span>{Object.keys(selectedPorts).length}</span></div>{Object.entries(selectedPorts).map(([k,p])=>{const b=selectedBindings[k];return <div className="binding-field" key={k}><label><span className={`tiny-dot ${b?'bound':''}`}/>{k}<small>{p.unit||p.type}</small></label><select aria-label={`${k} 来源`} value={bindingId(b)} onChange={e=>updateBinding(k,e.target.value==='constant'?{source:'constant',value:p.type==='flat_dict'?{}:''}:e.target.value?fromId(e.target.value):undefined)}><option value="">未连接</option><option value="constant">显式常量</option>{bindingOptions.filter(o=>p.type!=='flat_dict'||o.type==='flat_dict').map(o=><option value={o.id} key={o.id}>{o.label}</option>)}</select>{b?.source==='constant'&&p.type==='flat_dict'&&<ValueEditor port={p} value={b.value} label={k+' 常量'} onChange={value=>updateBinding(k,{source:'constant',value})}/>}{b?.source==='constant'&&p.type!=='flat_dict'&&<ValueEditor port={p} value={b.value} label={k+' 常量'} onChange={value=>updateBinding(k,{source:'constant',value})}/>}<small className="semantic-line">{p.type==='array'&&`${p.value_type?.dtype} [${p.value_type?.shape.join(' × ')}] · `}{[p.quantity,p.characteristic].filter(Boolean).join(' · ')||'通用数据契约'}</small></div>})}</>}
          {Object.entries(selectedPorts).map(([k,p])=><DictionaryDeclaration key={k} name={k} port={p}/>)}
          {currentModule&&Object.entries(currentModule.outputs).map(([k,p])=><DictionaryDeclaration key={'out'+k} name={'输出 '+k} port={p}/>)}
          {scopedTrace&&<div className="actual-values"><div className="inspector-section-title">实际接收字段</div><pre>{pretty(scopedTrace.inputs)}</pre><div className="inspector-section-title">字段映射</div><pre>{pretty(selectedBindings)}</pre></div>}
          {scopedTrace&&<div className="actual-values"><div className="inspector-section-title">最近一次实际输出</div><pre>{pretty(scopedTrace.outputs)}</pre></div>}</>}
        </div><div className="inspector-bottom"><button disabled={!Object.values(workflow.inputs).some(p=>p.type==='number')||!Object.values(workflow.output_ports).some(p=>p.type==='number')} onClick={()=>openEditor({kind:'optimize',title:'DE 参数优化 · 预算与变量',text:pretty({bounds:Object.fromEntries(Object.entries(workflow.inputs).filter(([,p])=>p.type==='number').slice(0,1).map(([k])=>{const v=Number(inputs[k])||1;return [k,[v*.8,v*1.2].sort((a,b)=>a-b)];})),objective:Object.entries(workflow.output_ports).find(([,p])=>p.type==='number')?.[0]||'',direction:'min',seed:42,maxiter:5,popsize:5})})}><Activity size={14}/>配置 DE 优化<ChevronRight size={14}/></button></div>
      </aside></div><footer className="statusbar"><span><span className="online-dot"/>Python 计算服务</span><span>JSON 格式 {workflow.schema_version} · {workflow.nodes.length} 个顶层节点</span><span>DSH 自动适配 · 后台任务</span><button onClick={()=>api('/harness/registration-contract').then(d=>download('dsh-registration-contract.json',d)).catch(e=>setDiagnostics(errorDiagnostics(e)))}>接口说明<ArrowRight size={12}/></button></footer>
    </div>
    <input type="file" accept=".json,application/json" ref={importFile} hidden onChange={async e=>{const file=e.target.files?.[0];if(file)openEditor({kind:'json',title:'导入 JSON 工作流',text:await file.text()});e.target.value='';}}/>
    {notice&&<div className="toast"><Check size={16}/>{notice}</div>}
    </div>
    {editor&&<div className="modal-backdrop"><div className="editor-modal"><header><div><Braces size={19}/><strong>{editor.title}</strong></div><button className="icon-button" title="关闭编辑器" onClick={()=>setEditor(null)}><X size={20}/></button></header><p>{editor.kind==='revision'?'模块不可覆盖。锚点通过后派生新版本，并更新草稿中的对应引用；仅更新选中节点；修改输入契约后请重新接线和试读。':editor.kind==='module'?'支持原有模块 JSON；函数适配请提交 {module, anchors}，使用 python_function、entrypoint 和 adapter。仅允许受控数学、NumPy 和已登记 SciPy 函数。':'JSON 是计算关系的唯一来源。修改后先试读，再执行。'}</p><textarea spellCheck={false} aria-label="JSON 编辑器" value={editor.text} onChange={e=>setEditor({...editor,text:e.target.value})}/>{editError&&<pre className="editor-error">{editError}</pre>}<footer><span>所有内容保存在本地</span><button className="outline-button" onClick={()=>setEditor(null)}>取消</button><button className="primary-button" disabled={!!busy} onClick={commitEditor}>{busy?<Loader2 className="spin" size={14}/>:<Check size={14}/>} {editor.kind==='revision'?'验证锚点并应用':editor.kind==='optimize'?'开始优化':'应用修改'}</button></footer></div></div>}
    {changeTarget&&<ChangeDialog workflow={workflow} inputs={inputs} revision={revision} path={changeTarget.path} nodeId={changeTarget.nodeId} module={changeTarget.module} onClose={()=>setChangeTarget(null)} onApplied={data=>{draft.hydrate({workflow:data.workflow,inputs:data.inputs,provenance:sources},0,data.revision);setWorkflow(data.workflow);setInputs(data.inputs);setRevision(data.revision);setRun(data.run);setRunSignature(JSON.stringify({workflow:data.workflow,inputs:data.inputs},(key,value)=>key==='layout'?undefined:value));setReport('valid');setDiagnostics(data.run.diagnostics||[]);setTab('results');setResultsOpen(true);setNotice(`修改任务通过，已保存 r${data.revision} 并运行`);void refresh();}}/>}
    {autoTarget&&<AutomationDialog workflow={workflow} inputs={inputs} revision={revision} path={autoTarget.path} nodeId={autoTarget.nodeId} module={autoTarget.module} onClose={()=>{setAutoTarget(null);void api('/module-families').then(d=>setFamilies(d.items));}} onApplied={data=>{draft.hydrate({workflow:data.workflow,inputs:data.inputs,provenance:sources},0,data.revision);setAutoTarget(null);setWorkflow(data.workflow);setInputs(data.inputs);setRevision(data.revision);setRun(data.run);setRunSignature(JSON.stringify({workflow:data.workflow,inputs:data.inputs},(key,value)=>key==='layout'?undefined:value));setReport('valid');setDiagnostics(data.run.diagnostics||[]);setTab('results');setResultsOpen(true);setNotice(`修改任务通过，已保存 r${data.revision} 并运行`);void refresh();}}/>}

    {(draft.error||draft.recoveryError)&&<div className="draft-alert" role="alert">{draft.error||draft.recoveryError}<button onClick={()=>void draft.flush().catch(()=>{})}>重试保存</button><button onClick={()=>void openWorkspace('snapshot')}>另存为独立方案</button></div>}
    {recovery&&<div className="draft-alert">发现本机未保存编辑。<button onClick={()=>{const p=recovery.payload;adopt({workflow:{...p.workflow,id:'wf_'+crypto.randomUUID().replaceAll('-',''),name:p.workflow.name+' · 本机恢复'},inputs:p.inputs,provenance:p.provenance});setRecovery(undefined);setNotice('已恢复为独立草稿，原流程不被覆盖');}}>恢复为独立草稿</button><button onClick={()=>setRecovery(undefined)}>保留服务端内容</button></div>}
    {workspaceMode&&workspaceTarget&&<WorkspaceDialog mode={workspaceMode} items={saved.some(w=>w.id===workflow.id)?saved:[{id:workflow.id,name:workflow.name,revision,draft_revision:0},...saved]} trash={trash} selected={workspaceTarget} snapshot={{workflow,inputs,provenance:sources}} onClose={()=>setWorkspaceMode(null)} onLoad={adopt} onFlush={draft.flush} onAI={()=>setAssemblyOpen(true)} onRefresh={async()=>{const r=await refreshWorkspace();if(workspaceMode==='archive'&&workspaceTarget.id===workflow.id){if(r.workflows.length)adopt(await api('/workspace/workflows/'+r.workflows[0].id));else adopt({workflow:{schema_version:'3.0',id:'wf_'+crypto.randomUUID().replaceAll('-',''),name:'新的工作流',inputs:{},nodes:[],outputs:{},output_ports:{},layout:{}},inputs:{}});}return r;}}/>}
    {declaration&&<DeclarationDialog kind={declaration} workflow={current} modules={modules} onClose={()=>setDeclaration(null)} onAdd={(name,port,binding)=>{mutate(w=>declaration==='input'?{...w,inputs:{...w.inputs,[name]:port}}:{...w,outputs:{...w.outputs,[name]:binding!},output_ports:{...w.output_ports,[name]:port}});setDeclaration(null);setInspectorOpen(true);}}/>}
    {assemblyOpen&&<Suspense fallback={<div className="modal-backdrop"><div className="change-modal">正在加载计算方案设计器…</div></div>}><DesignDialog initialMode={designMode} onClose={()=>setAssemblyOpen(false)} onOpen={w=>{setHomeOpen(false);setInspectorOpen(true);draft.hydrate({workflow:w,inputs:{},provenance:{}},0,1);setSources({});setAssemblyOpen(false);setWorkflow(w);setInputs({});setRevision(1);setPath([]);setSelection('$input');setRun(null);setReport('valid');setDiagnostics([]);setResultsOpen(false);setNotice('已打开结构检查通过的新工作流；运行前请配置实际输入');void refresh();}} onLegacy={data=>{draft.hydrate({workflow:data.workflow,inputs:data.inputs,provenance:{}},0,data.revision);setSources({});setAssemblyOpen(false);setWorkflow(data.workflow);setInputs(data.inputs);setRevision(data.revision);setPath([]);setSelection('$input');setRun(data.run);setRunSignature(JSON.stringify({workflow:data.workflow,inputs:data.inputs},(key,value)=>key==='layout'?undefined:value));setReport('valid');setDiagnostics([]);setResultsOpen(true);setNotice('已打开 DSH 装配的新工作流；工程适用性待核对');void refresh();}}/></Suspense>}
    {historyOpen&&<div className="modal-backdrop"><div className="history-modal"><header><h3>工作流版本</h3><button className="icon-button" title="关闭版本历史" onClick={()=>setHistoryOpen(false)}><X size={18}/></button></header>{versions.length?versions.map(v=><div className="version-row" key={v.revision}><span>r{v.revision}</span><small>{new Date(v.created_at).toLocaleString('zh-CN')}</small><button onClick={()=>load(workflow.id,v.revision)}>复制此版本为草稿</button></div>):<p>保存后将建立不可覆盖的版本历史。</p>}</div></div>}
  </div>;
}

export default function App(){return <ReactFlowProvider><Workbench/></ReactFlowProvider>;}
