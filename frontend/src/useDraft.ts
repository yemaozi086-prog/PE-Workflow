import {useCallback,useEffect,useRef,useState} from 'react';
import type {Workflow} from './types';
type Payload={workflow:Workflow;inputs:Record<string,unknown>;provenance:Record<string,string>};
type Entry={revision:number;base:number;saved:string;pending:Payload;inflight?:Promise<number>};
function localStore(mode:IDBTransactionMode,key:string,value?:unknown):Promise<unknown>{return new Promise((resolve,reject)=>{
 const open=indexedDB.open('flux-draft-recovery',1);open.onupgradeneeded=()=>open.result.createObjectStore('drafts');open.onerror=()=>reject(open.error);
 open.onsuccess=()=>{const db=open.result;const tx=db.transaction('drafts',mode);const store=tx.objectStore('drafts');const request=mode==='readonly'?store.get(key):store.put(value,key);request.onerror=()=>reject(request.error);tx.oncomplete=()=>{resolve(request.result);db.close();};tx.onerror=()=>reject(tx.error);};
});}
export async function recoveryFor(key:string){return await localStore('readonly',key) as {payload:Payload;draft_revision:number;base_revision:number;updated_at:string;saved:boolean}|undefined;}
export default function useDraft(workflow:Workflow|null,inputs:Record<string,unknown>,baseRevision:number,provenance:Record<string,string>){
 const entries=useRef(new Map<string,Entry>()),latest=useRef(workflow?.id||'');latest.current=workflow?.id||'';
 const [state,setState]=useState('已保存'),[error,setError]=useState(''),[recoveryError,setRecoveryError]=useState('');
 const hydrate=useCallback((payload:Payload,revision:number,base:number)=>{entries.current.set(payload.workflow.id,{revision,base,saved:JSON.stringify(payload),pending:payload});setState('已保存');setError('');},[]);
 const flushId=useCallback(async(key:string):Promise<number>=>{
   const entry=entries.current.get(key);if(!entry)return 0;
   if(entry.inflight){await entry.inflight;return flushId(key);}
   const payload=entry.pending,signature=JSON.stringify(payload);if(signature===entry.saved)return entry.revision;
   if(latest.current===key){setState('保存中');setError('');}
   const promise=(async()=>{try{
     const r=await fetch('/api/workspace/workflows/'+key+'/draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,expected_draft_revision:entry.revision,base_revision:entry.base})});
     const data=await r.json();if(!r.ok)throw Error(data.diagnostics?.[0]?.message||'草稿保存失败');
     if(entries.current.get(key)===entry){entry.revision=data.draft_revision;entry.saved=signature;if(latest.current===key)setState(JSON.stringify(entry.pending)===signature?'已保存':'等待保存');void localStore('readwrite',key,{payload:entry.pending,draft_revision:entry.revision,base_revision:entry.base,updated_at:new Date().toISOString(),saved:JSON.stringify(entry.pending)===entry.saved}).catch(()=>setRecoveryError('本机恢复副本保存失败'));}
     return data.draft_revision as number;
   }catch(e){if(latest.current===key&&entries.current.get(key)===entry){setState('保存失败');setError(e instanceof Error?e.message:String(e));}throw e;}})();
   entry.inflight=promise;
   try{return await promise;}finally{entry.inflight=undefined;}
 },[]);
 const payload=workflow?{workflow,inputs,provenance}:null;const signature=JSON.stringify(payload);
 // Capture pending data during render so a click immediately after typing flushes the latest value.
 if(payload){const old=entries.current.get(payload.workflow.id);if(old)old.pending=payload;else entries.current.set(payload.workflow.id,{revision:0,base:baseRevision,saved:'',pending:payload});}
 useEffect(()=>{
   if(!payload)return;const key=payload.workflow.id;const entry=entries.current.get(key)!;
   try{localStorage.setItem('flux-last-workflow',key);}catch{/* backend remains authoritative */}
   void localStore('readwrite',key,{payload,draft_revision:entry.revision,base_revision:entry.base,updated_at:new Date().toISOString(),saved:JSON.stringify(payload)===entry.saved}).catch(()=>setRecoveryError('本机临时恢复副本写入失败；请确认服务端草稿已保存后再关闭'));
   if(entry.saved===JSON.stringify(payload))return;
   setState('等待保存');const timer=setTimeout(()=>void flushId(key).catch(()=>{}),800);
   return()=>clearTimeout(timer);
 },[signature,baseRevision,flushId]);
 useEffect(()=>{const warn=(event:BeforeUnloadEvent)=>{const entry=entries.current.get(latest.current);if(entry&&JSON.stringify(entry.pending)!==entry.saved){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[]);
 const flush=useCallback((nextInputs?:Record<string,unknown>)=>{const entry=entries.current.get(latest.current);if(entry&&nextInputs)entry.pending={...entry.pending,inputs:nextInputs};return flushId(latest.current);},[flushId]);
 return {state,error,recoveryError,flush,hydrate};
}
