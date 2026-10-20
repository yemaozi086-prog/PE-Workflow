export type JourneyTask={candidate_processing?:{status:string};last_report?:{code?:string};status:string;phase:string;queue_id?:string;plan?:{steps:{module_ref:string|null}[]};superseded_by?:string;archived?:boolean;questions?:Record<string,string>;plan_preflight?:{valid:boolean};continuation_action?:{allowed:boolean};completion_missing_info?:boolean;failure_routing?:{route:string}};
export type JourneyQueue={active:number;pending:number;ready:number;blocked:number;registered:number;total:number};
export function designJourney(t:JourneyTask|null,q?:JourneyQueue,canAssemble=false){
 const stage=!t||!t.plan?0:t.status==='saved'?3:t.phase==='bind'||t.status==='ready_to_save'?2:1;
 const result=(title:string,note:string,label:string,target:string,tone='neutral')=>({stage,title,note,label,target,tone});
 if(!t)return result('先描述你想完成的计算','不用先填运行参数。系统先给出计算步骤，再匹配工具。','填写需求','design-request');
 if(t.archived)return result('这个任务已归档','记录与调用次数均已保留，恢复后不会自动执行。','查看恢复操作','design-archived');
 if(t.superseded_by)return result('这是旧任务，请切换到当前修订','后续操作应在新修订中继续，旧记录保留供查看。','打开当前修订入口','design-successor');
 if(t.status==='saved')return result('工作流已保存','下一步打开画布，填写实际参数后运行。','打开画布入口','design-saved','success');
 if(t.status==='ready_to_save')return result('接线检查通过，等待保存','保存只建立新工作流，不会自动运行计算。','检查接线并保存','design-save','success');
 if(t.candidate_processing?.status==='validating')return result('候选已收到，平台正在校验','当前不是等待 DSH 生成；请勿重复提交。','查看校验进度','design-activity');
 if(t.last_report?.code==='CANDIDATE_PROCESSING_INTERRUPTED')return result('候选已收到，但平台校验中断','复检已保存的原文，不调用模型，也不增加额度。','复检原候选','design-recovery','warning');
 if(['dispatch_ready','dispatching','awaiting_worker','delivery_unknown'].includes(t.status))return result(t.status==='delivery_unknown'?'正在核对调用是否送达':'智能体正在处理','目前无需重复提交。这里可查看真实活动、耗时或停止接收结果。','查看执行进度','design-activity');
 if(t.failure_routing?.route==='platform_fault')return result('平台故障，当前任务已停止','请查看故障说明；追加模型次数不能解决平台故障。','查看故障说明','design-diagnostics','warning');
 if(t.plan_preflight?.valid===false)return result('计算方案还有接口问题','先处理字段来源、接口或保护条件，再生成工具。','处理方案问题','design-interface','warning');
 if(canAssemble)return result('工具已齐全，可以检查接线','核对程序生成的接线预览后继续，不需要再次调用智能体建图。','核对接线预览','design-wiring');
 if(Object.keys(t.questions||{}).length)return result('需要你补充信息','只回答列出的问题，不需要重填整套需求。补充不会增加额度。','填写待补充信息','design-questions','warning');
 if(t.status==='waiting_extension')return result('需要核对自定义声明','核对新增定义与来源，再明确批准；不会自动改用别的单位。','核对声明','design-extension','warning');
 if(t.phase==='formula'){
  if(t.status==='formula_candidate')return result('工具候选已检查，等待你核对','检查代码及验证范围后再明确注册。','核对并注册候选','design-candidate');
  if(t.status==='formula_registered')return result('工具已注册，请回到关联方案','在关联工具清单或历史任务中选择最新派生方案继续。','返回关联方案',t.queue_id?'design-back-to-tools':'design-history');
  return result('这个工具已暂停','先查看停止原因与可用操作，不要重复创建任务。','查看原因与恢复入口','design-recovery','warning');
 }
 if(q?.active)return result(`正在生成 ${q.active} 个工具`,`排队 ${q.pending} 个 · 待核对 ${q.ready} 个 · 已暂停 ${q.blocked} 个。无需重复启动。`,'查看工具进度','design-tools');
 if(q?.ready)return result(`${q.ready} 个候选等待你核对`,`已暂停 ${q.blocked} 个。可先核对通过的候选，再注册选中项。`,'核对工具候选','design-tools');
 if(q?.blocked)return result(`${q.blocked} 个工具需要处理`,'工具清单会标出暂停项。进入对应步骤查看原因、补充或批准修正。','处理暂停的工具','design-tools','warning');
 if(q?.pending)return result(`${q.pending} 个工具等待启动`,'在工具区域查看队列状态；继续排队不会追加调用额度。','查看工具队列','design-tools');
 if(q?.registered)return result('本批工具已注册','打开关联的派生方案，继续检查接线。','打开关联方案入口','design-tools');
 if(['paused','blocked'].includes(t.status))return result('任务已暂停，等待处理','已有方案和额度记录均保留。先核对原因，再决定如何继续。','查看原因与恢复入口','design-recovery','warning');
 if(t.status==='missing_tools')return result('计算方案已保存，下一步补齐工具','选择缺失步骤生成候选，核对后注册；不用填写整套运行参数。','准备缺失工具','design-tools');
 return result('先核对计算方案','核对步骤和适用条件，然后在方案下方确认。','核对方案并继续','design-plan-actions');
}

export function revealDesignSection(id:string){
 const element=document.getElementById(id);if(!element)return;
 let current:HTMLElement|null=element;
 while(current){if(current instanceof HTMLDetailsElement)current.open=true;current=current.parentElement;}
 element.setAttribute('tabindex','-1');
 requestAnimationFrame(()=>{element.scrollIntoView({behavior:'smooth',block:'start'});element.focus({preventScroll:true});});
}
