import {useState} from 'react';

async function api(path:string,body?:unknown){
  const r=await fetch('/api'+path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(!r.ok)throw Error(d.diagnostics?.[0]?.message||d.detail||'结构修复操作失败');
  return d;
}

export default function StructureRepair({taskId,revision,onUpdated}:{taskId:string;revision:number;onUpdated:(result:any)=>void}){
  const [view,setView]=useState<any>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function act(fn:()=>Promise<void>){if(busy)return;setBusy(true);setError('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <section className="design-step" aria-label="平台结构错放修复">
    <h4>平台结构修复 · 不调用模型、不追加额度</h4>
    <p>只把原始候选中的已知顶层成员从错误层级移回正确位置。公式、端口、单位、依赖和保护条件保持不变，然后重新检查原始方案。</p>
    <button disabled={busy} onClick={()=>void act(async()=>setView(await api('/workflow-designs/'+taskId+'/structure-repair-preview')))}>读取原始候选结构修复预览</button>
    {error&&<p role="alert">{error}</p>}
    {view&&<>
      {!view.available&&<p>{view.reason||'没有可自动移动的已知结构错放。'}</p>}
      {!!view.moves?.length&&<><p>将移动以下内容：</p><ul>{view.moves.map((m:any,i:number)=><li key={i}><code>{m.from}</code> → <code>{m.to}</code></li>)}</ul></>}
      {!!view.issues?.length&&<><p role="alert">复检后仍有阻断问题，不能自动生成修订：</p><ul>{view.issues.map((i:any,n:number)=><li key={n}><code>{i.path||'/'}</code>：{i.message}</li>)}</ul></>}
      {view.valid&&view.plan&&<p role="status">结构移动后方案复检通过，共 {view.plan.steps?.length||0} 个步骤；原始回执 {String(view.source_receipt_hash).slice(0,12)}…</p>}
      {view.available&&<button className="primary-button" disabled={busy||!view.valid} onClick={()=>void act(async()=>onUpdated(await api('/workflow-designs/'+taskId+'/structure-repair-confirm',{revision:view.revision,preview_hash:view.preview_hash,confirm:true})))}>{busy?'正在生成结构修复修订…':'确认移动并复检原始方案'}</button>}
      <details><summary>查看移动与复检依据</summary><pre>{JSON.stringify(view,null,2)}</pre></details>
    </>}
  </section>;
}
