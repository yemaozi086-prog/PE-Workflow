export type RecoveryRow={action:string;remaining_calls:number;calls:number};
export function recoverySummary(items:RecoveryRow[]){
 const recheck=items.filter(i=>i.action==='revalidate_candidate');
 const model=items.filter(i=>['candidate_repair','preparation_retry'].includes(i.action));
 const declarations=items.filter(i=>i.action==='declarations');
 return {recheck:recheck.length,model:model.length,declarations:declarations.length,
  actionable:recheck.length+model.length,maximumCalls:model.reduce((n,i)=>n+Math.max(0,i.remaining_calls),0),
  label:model.length?`处理 ${recheck.length+model.length} 个工具（包含 AI 修正）`:`重新检查 ${recheck.length} 个工具（不调用 AI）`};
}

export function toolAction(item:{registered:boolean;status:string;status_label?:string}){
 if(item.registered)return '查看注册记录';
 if(item.status==='formula_candidate')return '核对代码与验证结果';
 if(item.status_label?.includes('未调用')||['blocked','needs_clarification'].includes(item.status))return '补充这一步的信息';
 if(item.status_label?.includes('可复检'))return '查看原候选与提醒';
 return ['paused','waiting_extension','delivery_unknown'].includes(item.status)?'查看原因与处理方式':'查看执行进度';
}
