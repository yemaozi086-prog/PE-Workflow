import {useState} from 'react';
import MathFormula from './MathFormula';

export default function InputUsageReview({queueId,onUpdated}:{queueId:string;onUpdated:(queue:any)=>void}){
 const [view,setView]=useState<any>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function api(path:string,body?:unknown){const r=await fetch('/api/tool-completion-queues/'+queueId+'/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const d=await r.json();if(!r.ok)throw Error(d.diagnostics?.[0]?.message||d.detail||'依赖修订未完成');return d;}
 async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
 const eligible=view?.items.filter((i:any)=>i.eligible)||[];
 return <section className="continue-task-card"><h3>核对工具真正需要哪些输入</h3><p>区分公式计算、保护检查与上游携带字段。预览不调用模型；确认后派生修订并复检已有代码，不自动注册。</p>
 <button disabled={busy} onClick={()=>void act(async()=>setView(await api('input-usage-preview')))}>查看本批输入依赖修订预览</button>
 {error&&<p role="alert">{error}</p>}
 {view&&<><p>{eligible.length} 个工具可核对修订；其余问题单独保留。公式与代码、单位、保护条件和上游数据不变。</p>{view.items.map((i:any)=><details key={i.task_id}><summary>{i.title} · {i.eligible?i.review.changes.length+' 项本工具输入不再要求提供':'需单独核对'} · 剩余 {i.remaining} 次</summary>{i.eligible?<><p>移出工具契约的字段仍在上游和工作流中保留，其他工具照常使用。字段读取只提供依赖证据，仍需对照公式核对。</p><ul>{i.review.roles.map((r:any)=><li key={r.field}><code>{r.field}</code>：{({calculation:'参与计算',check:'用于保护检查',calculation_and_check:'计算与保护检查',carried:'上游携带，本工具不要求'} as Record<string,string>)[r.role]}</li>)}</ul><MathFormula source={i.review.formula}/><details><summary>查看保持不变的候选代码</summary><pre>{i.review.code}</pre></details><p>保留的保护条件：</p><pre>{i.review.checks.join('\n')||'无已声明保护条件'}</pre><details><summary>查看字段声明差异</summary><pre>{JSON.stringify(i.review.changes,null,2)}</pre></details></>:<p>{i.reason}</p>}</details>)}
 {!view.plan_check.valid&&<><p>合并修订后还有接线问题，不能确认：</p><pre>{JSON.stringify(view.plan_check.issues,null,2)}</pre></>}
 <button className="primary-button" disabled={busy||!eligible.length||!view.plan_check.valid} onClick={()=>void act(async()=>{onUpdated(await api('input-usage-confirm',{revision:view.revision,preview_hash:view.preview_hash,confirm:true}));setView(null);})}>{busy?'正在处理…':`确认这 ${eligible.length} 个工具的依赖差异，复检已有候选`}</button><p>保留旧任务、额度与独立样例原文；复检只投影工具所需输入，预期输出不变；不重新生成已通过工具。没有独立样例的仍标记为数值未验证。</p></>}
 </section>;
}
