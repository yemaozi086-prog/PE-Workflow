import type {Run,Port} from './types';
export default function RunSummary({run,current}:{run:Run;current:boolean}){
 const metrics:{path:string;label:string;value:number;unit:string}[]=[];
 function collect(values:Record<string,unknown>,ports:Record<string,Port>,prefix=''){
  for(const [key,value] of Object.entries(values)){const p=ports[key],path=prefix+key;
   if(typeof value==='number')metrics.push({path,label:p?.chinese_name||p?.description||key,value,unit:p?.unit||''});
   else if(p?.type==='flat_dict'&&value&&typeof value==='object'&&!Array.isArray(value))collect(value as Record<string,unknown>,p.fields||{},path+'.');
  }
 }
 collect(run.outputs||{},run.output_ports||{});
 function hasMissing(p:Port):boolean{return !!p.missing_when||Object.values(p.fields||{}).some(hasMissing);}
 const missing=Object.values(run.output_ports||{}).some(hasMissing);
 return <section className="run-summary"><strong>{run.status==='succeeded'?'计算执行完成':'计算未完成'}</strong><p>{current?'本次输入对应的结果':'历史快照结果；不代表当前编辑内容'}。执行成功不等于满足设计要求；可行性须依据工作流明确输出的判据核对。</p><div className="summary-metrics">{metrics.map(m=><article key={m.path}><span>{m.label}</span><strong>{Number(m.value.toPrecision(8))} <small>{m.unit}</small></strong><code>{m.path}</code></article>)}</div>{!metrics.length&&<p>结果包含数组或结构化数据，请展开下方明细查看。</p>}{missing&&<p>此结果允许显式缺失推荐点：JSON null 表示 NaN（无有效推荐），不是零值；请核对对应的 0/1 有效标记。</p>}</section>;
}
