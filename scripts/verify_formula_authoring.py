"""Opt-in real DSH check: one formula call, <=3 prechecks, no registration.

An engineer-authored plan enters via public revision/queue APIs. Dispatch, bridge,
candidate reception and validation are real. Never changes production task state.
"""
import argparse
import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-real',action='store_true')
    args=parser.parse_args()
    if not args.run_real:parser.error('--run-real consumes at most ONE real DSH call')
    sys.stdout.reconfigure(encoding='utf-8')
    out=ROOT/'data/formula-authoring-tests'/('real-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True,exist_ok=False)
    os.environ.update(PE_WORKFLOW_DB=str(out/'bootstrap.sqlite'),PE_DSH_ENABLED='1')
    sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
    from test_designs import fixture
    from pe_workflow.api import worker
    from pe_workflow.models import Port
    from pe_workflow.worker_routes import route_file
    c,s,store=fixture.__wrapped__(out)
    s.worker=worker
    def bag(fields):return Port(type='flat_dict',fields={k:Port(type='number',quantity=q,unit=u) for k,q,u in fields}).model_dump()
    inputs={'electrical':bag([('i_rms','electric current','A')]),'device':bag([('R','electrical resistance','ohm')])}
    outputs={'loss':bag([('p_w','power','W')])}
    plan={'title':'固定电阻损耗：紧凑实现验收','summary':'P=I_rms²R，仅固定电阻，不做热反馈。人工提供方案，测试公式工具生成。',
          'external_inputs':inputs,'steps':[{'id':'ohmic','title':'固定电阻焦耳损耗','purpose':'按 P=I_rms²R 计算损耗',
            'dependencies':[],'inputs':inputs,'outputs':outputs,'input_sources':{k:{'source':'input','port':k} for k in inputs},
            'input_checks':['electrical["i_rms"] >= 0','device["R"] >= 0'],
            'formula_latex':'P=I_{rms}^{2}R','evidence':['验收给定公式；不引入温度依赖'],'assumptions':['固定电阻'],'module_ref':None}]}
    t=c.post('/api/workflow-designs',json={'instruction':plan['summary'],'allow_external_suggestions':False}).json()
    t=c.post('/api/workflow-designs/'+t['id']+'/revise',json={'revision':t['revision'],'plan':plan}).json()
    examples=[{'inputs':{'electrical':{'i_rms':2},'device':{'R':0.5}},'outputs':{'loss':{'p_w':2}}},
              {'inputs':{'electrical':{'i_rms':3},'device':{'R':0.25}},'outputs':{'loss':{'p_w':2.25}}}]
    q=c.post('/api/workflow-designs/'+t['id']+'/tool-completion',json={'revision':t['revision'],'confirm_plan':True,'concurrency':1,
             'items':[{'step_id':'ohmic','max_calls':1,'examples':examples}]}).json()
    if 'id' not in q:raise RuntimeError('Queue preparation failed: '+str(q))
    s.completion.tick()
    key=q['items'][0]['task_id'];task=s.get(key)
    if task['status']!='dispatch_ready':raise RuntimeError('Preparation did not become dispatch_ready: '+task['status'])
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):self.forward('GET')
        def do_POST(self):self.forward('POST')
        def forward(self,method):
            try:
                data=self.rfile.read(int(self.headers.get('Content-Length','0'))) if method=='POST' else None
                response=c.request(method,self.path,content=data,headers={'Content-Type':'application/json','Authorization':self.headers.get('Authorization','')})
                self.send_response(response.status_code);self.end_headers();self.wfile.write(response.content)
            except Exception:
                self.send_response(500);self.end_headers();self.wfile.write(b'{"error":"isolated handler failure"}')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    os.environ['PE_WORKFLOW_PORT']=str(server.server_port)
    route=None
    try:
        start=time.time();asyncio.run(s.dispatch(task));task=s.get(key)
        route=route_file(ROOT,key,task.get('call_id',''))
        print(json.dumps({'isolated_directory':str(out),'status':task['status'],'maximum_model_calls':1,'calls':task['calls']}),flush=True)
        while task['status'] in ('dispatching','awaiting_worker','delivery_unknown') and time.time()-start<600:
            time.sleep(5);s.progress.refresh(key);task=s.get(key)
        if task['status'] in ('dispatching','awaiting_worker','delivery_unknown'):
            c.post('/api/workflow-designs/'+key+'/stop',json={'revision':task['revision']});task=s.get(key)
        rows=s.authoring.rows(task)
        report={'production_writes':False,'model_calls':task['calls'],'maximum_model_calls':1,
                'status':task['status'],'prechecks':[{k:r.get(k) for k in ('attempt','status','seconds','report')} for r in rows],
                'formula_validation':task.get('formula_validation'),'last_report':task.get('last_report'),
                'registered':False,'published':False,'scope':'One real formula generation; no claim of full LLC generation or engineering approval.'}
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False),flush=True)
        if task['status']!='formula_candidate' or not rows or rows[-1]['status']!='passed':raise RuntimeError('Bounded real acceptance did not pass; no extra calls requested')
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        if route:route.unlink(missing_ok=True)


if __name__=='__main__':main()
