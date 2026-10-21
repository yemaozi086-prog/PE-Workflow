import {useEffect,useState} from 'react';
import {recoverySummary} from './completionView';
type Field={path:string;old:string;proposed:string;source:string|null;message:string};
type Item={task_id:string;revision:number;preview_hash:string;step_id:string;title?:string;action:string;remaining_calls:number;calls:number;fields:Field[];missing:{field:string;message:string}[];report:{code?:string;message?:string}};
type Preview={revision:number;preview_hash:string;items?:Item[]} & Partial<Item>;
async function api(path:string,body?:unknown){const r=await fetch('/api'+path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.diagnostics?.[0]?.message||d.detail||d.message||'操作失败，请刷新核对最新任务');return d;}
const actions:Record<string,string>={revalidate_candidate:'复检原候选：不改接口、不调用 AI',declarations:'需要确认声明草案',preparation_retry:'平台准备问题已修复，可按原额度恢复',candidate_repair:'可用剩余额度修正候选',keep:'保持当前状态',stopped:'达到停止条件，需要另行批准'};
export default function CompletionRecovery({taskId,queueId,revision,onUpdated,onOpen}:{taskId?:string;queueId?:string;revision:number;onUpdated:(result:any)=>void;onOpen?:(id:string)=>void}){
 const [view,setView]=useState<Preview|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[ack,setAck]=useState(false),[values,setValues]=useState<Record<string,string>>({});
 const path=queueId?'/tool-completion-queues/'+queueId+'/recovery':'/workflow-designs/'+taskId+'/declaration-recovery';
 useEffect(()=>{let live=true;setView(null);setAck(false);setError('');setValues({});void api(path).then(d=>{if(!live)return;setView(d);if(taskId)setValues(Object.fromEntries((d.fields||[]).map((f:Field)=>[f.path,f.proposed])));}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[path,revision]);
 useEffect(()=>setNotice(''),[path]);
 async function act(fn:()=>Promise<void>){if(busy)return;setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
 if(taskId&&view?.action!=='declarations'&&!error)return null;
 const items=view?.items||[];
 const summary=recoverySummary(items);
 return <section className="recovery-guide" aria-label={queueId?'下一步：处理计算工具':'补充字段含义'}><h4>{queueId?'下一步：处理计算工具':'补充字段含义'}</h4>{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {queueId&&<>
  {!view&&!error&&<p role="status">正在读取各工具的处理方式，不调用 AI…</p>}
  {view&&<>
   <div className="recovery-summary">
    {summary.recheck>0&&<div><strong>{summary.recheck}</strong><span>已有代码可重新检查</span><small>不改接口 · 不调用 AI</small></div>}
    {summary.model>0&&<div><strong>{summary.model}</strong><span>需要 AI 修正代码</span><small>使用剩余额度，最多 {summary.maximumCalls} 次</small></div>}
    {summary.declarations>0&&<div><strong>{summary.declarations}</strong><span>需要补充声明</span><small>打开对应工具逐项核对</small></div>}
   </div>
   {summary.actionable>0?<div className="recovery-next-action">
    <p>{summary.model>0?`本次会重新检查已有代码，并让 AI 修正 ${summary.model} 个工具。`:`先重新检查这 ${summary.recheck} 份已有代码，无需先缩小接口，也不会重新生成代码。`}{summary.declarations>0?'缺少声明的工具会保留，随后单独处理。':''}</p>
    {summary.model>0&&<><p>本次最多使用 {summary.maximumCalls} 次已有额度，不追加。各工具上限见下面清单。</p><label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>我确认使用原剩余额度，让 AI 修正清单中的代码问题。</label></>}
    <button className="primary-button" disabled={busy||(summary.model>0&&!ack)} onClick={()=>void act(async()=>{const result=await api(path,{revision:view.revision,preview_hash:view.preview_hash,confirm:true});setAck(false);setNotice('处理已完成或已排队，请查看下方各工具的最新状态。复检通过的候选仍需你核对后注册。');onUpdated(result);setView(await api(path));})}>{busy?'正在处理，请稍候…':summary.label}</button>
    <small>本操作不注册工具、不运行工作流。</small>
   </div>:<p>{summary.declarations>0?'请先补充下方工具的声明，再继续生成。':'当前没有可批量恢复的操作，请按下方各工具状态继续。'}</p>}
   {summary.declarations>0&&<div className="recovery-supplements"><h5>随后处理：补充声明</h5>{items.filter(i=>i.action==='declarations').map(i=><div key={i.task_id}><span>{i.title||i.step_id}</span><small>缺少 {(i.missing||[]).length} 项声明 · 本步骤已用 {i.calls} 次调用</small>{onOpen&&<button disabled={busy} onClick={()=>onOpen(i.task_id)}>打开并核对</button>}</div>)}</div>}
   <details className="completion-advanced"><summary>查看逐项动作与原始诊断</summary><ul>{items.map(i=><li key={i.task_id}><strong>{i.title||i.step_id}</strong>：{actions[i.action]||'请查看记录'}；已用 {i.calls} 次，剩余 {i.remaining_calls} 次。{i.report?.code&&<details><summary>原始停止原因</summary><p>{i.report.code}：{i.report.message}</p></details>}{i.missing?.map((m,n)=><p key={n}>{m.field}：{m.message}</p>)}</li>)}</ul></details>
  </>}
  <button className="text-button" disabled={busy} onClick={()=>void act(async()=>{setView(await api(path));setAck(false);})}>刷新处理清单（不调用 AI）</button>
 </>}

 {!queueId&&view?.action==='declarations'&&<><p>以下草案仅复制原字段说明，不调用模型，也不代表数值验证。核对后确认；无来源字段请明确填写，暂不清楚的可以留空待补。</p>{view.fields?.map(f=><label className="change-label" key={f.path}><strong>{f.path}</strong><span>原语义：{f.old||'空缺'}；来源：{f.source||'无明确来源，需要你提供'}</span><input value={values[f.path]||''} onChange={e=>{setValues({...values,[f.path]:e.target.value});setAck(false);}}/></label>)}{view.missing?.filter(m=>!view.fields?.some(f=>f.path===m.field)).map(m=><p key={m.field}>{m.field}：{m.message}。本入口只补字段语义，其他声明需在方案修订中补充。</p>)}<p>只补充已选字段的含义；公式、字段名称、必填性、单位、约束和验收答案保持冻结。剩余 {view.remaining_calls} 次调用，确认本草案不增加额度。</p><label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>我确认以上非空字段的含义，派生修订并检查后继续；空缺字段保留待补充。</label><button disabled={busy||!ack||!Object.values(values).some(v=>v.trim())} onClick={()=>void act(async()=>{const result=await api(path,{revision:view.revision,preview_hash:view.preview_hash,confirm:true,quantities:Object.fromEntries(Object.entries(values).filter(([,v])=>v.trim()))});setNotice(result.message||'已保存新修订');setAck(false);onUpdated(result);})}>确认声明，派生修订并按剩余额度继续</button></>}
 </section>;
}
