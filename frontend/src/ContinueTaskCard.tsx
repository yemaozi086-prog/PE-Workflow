type Action={allowed:boolean;additional_limit:number;used:number;current_limit:number;new_limit:number;reason:string;scope:string[]};
export default function ContinueTaskCard({action,busy,onContinue,taskId,message}:{action?:Action;busy:boolean;onContinue:()=>void;taskId:string;message?:string}){
 return <section className="continue-task-card" aria-label="任务暂停与继续操作" role="status">
 <span className="continue-task-badge">本任务已暂停 · 等待你的操作</span><p>当前任务：{taskId.slice(0,6)}</p>
 <h2>{action?.allowed?'需要你批准，才能继续修正':'需要先处理当前问题'}</h2>
 <p>{action?.allowed?'已有方案已保留。这次继续处理检查发现的错误，不会从头建立新任务。':action?.reason||'请核对下方待补充内容和诊断，任务已保存。'}</p>
 {!action?.allowed&&message&&<p><strong>本次停止说明：</strong>{message.split('\n')[0]}</p>}
 {action&&<p className="continue-task-budget">已用 <strong>{action.used}</strong> 次 / 当前上限 {action.current_limit} 次 · 还剩 {Math.max(0,action.current_limit-action.used)} 次</p>}
 {action?.allowed&&<><p>{action.scope.length?`本轮只允许修改 ${action.scope.length} 处已定位的声明，公式和其他内容保持不变。`:'继续按当前阶段处理；具体修改仍须通过平台检查。'}</p>
 <button className="primary-button" disabled={busy} onClick={onContinue}>{busy?'正在提交，请稍候…':`追加最多 ${action.additional_limit} 次，让 DSH 继续修正`}</button>
 <p className="continue-task-note">点击即批准：调用总上限调整为 {action.new_limit} 次。成功或触发停止规则会提前结束，不一定用满 {action.additional_limit} 次。不会自动注册工具或运行计算。</p></>}
 </section>;
}
