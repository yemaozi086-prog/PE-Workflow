import {useState} from 'react';
import {pretty} from './types';

export type PrecheckSummary={attempts:number;limit:number;remaining:number;status:string;latest?:{status:string;message?:string;code?:string;path?:string};notice:string};
type Record={id:string;attempt:number;status:string;seconds:number;code?:{source?:string;expressions?:unknown};report?:unknown;at:string};

export default function FormulaPrecheckPanel({taskId,summary,status,closed}:{taskId:string;summary:PrecheckSummary;status:string;closed?:boolean}){
 const [history,setHistory]=useState<Record[]>([]),[error,setError]=useState('');
 async function load(){try{const r=await fetch('/api/workflow-designs/'+taskId+'/formula-prechecks');if(!r.ok)throw Error('预检记录读取失败，请重试');setHistory((await r.json()).items);setError('');}catch(e){setError(String(e));}}
 if(!summary.attempts)return null;
 const label=summary.status==='stale'?'任务已变化，此预检未应用':summary.status==='running'?'平台检查中':status==='formula_candidate'?'候选已提交，等待人工核对':status==='formula_registered'?'已核对注册':summary.status==='passed'?'预检通过，等待正式提交':closed?'已暂停，请核对原因':'检查未通过，智能体可局部修正';
 return <section className="design-step" role="status" aria-label="工具代码预检"><strong>工具代码预检 · {label}</strong><p>本次模型调用内已预检 {summary.attempts}/{summary.limit} 次。预检不额外增加模型调用，不自动注册。</p>{summary.latest?.message&&<p>{summary.latest.message}</p>}<details onToggle={e=>{if(e.currentTarget.open)void load();}}><summary>查看代码修改与预检记录</summary>{error&&<p role="alert">{error}</p>}{history.map(r=><article key={r.id}><h4>第 {r.attempt} 次预检 · {r.status==='passed'?'通过':r.status==='running'?'检查中':'未通过'}</h4><p>{new Date(r.at).toLocaleString()} · {r.seconds} 秒</p><pre>{r.code?.source||pretty(r.code?.expressions)}</pre><pre>{pretty(r.report)}</pre></article>)}<p>{summary.notice}</p></details></section>;
}
