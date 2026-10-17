import {useState,useEffect} from 'react';
import type {Port} from './types';

export function numberInput(text:string):number|string {if(!text.trim())return '';if(!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(text.trim()))return text;const n=Number(text);return Number.isFinite(n)?n:text;}
export function normaliseValues(values:Record<string,unknown>,ports:Record<string,Port>):Record<string,unknown>{
 const field=(v:unknown,p:Port):unknown=>p.type==='number'&&typeof v==='string'?numberInput(v):p.type==='flat_dict'&&v&&typeof v==='object'?normaliseValues(v as Record<string,unknown>,p.fields||{}):p.type==='array'?array(v):p.type==='collection'&&Array.isArray(v)?v.map(x=>field(x,p.item||{type:'number'})):v;
 const array=(v:unknown):unknown=>Array.isArray(v)?v.map(array):typeof v==='string'?numberInput(v):v;
 return {...values,...Object.fromEntries(Object.entries(ports).filter(([k])=>k in values).map(([k,p])=>[k,field(values[k],p)]))};
}
export function parseTable(text:string):string[][] {
  const delimiter=text.includes('\t')?'\t':',';const rows:string[][]=[];let row:string[]=[],cell='',quoted=false;
  const source=text.replace(/^\uFEFF/,'');
  for(let i=0;i<source.length;i++){
    const ch=source[i];
    if(ch==='"'){if(quoted&&source[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}
    else if(!quoted&&(ch===delimiter||ch==='\n')){row.push(cell.trim());cell='';if(ch==='\n'){rows.push(row);row=[];}}
    else if(ch!=='\r'||quoted)cell+=ch;
  }
  if(quoted)throw Error('引号未闭合，请检查 CSV');
  if(cell.length||row.length){row.push(cell.trim());rows.push(row);}
  if(!rows.length)throw Error('没有可导入的数据');
  return rows;
}
const at=(value:unknown,path:number[]):unknown=>path.reduce<unknown>((v,k)=>Array.isArray(v)?v[k]:undefined,value);
function put(value:unknown,path:number[],next:unknown):unknown {if(!path.length)return next;const copy=Array.isArray(value)?[...value]:[];copy[path[0]]=put(copy[path[0]],path.slice(1),next);return copy;}
function dimensions(value:unknown):number[]{return Array.isArray(value)?[value.length,...dimensions(value[0])]:[];}
function build(shape:number[],old:unknown):unknown {return shape.length?Array.from({length:shape[0]},(_,i)=>build(shape.slice(1),Array.isArray(old)?old[i]:undefined)):old??'';}

function ArrayEditor({port,value,onChange,label}:{port:Port;value:unknown;onChange:(v:unknown)=>void;label:string}){
  const rank=port.value_type?.shape.length||1;const actual=dimensions(value);
  const [sizes,setSizes]=useState(()=>Array.from({length:rank},(_,i)=>String(actual[i]|| (typeof port.value_type?.shape[i]==='number'?port.value_type.shape[i]:1))));
  useEffect(()=>{if(actual.length)setSizes(actual.map(String));},[actual.join(',')]);
  const [prefix,setPrefix]=useState<number[]>(Array(Math.max(0,rank-2)).fill(0));
  const [page,setPage]=useState(0),[columnPage,setColumnPage]=useState(0),[text,setText]=useState(''),[header,setHeader]=useState(false),[column,setColumn]=useState(0),[confirmed,setConfirmed]=useState(false),[error,setError]=useState('');
  const slice=rank>2?at(value,prefix):value;
  const rows=Array.isArray(slice)?slice:[];const width=rank===1?1:(Array.isArray(rows[0])?rows[0].length:0);
  function resize(){try{const shape=sizes.map(Number);if(shape.some((n,i)=>!Number.isInteger(n)||n<1||(typeof port.value_type?.shape[i]==='number'&&n!==port.value_type.shape[i]))||shape.reduce((a,b)=>a*b,1)>100000)throw Error('尺寸必须为正整数并符合声明；编辑表格最多 100000 个元素');onChange(build(shape,value));setPrefix(prefix.map(()=>0));setPage(0);setColumnPage(0);setError('');}catch(e){setError(String(e));}}
  let preview:string[][]=[];try{if(text.trim())preview=parseTable(text);}catch{/* shown when importing */}
  function importRows(){try{
    if(!confirmed)throw Error('请先确认数据单位与声明一致');
    let data=parseTable(text);if(header)data=data.slice(1);if(!data.length)throw Error('表格没有数据行');
    if(data.some(r=>r.length!==data[0].length))throw Error('每行列数必须一致，缺失单元格不能省略');
    const selected=rank===1?data.map(r=>r[column]):data.flat();
    if(selected.some(v=>v===undefined||!v.trim()||!Number.isFinite(Number(v))))throw Error('存在空白或非数值单元格，请核对数据列');
    const next=rank===1?data.map(r=>Number(r[column])):data.map(r=>r.map(Number));
    const shape=dimensions(next);const declared=port.value_type?.shape.slice(rank>2?rank-2:0)||[];
    if(shape.some((n,i)=>typeof declared[i]==='number'&&declared[i]!==n))throw Error('导入尺寸不符合固定形状声明');
    if(data.length*data[0].length>100000)throw Error('一次表格导入最多 100000 个元素');
    if(rank>2&&shape.some((n,i)=>n!==actual[rank-2+i]))throw Error('切片尺寸必须与现有数组一致，请先调整完整尺寸');
    onChange(rank>2?put(value,prefix,next):next);setSizes((rank>2?actual:shape).map(String));setPage(0);setError('');
  }catch(e){setError(e instanceof Error?e.message:String(e));}}
  return <div className="array-editor"><small>声明：{port.value_type?.dtype} [{port.value_type?.shape.join(' × ')}]；轴：{port.axes?.map(a=>a.name+(a.coordinate_ref?' → '+a.coordinate_ref:'')).join('、')}；当前尺寸：{actual.join(' × ')||'未填写'}</small>
    <div className="form-inline">{sizes.map((s,i)=><label key={i}>维度 {i+1}<input aria-label={`${label} 维度 ${i+1}`} value={s} inputMode="numeric" onChange={e=>setSizes(sizes.map((v,j)=>i===j?e.target.value:v))}/></label>)}<button type="button" onClick={resize}>按尺寸调整</button></div><small>保留重叠数据，缩小尺寸会截去超出部分；新增单元格留空，不填 0。</small>
    {rank>2&&<div className="form-inline">{prefix.map((v,i)=><label key={i}>切片轴 {i+1}<select aria-label={`${label} 切片轴 ${i+1}`} value={v} onChange={e=>setPrefix(prefix.map((n,j)=>j===i?Number(e.target.value):n))}>{Array.from({length:actual[i]||1},(_,n)=><option key={n} value={n}>{n}</option>)}</select></label>)}</div>}
    {!!rows.length&&<><div className="array-grid"><table><thead><tr><th>行</th>{Array.from({length:Math.min(12,Math.max(0,width-columnPage*12))},(_,j)=><th key={j}>{rank===1?'值':columnPage*12+j}</th>)}</tr></thead><tbody>{rows.slice(page*20,page*20+20).map((_,i)=><tr key={i}><th>{page*20+i}</th>{Array.from({length:Math.min(12,Math.max(0,width-columnPage*12))},(_,j)=>{const path=[...prefix,page*20+i,...(rank===1?[]:[columnPage*12+j])];const v=at(value,path);return <td key={j}><input aria-label={`${label} 单元格 ${path.join(',')}`} inputMode="decimal" value={String(v??'')} onChange={e=>onChange(put(value,path,e.target.value))} onBlur={e=>onChange(put(value,path,numberInput(e.target.value)))}/></td>;})}</tr>)}</tbody></table></div><div className="form-inline"><button disabled={!page} onClick={()=>setPage(page-1)}>前 20 行</button><button disabled={(page+1)*20>=rows.length} onClick={()=>setPage(page+1)}>后 20 行</button><button disabled={!columnPage} onClick={()=>setColumnPage(columnPage-1)}>前 12 列</button><button disabled={(columnPage+1)*12>=width} onClick={()=>setColumnPage(columnPage+1)}>后 12 列</button></div></>}
    <details><summary>粘贴表格 / CSV 导入{rank>2?'当前切片':''}</summary><input aria-label={`${label} CSV 文件`} type="file" accept=".csv,.tsv,.txt" onChange={async e=>{const f=e.target.files?.[0];if(f){if(f.size>8000000){setError('文件过大，最多 8 MB');return;}setText(await f.text());setConfirmed(false);}e.target.value='';}}/><textarea aria-label={`${label} 粘贴表格`} value={text} onChange={e=>{setText(e.target.value);setConfirmed(false);}} placeholder="粘贴逗号或制表符分隔的数值"/><label><input type="checkbox" checked={header} onChange={e=>setHeader(e.target.checked)}/>第一行是列名</label>{rank===1&&<label>读取数据列<select aria-label={`${label} 导入列`} value={column} onChange={e=>setColumn(Number(e.target.value))}>{Array.from({length:preview[0]?.length||1},(_,i)=><option key={i} value={i}>{i+1}{header?' · '+preview[0]?.[i]:''}</option>)}</select></label>}<small>预览：{preview.length} 行 × {preview[0]?.length||0} 列。{preview.slice(0,3).map(r=>r.join(' | ')).join('；')}</small><label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>确认数据单位为 {port.unit||'声明中的无量纲'}；不转置、不重采样</label><button type="button" onClick={importRows}>核对后导入</button></details>{error&&<p className="form-error" role="alert">{error}</p>}
  </div>;
}

export function ValueEditor({port,value,onChange,label}:{port:Port;value:unknown;onChange:(v:unknown)=>void;label:string}){
  if(port.type==='flat_dict'){const bag=value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};return <ValueForm ports={port.fields||{}} values={bag} onChange={onChange} prefix={label}/>;}
  if(port.type==='array')return <ArrayEditor port={port} value={value} onChange={onChange} label={label}/>;
  if(port.type==='boolean')return <select aria-label={label} value={value===true?'true':value===false?'false':''} onChange={e=>onChange(e.target.value===''?'':e.target.value==='true')}><option value="">未填写</option><option value="true">是</option><option value="false">否</option></select>;
  if(port.type==='collection'){const list=Array.isArray(value)?value:[];return <div>{list.map((v,i)=><details key={i}><summary>第 {i+1} 项 <button onClick={()=>onChange(list.filter((_,j)=>j!==i))}>移除</button></summary><ValueEditor port={port.item||{type:'number'}} value={v} onChange={next=>onChange(list.map((old,j)=>j===i?next:old))} label={`${label}.${i}`}/></details>)}<button onClick={()=>onChange([...list,port.item?.type==='flat_dict'?{}:''])}>添加一项</button></div>;}
  const parsed=typeof value==='string'?numberInput(value):value;
  const invalid=port.type==='number'&&value!==undefined&&(typeof parsed!=='number'||!Number.isFinite(parsed));
  return <><input aria-label={label} inputMode={port.type==='number'?'decimal':undefined} value={typeof value==='object'?'':String(value??'')} onChange={e=>onChange(e.target.value)} onBlur={e=>{if(port.type==='number')onChange(numberInput(e.target.value));}}/>{invalid&&<small className="form-error">请填写有限数值，空白不会按 0 计算</small>}</>;
}

export default function ValueForm({ports,values,onChange,prefix='',sources={},onSourceChange}:{ports:Record<string,Port>;values:Record<string,unknown>;onChange:(v:Record<string,unknown>)=>void;prefix?:string;sources?:Record<string,string>;onSourceChange?:(path:string)=>void}){
  return <div className="value-form">{Object.entries(ports).map(([key,p])=>{const name=prefix?prefix+'.'+key:key;const change=(v:unknown)=>{onChange({...values,[key]:v});if(p.type!=='flat_dict')onSourceChange?.(name);};return <div className="value-field" key={key}><label><strong>{p.chinese_name||key}</strong>{p.chinese_name&&<code>{key}</code>}<span>{p.unit||p.type}{p.required===false?' · 可选':' · 必填'}</span></label>{p.description&&p.description!==key&&<details className="field-description"><summary>字段说明</summary><small>{p.description}</small></details>}<small className="value-source">来源：{values[key]===undefined||values[key]===''?'尚未填写':sources[name]||sources[key]||sources[name.split('.')[0]]||'当前流程输入（请核对）'}</small>{p.type==='flat_dict'?<details open><summary>{key} · {Object.keys(p.fields||{}).length} 个字段</summary><ValueForm ports={p.fields||{}} values={values[key]&&typeof values[key]==='object'?values[key] as Record<string,unknown>:{}} onChange={change} prefix={name} sources={sources} onSourceChange={onSourceChange}/></details>:<ValueEditor port={p} value={values[key]} onChange={change} label={name}/>}</div>;})}</div>;
}
