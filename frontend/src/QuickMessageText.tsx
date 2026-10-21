import katex from 'katex';
import 'katex/dist/katex.min.css';
function math(source:string,displayMode=false){try{return <span className={displayMode?'quick-display-math':'quick-inline-math'} dangerouslySetInnerHTML={{__html:katex.renderToString(source,{displayMode,throwOnError:true,trust:false,strict:'error',maxExpand:500})}}/>;}catch{return <code>{source}</code>;}}
function inline(text:string){return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\$[^$\n]+\$)/g).map((s,i)=><span key={i}>{s.startsWith('`')?<code>{s.slice(1,-1)}</code>:s.startsWith('**')?<strong>{s.slice(2,-2)}</strong>:s.startsWith('$')?math(s.slice(1,-1)):s}</span>);}
export default function QuickMessageText({text}:{text:string}){
 return <div className="quick-message-content">{text.split(/(```[\s\S]*?```|\$\$[\s\S]*?\$\$)/g).map((block,i)=>block.startsWith('```')?<pre key={i}><code>{block.slice(3,-3).replace(/^[A-Za-z0-9_-]*\n/,'')}</code></pre>:block.startsWith('$$')?<div key={i}>{math(block.slice(2,-2),true)}</div>:<div key={i}>{block.split(/\n\s*\n/).filter(Boolean).map((paragraph,j)=>/^#{1,4}\s/.test(paragraph)?<h4 key={j}>{inline(paragraph.replace(/^#{1,4}\s+/,''))}</h4>:paragraph.split('\n').every(l=>/^\s*[-*]\s/.test(l))?<ul key={j}>{paragraph.split('\n').map((l,k)=><li key={k}>{inline(l.replace(/^\s*[-*]\s+/,''))}</li>)}</ul>:<p key={j}>{inline(paragraph)}</p>)}</div>)}</div>;
}
