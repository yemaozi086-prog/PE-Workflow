import katex from 'katex';
import 'katex/dist/katex.min.css';
export default function MathFormula({source}:{source:string}){
 const lines=source.includes('\\begin{')?source:source.replace(/;\s*\\quad\s*/g,' \\\\ ');
 const display=lines!==source?'\\begin{gathered}'+lines+'\\end{gathered}':source;
 try{return <div className="module-math" dangerouslySetInnerHTML={{__html:katex.renderToString(display,{displayMode:true,throwOnError:true,trust:false,strict:'error',maxExpand:500})}}/>;}
 catch{return <pre className="formula-fallback">{source}</pre>;}
}
