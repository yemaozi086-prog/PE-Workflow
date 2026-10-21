import {designJourney,revealDesignSection,type JourneyTask,type JourneyQueue} from './designJourney';
export default function DesignNextStep({task,queue,canAssemble,onNavigate}:{onNavigate?:(id:string)=>void;task:JourneyTask|null;queue?:JourneyQueue;canAssemble:boolean}){
 const next=designJourney(task,queue,canAssemble);
 return <section className={'design-next-step '+next.tone} aria-label="当前状态与下一步" aria-live="polite">
 <div><small>当前 · {['描述需求','确认方案与工具','自动搭建','保存完成'][next.stage]}</small><h2>{next.title}</h2><p>{next.note}</p></div>
 <button type="button" className="primary-button" onClick={()=>onNavigate?onNavigate(next.target):revealDesignSection(next.target)}>{'前往：'+next.label}<span aria-hidden="true"> ↓</span></button>
 </section>;
}
