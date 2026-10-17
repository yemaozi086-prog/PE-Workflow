import type {Port} from './types';
export function numberInput(text:string):number|string {if(!text.trim())return '';if(!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(text.trim()))return text;const n=Number(text);return Number.isFinite(n)?n:text;}
export function normaliseValues(values:Record<string,unknown>,ports:Record<string,Port>):Record<string,unknown>{
 const array=(v:unknown):unknown=>Array.isArray(v)?v.map(array):typeof v==='string'?numberInput(v):v;
 const field=(v:unknown,p:Port):unknown=>p.type==='number'&&typeof v==='string'?numberInput(v):p.type==='flat_dict'&&v&&typeof v==='object'?normaliseValues(v as Record<string,unknown>,p.fields||{}):p.type==='array'?array(v):p.type==='collection'&&Array.isArray(v)?v.map(x=>field(x,p.item||{type:'number'})):v;
 const result={...values};
 for(const [key,p] of Object.entries(ports)){
  if(!(key in values))continue;
  const v=values[key];
  if(p.required===false&&(v===undefined||typeof v==='string'&&!v.trim())){delete result[key];continue;}
  result[key]=field(v,p);
 }
 return result;
}
