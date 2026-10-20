"""Opt-in, bounded real DSH planning acceptance; never touches the main database.

Run explicitly with --run-real. Two fresh tasks, one planning call each, no repairs,
no registration/publication. Retains reports and the isolated SQLite database.
"""
import argparse,asyncio,json,os,sys,time,threading
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-real',action='store_true');parser.add_argument('--output',type=Path);args=parser.parse_args()
    if not args.run_real:parser.error('Explicit --run-real is required: this consumes at most two real model calls')
    sys.stdout.reconfigure(encoding='utf-8');out=args.output or ROOT/'data/protocol-audit-20261020'/('real-scan-'+datetime.now().strftime('%Y%m%d-%H%M%S'));out.mkdir(parents=True,exist_ok=True)
    print('Isolated acceptance artifacts: '+str(out),flush=True)
    os.environ['PE_WORKFLOW_DB']=str(out/'bootstrap.sqlite');os.environ['PE_DSH_ENABLED']='1'
    sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
    from test_designs import fixture
    from pe_workflow.models import Module,Port,Workflow
    from pe_workflow.engine import execute
    from pe_workflow.worker_routes import route_file
    from pe_workflow.storage import canonical
    c,s,store=fixture.__wrapped__(out)
    if store.modules() and any(m['id']=='ohmic_loss' for m in store.modules()):raise RuntimeError('Use a fresh isolated run directory; no hidden reuse')
    def bag(field,quantity,unit):return Port(type='flat_dict',fields={field:Port(type='number',quantity=quantity,unit=unit)})
    module=Module(id='ohmic_loss',version=1,name='固定电阻焦耳损耗',contract_version='3.0',
        inputs={'electrical':bag('i_rms','electric current','A'),'device':bag('R','electrical resistance','ohm')},
        outputs={'loss':bag('p_w','power','W')},expressions={'loss':"{'p_w': electrical['i_rms'] ** 2 * device['R']}"},provenance='Independent acceptance fixture: P=I_rms^2 R; fixed resistance, no temperature dependence')
    store.add_module(module)
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
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    os.environ['PE_WORKFLOW_PORT']=str(server.server_port)
    instruction='''建立固定电阻焦耳损耗的有限电流扫描工作流。已知公式 P=I_rms²R，固定电阻、不考虑温度变化。
只复用已注册 ohmic_loss@1，禁止新工具和额外计算步骤。运行时外部输入 scan.currents 为一维 RMS 电流数组[A]，device.R 为固定电阻[ohm]。
使用原生网格产生一列 i_rms，再用一层有限 batch 子图逐行调用该工具。扫描行的 i_rms 接到模块 electrical.i_rms；device 必须作为显式 capture 传入循环。
保留全部逐点 loss.p_w 结果和扫描原顺序，不筛选、不优化、不新增汇总节点。此阶段只规划，数值留到运行时，不添加默认值。
请按当前平台给出的正式控制契约和精确工具接口规划；这些说明是完整的计算要求。'''
    report={'production_writes':False,'maximum_model_calls':2,'cases':[]};routes=[]
    try:
        for index in (1,2):
            t=c.post('/api/workflow-designs',json={'instruction':instruction,'allow_external_suggestions':False}).json()
            t=s.get(t['id']);t['call_limit']=1;t['configuration']['max_calls']=1;t=s.save(t,'isolated_one_call_budget')
            start=time.time();asyncio.run(s.dispatch(t));t=s.get(t['id']);routes.append(route_file(ROOT,t['id'],t.get('call_id','')))
            print(canonical({'case':index,'status':t['status'],'calls':t['calls']}),flush=True)
            while t['status'] in ('dispatching','awaiting_worker','delivery_unknown') and time.time()-start<600:
                time.sleep(5);s.progress.refresh(t['id']);t=s.get(t['id'])
            case={'case':index,'task_id':t['id'],'status':t['status'],'calls':t['calls'],'limit':t['call_limit'],'elapsed_seconds':round(time.time()-start),'last_report':t.get('last_report'),'route':t.get('failure_routing'),'numerical_check':False}
            if t['status']=='awaiting_confirmation':
                view=c.get('/api/workflow-designs/'+t['id']+'/binding-preview').json();case['binding_valid']=view['valid']
                if view['valid']:
                    registry={f'{m["id"]}@{m["version"]}':Module.model_validate(m) for m in store.modules()}
                    output=execute(Workflow.model_validate(view['workflow']),registry,{'scan':{'currents':[0,1,2,3]},'device':{'R':0.5}})
                    values=[v['p_w'] for v in output.values() if isinstance(v,dict) and 'p_w' in v]
                    case.update(outputs=output,numerical_check=values==[[0,0.5,2,4.5]])
            elif t['status'] in ('dispatching','awaiting_worker','delivery_unknown'):
                c.post('/api/workflow-designs/'+t['id']+'/stop',json={'revision':t['revision']});case['stopped_receiving_after_timeout']=True
            report['cases'].append(case);(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(canonical(case),flush=True)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        for p in routes:p.unlink(missing_ok=True)

if __name__=='__main__':main()
