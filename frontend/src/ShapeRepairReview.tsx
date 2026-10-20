import {useState} from 'react';

export default function ShapeRepairReview({taskId,revision,onApplied}:{taskId:string;revision:number;onApplied:(task:any)=>void}){
 const [view,setView]=useState<any>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function request(path:string,body?:unknown){const r=await fetch('/api/workflow-designs/'+taskId+'/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const d=await r.json();if(!r.ok)throw Error(d.diagnostics?.[0]?.message||d.detail||'接口修订未完成');return d;}
 async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
 return <section className="continue-task-card"><h3>按已声明来源修正输入类型与维度</h3><p>适用于循环输出是数组、下游却声明成单值等接口问题。先查看差异，不调用 DSH、不增加额度。</p>
 <button disabled={busy} onClick={()=>void act(async()=>setView(await request('shape-repair-preview')))}>查看接口修正预览（不调用模型）</button>
 {error&&<p role="alert">{error}</p>}
 {view&&view.revision===revision&&<>{!view.changes.length?<p>没有可按明确来源自动修正的类型差异；请查看原诊断。</p>:<><p>找到 {view.changes.length} 项接口差异。</p><details><summary>查看逐字段类型、维度与来源差异</summary><ul>{view.changes.map((c:any,i:number)=><li key={i}><code>{c.step_id} / {c.port}.{c.field}</code>：{c.before.type} → {c.after.type}；维度 {JSON.stringify(c.before.value_type?.shape||[])} → {JSON.stringify(c.after.value_type?.shape||[])}<details><summary>来源与轴名差异</summary><pre>{JSON.stringify(c,null,2)}</pre></details></li>)}</ul></details><p>只修正类型、维度与轴；公式、单位、语义、必填性、来源及保护条件保留。注册工具需要派生接口，不修改原工具。</p>{view.valid?<><p>修正后结构检查通过；尚未验证数值或物理正确性。</p><button className="primary-button" disabled={busy} onClick={()=>void act(async()=>onApplied(await request('shape-repair-confirm',{revision:view.revision,patch_hash:view.patch_hash})))}>确认这些接口差异，打开派生修订</button></>:<><p>以下问题仍阻塞修正，不会保存或调用模型：</p><ul>{view.issues.map((d:any,i:number)=><li key={i}>{d.path}：{d.message}</li>)}</ul></>}</>}</>}
 </section>;
}
