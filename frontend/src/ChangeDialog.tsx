import { useEffect, useState } from 'react';
import { ArrowDownToLine, Check, Loader2, Play, X } from 'lucide-react';
import type { Location, Module, Run, Workflow } from './types';
import { pretty } from './types';

async function request(path:string,body?:unknown){
  const r=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await r.json();if(!r.ok)throw data;return data;
}
function download(name:string,data:unknown){const url=URL.createObjectURL(new Blob([pretty(data)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();URL.revokeObjectURL(url);}
type Task={id:string;context_id:string;status:string;label:string;context:Record<string,unknown>;questions?:Record<string,string>;demo_candidate?:unknown;[key:string]:unknown};

export default function ChangeDialog({workflow,inputs,revision,path,nodeId,module,onClose,onApplied}:{workflow:Workflow;inputs:Record<string,unknown>;revision:number;path:Location[];nodeId:string;module:Module;onClose:()=>void;onApplied:(data:{workflow:Workflow;inputs:Record<string,unknown>;revision:number;run:Run})=>void}){
  const eligible=module.id==='switch_loss'&&!!module.inputs.Tj&&!module.inputs.alpha;
  const [demo,setDemo]=useState<boolean>(eligible);
  const [instruction,setInstruction]=useState(eligible?'将导通压降公式改为 Vce(Tj)=vce+alpha*(Tj-25)，alpha=0.002 V/℃，新增 alpha 输入并连接显式常量；保持开关损耗公式不变。':'');
  const [alpha,setAlpha]=useState('0.002');
  const [anchors,setAnchors]=useState('[]');const [bindings,setBindings]=useState('{}');const [unchanged,setUnchanged]=useState('[]');
  const [task,setTask]=useState<Task|null>(null);const [candidate,setCandidate]=useState('');const [answers,setAnswers]=useState<Record<string,string>>({});
  const [busy,setBusy]=useState('');const [error,setError]=useState('');const [diagnostic,setDiagnostic]=useState<unknown>(null);
  const [recent,setRecent]=useState<Task[]>([]);
  const nodePath=path.flatMap(p=>p.kind==='body'?[p.id,'body']:[p.id,'cases',p.key!]).concat(nodeId);
  useEffect(()=>{request('/change-tasks').then(data=>setRecent(data.filter((t:Task)=>t.workflow_id===workflow.id&&JSON.stringify(t.node_path)===JSON.stringify(nodePath)))).catch(e=>setError(pretty(e)));},[]);
  async function restore(id:string){try{const data=await request(`/change-tasks/${id}`);setTask(data);setCandidate(pretty(data.demo_candidate?{...(data.demo_candidate as object),context_id:data.context_id}:{context_id:data.context_id,module:{...(data.context.base_module as Module),version:data.context.next_module_version},added_bindings:data.context.authorized_added_bindings}));}catch(e){setError(pretty(e));}}
  async function prepare(){setBusy('准备上下文与基准运行');setError('');setDiagnostic(null);try{
    const data=await request('/change-tasks',{workflow,inputs,expected_revision:revision,node_path:nodePath,instruction,alpha_demo:demo,alpha:demo&&alpha.trim()?Number(alpha):null,anchors:demo?[]:JSON.parse(anchors),added_bindings:demo?{}:JSON.parse(bindings),unchanged_outputs:demo?[]:JSON.parse(unchanged),timeout_seconds:60});
    if(data.status==='needs_clarification'){setError(Object.values(data.questions).join('\n'));return;}
    if(data.status!=='awaiting_candidate'){setDiagnostic(data);return;}
    setTask(data);setCandidate(pretty(data.demo_candidate||{context_id:data.context_id,module:{...(data.context.base_module as Module),version:data.context.next_module_version},added_bindings:data.context.authorized_added_bindings}));
  }catch(e){setError(e instanceof Error?e.message:pretty(e));}finally{setBusy('');}}
  async function apply(){if(!task)return;setBusy('验证模块、整图并保存运行');setError('');setDiagnostic(null);try{
    const data=await request(`/change-tasks/${task.id}/apply`,JSON.parse(candidate));
    if(data.status!=='applied'){setDiagnostic(data);return;}
    onApplied(data);setTask({...task,...data});setDiagnostic({status:'applied',revision:data.revision,module:data.module_ref,before:data.baseline_outputs,after:data.after_outputs,regression:data.regression,label:data.label});
  }catch(e){setError(e instanceof Error?e.message:pretty(e));}finally{setBusy('');}}
  async function reloadTask(){if(!task)return;try{const data=await request(`/change-tasks/${task.id}`);setTask(data);setCandidate(text=>{try{return pretty({...JSON.parse(text),context_id:data.context_id});}catch{return text;}});}catch(e){setError(pretty(e));}}
  async function answer(){if(!task)return;setBusy('提交本轮回答');setError('');try{const data=await request(`/change-tasks/${task.id}/answer`,{answers});setTask(data);setCandidate(text=>pretty({...JSON.parse(text),context_id:data.context_id}));}catch(e){setError(pretty(e));}finally{setBusy('');}}
  return <div className="modal-backdrop"><div className="change-modal" role="dialog" aria-modal="true" aria-label="自然语言修改任务"><header><div><strong>自然语言修改任务</strong><small>只修改选中节点 · {nodePath.join(' / ')} · {module.id} v{module.version}</small></div><button className="icon-button" title="关闭修改任务" disabled={!!busy} onClick={onClose}><X size={20}/></button></header>
    <div className="change-scroll"><div className="change-notice">此处为手动候选与手算模板验收入口。自动调用 DSH 请使用节点属性中的“上传并自动适配”。</div>
      {!task?<>{recent.length>0&&<label className="change-label">恢复此节点的已有任务<select aria-label="恢复修改任务" defaultValue="" onChange={e=>{if(e.target.value)void restore(e.target.value);}}><option value="">创建新任务</option>{recent.map(t=><option key={t.id} value={t.id}>{t.id.slice(0,10)} · {t.status}</option>)}</select></label>}<label className="change-label">本次修改指令<textarea aria-label="修改指令" value={instruction} onChange={e=>setInstruction(e.target.value)} rows={4}/></label>
        {eligible&&<label className="change-checkbox"><input type="checkbox" checked={demo} onChange={e=>setDemo(e.target.checked)}/>使用 α 手算演示模板（待课题组核对）</label>}
        {demo?<><label className="change-label">明确给出的 α / V·℃⁻¹<input aria-label="alpha 数值" type="number" step="0.0001" value={alpha} onChange={e=>setAlpha(e.target.value)}/></label><p>仅支持此模板的 0.002。留空将集中追问，不自动补值。25 ℃：106.8 W；125 ℃：107.6 W；两点的开关损耗均为 100 W。</p><p>手算模板只执行上述确定公式；若指令要求不同公式，请关闭模板并使用 DSH 候选。</p></>:<><label className="change-label">独立验收样例 anchors · 至少两组、覆盖全部输出<textarea aria-label="验收样例" rows={5} value={anchors} onChange={e=>setAnchors(e.target.value)}/></label><label className="change-label">用户明确授权的新增接线 added_bindings<textarea aria-label="新增接线" rows={3} value={bindings} onChange={e=>setBindings(e.target.value)}/></label><label className="change-label">必须保持不变的输出名 · JSON 数组<textarea aria-label="保持不变的输出" rows={2} value={unchanged} onChange={e=>setUnchanged(e.target.value)}/></label></>}
      </>:<><div className="change-task-meta"><span>任务 {task.id.slice(0,10)} · {task.status}</span><button onClick={()=>download(`dsh-change-${task.id}.json`,task)}><ArrowDownToLine size={14}/>导出完整任务</button><button onClick={reloadTask}>读取 DSH 任务状态</button></div><p>{task.label}。候选模块、接线与整图均通过后，才保存新版本并发布运行结果。</p>
        {task.status==='needs_clarification'?<div>{Object.entries(task.questions||{}).map(([key,q])=><label className="change-label" key={key}>{q}<input value={answers[key]||''} onChange={e=>setAnswers({...answers,[key]:e.target.value})}/></label>)}<button className="outline-button" onClick={answer} disabled={!!busy}>提交本轮全部回答</button></div>:<label className="change-label">{task.demo_candidate?'手算模板候选 · 可检查接口 JSON':'DSH 候选 JSON · 请粘贴真实候选，当前内容仅为结构骨架'}<textarea aria-label="修改候选 JSON" rows={15} value={candidate} disabled={task.status==='applied'} onChange={e=>setCandidate(e.target.value)}/></label>}
        <details className="contract-details"><summary>查看基准、验收依据与执行上下文</summary><pre>{pretty(task.context)}</pre></details></>}
      {error&&<pre className="editor-error" role="alert">{error}</pre>}{diagnostic!==null&&<pre className="change-diagnostic">{pretty(diagnostic)}</pre>}
    </div><footer><span>{busy||'失败保留原图；不会替换其他节点引用'}</span>{!task?<button className="primary-button" disabled={!!busy||!instruction.trim()} onClick={prepare}>{busy?<Loader2 size={16} className="spin"/>:<Check size={16}/>}准备修改任务</button>:task.status!=='applied'&&<button className="primary-button" disabled={!!busy||task.status==='needs_clarification'} onClick={apply}>{busy?<Loader2 size={16} className="spin"/>:<Play size={16}/>}验证、保存并运行</button>}</footer>
  </div></div>;
}
