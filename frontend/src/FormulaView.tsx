import {lazy,Suspense} from 'react';
const MathFormula=lazy(()=>import('./MathFormula'));
export type FormulaRecord={module_ref:string;formula_latex:string;source_task:string;step_id:string;source:string};
export default function FormulaView({record}:{record?:FormulaRecord}){
 if(!record?.formula_latex)return <p className="formula-note">此版本没有可追溯的已保存数学公式；请查看实际代码。</p>;
 return <section className="module-formula" aria-label="已保存的计算公式"><strong>计算公式</strong><Suspense fallback={<pre>{record.formula_latex}</pre>}><MathFormula source={record.formula_latex}/></Suspense><small>来自{record.source==='workflow_plan'?'本工作流确认方案':'注册时的确认方案'} · {record.source_task.slice(0,6)}。公式为模型声明，请结合代码与验证记录核对。</small><details><summary>查看 LaTeX 原文</summary><pre>{record.formula_latex}</pre></details></section>;
}
