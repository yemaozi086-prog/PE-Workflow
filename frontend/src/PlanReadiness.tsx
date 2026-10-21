import type {ToolQueueSummary} from './ToolCompletion';
type Issue={code:string;message:string;path:string};
type Props={status:string;phase:string;steps:{module_ref:string|null}[];check?:{valid:boolean;issues:Issue[];warnings?:Issue[]};questions?:Record<string,string>;route?:string;queue?:ToolQueueSummary};

export default function PlanReadiness({status,phase,steps,check,questions,route,queue}:Props){
 const missing=steps.filter(s=>!s.module_ref).length;
 const active=['dispatch_ready','dispatching','awaiting_worker'].includes(status);
 const needsUser=Object.keys(questions||{}).length>0||['needs_information','needs_confirmation','uncertain'].includes(route||'');
 const fault=route==='platform_fault';
 const errors=check?.issues||[];
 const complete=['ready_to_save','saved'].includes(status);
 const queueTitle=queue?.active?`正在生成 ${queue.active} 个工具`:queue?.ready?`${queue.ready} 个候选待核对注册`:queue?.blocked?`${queue.blocked} 个工具需处理，原因见下方清单`:queue?.pending?`${queue.pending} 个工具等待生成`:queue?.registered?'本批工具已注册，请打开关联方案':null;
 const queueNote=queue?.active?'本批工具正在按已批准额度处理，无需重复提交。各步骤的进度与诊断见下方清单。':queue?.ready?'请在下方核对候选代码与验证范围，勾选后明确注册。候选检查通过不代表独立数值验证通过。':queue?.blocked?'暂停项保留了诊断和剩余额度。请在下方清单查看具体原因，不必重填整套参数。':queue?.pending?'选中的工具已进入队列，请查看下方队列状态；不需要重新发起生成。':queue?.registered?'请使用下方“打开关联工具后的派生方案”继续接线；当前记录保留用于追溯。':null;
 const title=active?'正在处理，请等待执行结果':status==='saved'?'工作流已保存，可填写运行数据':status==='ready_to_save'?'方案与工具已准备好，接线检查通过':fault?'平台检查发生故障，已停止':errors.length?`方案检查发现 ${errors.length} 项问题`:needsUser?'需要你补充或确认信息':queueTitle||(!steps.length?'等待可检查的计算方案':missing?`还需补齐 ${missing} 个工具`:'工具已匹配，准备检查接线');
 return <section className="plan-readiness" aria-label="方案与工具准备情况">
  <h3>{title}</h3>
  {!!check?.warnings?.length&&<aside><strong>可处理提醒（不等于方案失败）</strong><ul>{check.warnings.map((w,i)=><li key={i}>{w.message}</li>)}</ul></aside>}
  <ol className="readiness-stages">
   <li><strong>计算链与接口</strong><span>{check?check.valid?'检查通过':'尚未通过':'等待检查'}</span></li>
   <li><strong>计算工具</strong><span>{steps.length?`${steps.length-missing}/${steps.length} 已匹配，仍需核对适用性`:'方案确定后匹配'}</span>{queue&&<span>本批：{queue.active} 生成中 · {queue.ready} 待核对 · {queue.blocked} 需处理 · {queue.registered} 已注册</span>}</li>
   <li><strong>接线与保存</strong><span>{['ready_to_save','saved'].includes(status)?'结构检查通过':'前两项完成后继续'}</span></li>
  </ol>
  <p>{active?'当前无需重复提交。实际调用进度可在下方展开查看。':complete?'结构检查已完成。保存不会自动运行；实际参数、数值结果和模型适用性仍需分别核对。':fault?'保留现场，由开发人员检查；追加模型次数不能修复平台故障。':errors.length?'先集中处理下列接口问题，再生成缺失工具。无需手改 JSON；修正不得删除公式或保护条件。':needsUser?'请在下方补充区集中回答。补充信息不会重置调用额度。':queueNote?queueNote:!steps.length?'尚未取得可核对的方案；请查看下方执行进度或具体诊断。':missing?'下方清单可批量生成候选；核对后明确注册，检查通过不代表数值已验证。':'核对计算步骤与适用条件后，使用下方操作继续；保存不会自动运行。'}</p>
  {!!errors.length&&<details><summary>查看全部已发现问题（{errors.length} 项）</summary><ul>{errors.map((e,i)=><li key={e.path+e.code+i}><strong>{e.message}</strong><small>{e.path} · {e.code}</small></li>)}</ul></details>}
  <small>当前阶段：{phase==='formula'?'生成工具':phase==='bind'?'检查接线':'计算方案'}。结构、数值与工程适用性分别验证。</small>
 </section>;
}
