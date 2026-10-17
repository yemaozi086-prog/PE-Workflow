import {Box,Plus,RefreshCw} from 'lucide-react';
import type {Module} from './types';
import {moduleDisplayName} from './domainNames';

export type ModuleFamily={id:string;name:string;versions:{version:number;name:string;description:string;lineage?:{parent_version:number;summary:string;acceptance_label?:string}|null;uses:{name:string;workflow_id:string;node_path:string[]}[]}[];candidates:{task_id:string;version?:number;parent_version:number;state:string;summary:string;message:string;diagnostics:unknown[]}[]};
const states:Record<string,string>={queued:'待处理',preparing:'准备中',dispatch_ready:'待生成',creating_session:'连接 DSH',dispatching:'提交中',awaiting_worker:'生成中',validating:'验证中',applying:'验证与试运行',failed:'失败',blocked:'依据不足',needs_clarification:'待澄清',needs_evidence:'待验收依据',version_changed:'版本冲突',delivery_unknown:'调用待核实'};

export default function ModuleLibrary({modules,families,query,onAdd,onRefresh}:{modules:Module[];families:ModuleFamily[];query:string;onAdd:(m:Module)=>void;onRefresh:()=>void}){
  const matches=families.filter(f=>[f.id,f.name,...modules.filter(m=>m.id===f.id).map(m=>moduleDisplayName(m)+m.name),...f.candidates.map(c=>c.summary)].join(' ').toLowerCase().includes(query.toLowerCase()));
  return <div className="module-catalog" role="region" aria-label="计算模块列表" tabIndex={0}><button className="library-refresh" onClick={onRefresh}><RefreshCw size={13}/>刷新版本与候选状态</button>{matches.map(f=>{
    const versions=[...f.versions].sort((a,b)=>b.version-a.version);
    const base=modules.filter(m=>m.id===f.id).sort((a,b)=>a.version-b.version)[0];
    return <details className="module-family" key={f.id}><summary><Box size={18}/><span><strong>{base?moduleDisplayName(base):f.name}</strong><small>{f.id}</small><em>{versions.length} 个可用版本{f.candidates.length?` · ${f.candidates.length} 个候选任务`:''}</em></span></summary><div className="module-versions" aria-label={`${f.id} 版本列表`}>
      {versions.map(v=>{const m=modules.find(m=>m.id===f.id&&m.version===v.version);return <article className="module-version" key={v.version}><div className="version-heading"><b>v{v.version}</b><span className="version-status">可用</span>{m&&<button title={`添加 ${f.id} v${v.version} 到画布`} onClick={()=>onAdd(m)}><Plus size={14}/>添加</button>}</div><strong>{m?moduleDisplayName(m):v.name}</strong><small>{v.lineage?`派生自 v${v.lineage.parent_version}`:v.version===1?'初始登记版本':'派生来源未记录'}</small>{v.lineage?.summary&&<p>{v.lineage.summary}</p>}{v.lineage?.acceptance_label&&<p className="version-evidence">{v.lineage.acceptance_label}</p>}{!!v.uses.length&&<details className="version-uses"><summary>{v.uses.length} 处当前工作流引用</summary>{v.uses.map((u,i)=><small key={i}>{u.name} · {u.node_path.join(' / ')}</small>)}</details>}</article>})}
      {!!f.candidates.length&&<div className="candidate-section"><b>候选区 · 不可添加到工作流</b>{f.candidates.map(c=><article className="module-candidate" key={c.task_id}><strong>{c.version?`候选 v${c.version}`:'待生成候选'} · {states[c.state]||c.state}</strong><small>基于 v{c.parent_version} · 任务 {c.task_id.slice(0,8)}</small><p>{c.message||c.summary}</p><details><summary>查看任务说明与诊断</summary><p>{c.summary}</p><pre>{JSON.stringify(c.diagnostics,null,2)}</pre></details></article>)}</div>}
    </div></details>;
  })}{!matches.length&&<p className="sidebar-empty">没有匹配的模块族</p>}</div>;
}
