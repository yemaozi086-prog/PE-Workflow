import {useState} from 'react';
type Proposal={kind:'unit'|'parameter'|'alias';name:string;description:string;evidence:string;unit_definition?:{dimension:string;scale:number;offset:number}|null;declaration?:Record<string,unknown>|null;alias_target?:string|null};
export type ExtensionRequest={id:string;revision:number;project_id:string;status:string;missing:string[];proposal:Proposal};
export default function ExtensionReview({request:r,onSave,onDecision}:{request:ExtensionRequest;onSave:(p:Proposal)=>Promise<void>;onDecision:(approve:boolean)=>Promise<void>}){
 const [p,setP]=useState(r.proposal);
 const d=p.unit_definition||{dimension:'',scale:1,offset:0};
 return <section className="design-step"><h3>补充项目自定义声明：{p.name}</h3><p>保存到项目 {r.project_id} 的自定义表，标准表保持原样。待补充：{r.missing.join('、')||'无；请核对后批准'}。</p>
 <label>含义和用途<textarea value={p.description} onChange={e=>setP({...p,description:e.target.value})}/></label>
 <label>定义或换算依据<textarea value={p.evidence} placeholder="填写可信来源、定义和换算关系；不确定时保持待定" onChange={e=>setP({...p,evidence:e.target.value})}/></label>
 {p.kind==='unit'&&<><label>量纲标识<input value={d.dimension} placeholder="例如 thermal_resistance" onChange={e=>setP({...p,unit_definition:{...d,dimension:e.target.value}})}/></label><p>换算约定：基准值 = 输入值 × 倍率 + 偏移。温差与绝对温度须区分。</p><label>倍率<input type="number" value={d.scale} onChange={e=>setP({...p,unit_definition:{...d,scale:e.target.value===''?NaN:Number(e.target.value)}})}/></label><label>偏移<input type="number" value={d.offset} onChange={e=>setP({...p,unit_definition:{...d,offset:e.target.value===''?NaN:Number(e.target.value)}})}/></label></>}
 {p.kind==='alias'&&<label>指向的已有参数名称<input value={p.alias_target||''} onChange={e=>setP({...p,alias_target:e.target.value})}/></label>}
 {p.kind==='parameter'&&<><label>物理含义<input value={String(p.declaration?.quantity||'')} onChange={e=>setP({...p,declaration:{...p.declaration,type:p.declaration?.type||'number',quantity:e.target.value}})}/></label><label>单位<input value={String(p.declaration?.unit||'')} onChange={e=>setP({...p,declaration:{...p.declaration,type:p.declaration?.type||'number',unit:e.target.value}})}/></label></>}
 <div className="profile-controls"><button onClick={()=>void onSave(p)}>保存补充，暂不启用</button><button disabled={r.missing.length>0||JSON.stringify(p)!==JSON.stringify(r.proposal)} onClick={()=>void onDecision(true)}>批准当前定义并派生方案</button><button onClick={()=>void onDecision(false)}>拒绝申请</button></div><p>先保存再批准。批准不代表物理模型已验证，不会自动注册模块或运行。</p></section>;
}
