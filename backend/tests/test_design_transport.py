"""Installed JS -> real subprocess bridge -> HTTP -> actual platform validator."""
import copy,json,subprocess,sys,threading,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from test_designs import fixture,create,callback,plan,tool
from pe_workflow.worker_routes import write_route,route_file
from pe_workflow.preset_runtime import tool_filename,render_prompt

ROOT=Path(__file__).resolve().parents[2]


def test_installed_submission_chain_without_model_or_registration(fixture,tmp_path):
    c,s,store=fixture
    t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'power W','evidence':'independent arithmetic','examples':[{'inputs':{'data':{'value':v}},'outputs':{'result':{'value':v*2}}} for v in (3,5)]}).json()
    t=s.get(t['id']);t.update(status='awaiting_worker',call_id=uuid.uuid4().hex,token='isolated-test-secret',calls=1);t=s.save(t,'test_dispatched')
    original_modules=store.modules();requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            r=c.get(self.path,headers={'Authorization':self.headers.get('Authorization','')})
            self.send_response(r.status_code);self.end_headers();self.wfile.write(r.content)
        def do_POST(self):
            requests.append(self.path)
            r=c.post(self.path,content=self.rfile.read(int(self.headers['Content-Length'])),headers={'Content-Type':'application/json'})
            self.send_response(r.status_code);self.end_headers();self.wfile.write(r.content)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    route=route_file(ROOT,t['id'],t['call_id']);second_route=None
    try:
        write_route(ROOT,t['id'],t['call_id'],t['token'],server.server_port)
        home=tmp_path/'home'
        subprocess.run([sys.executable,str(ROOT/'scripts/install_dsh_presets.py'),'--dsh-home',str(home),'--only','flux-design'],check=True,capture_output=True)
        code=(ROOT/'dsh/preset/flux-design/flux-design-tools.js').read_bytes()
        installed=home/'.agent-presets/flux-design-v3'
        assert (installed/'agent.cordis.yml').read_text(encoding='utf-8')==render_prompt((ROOT/'dsh/preset/flux-design/agent.cordis.yml').read_text(encoding='utf-8'),code)
        m=tool().model_dump();m['id']='transport_only_double'
        m.update(implementation='python_subset',expressions={},source="def compute(inputs):\n    value = inputs['data']['value']\n    return {'result': {'value': value * 2}}\n# 中文说明、引号、花括号 { } "+'测'*8000+'\n')
        assert len(json.dumps(m,ensure_ascii=True))>32768
        base={k:t[k] for k in ('call_id','token')};base.update(task_id=t['id'],kind='module')
        config={'tool':str(installed/tool_filename(code)),'payloads':['{"incomplete":',dict(base,module=json.dumps(m)),dict(base,module=m)]}
        file=tmp_path/'probe.json';file.write_text(json.dumps(config),encoding='utf-8')
        r=subprocess.run(['node',str(ROOT/'scripts/probe_design_transport.cjs'),str(file)],capture_output=True,text=True,encoding='utf-8',timeout=40,check=True)
        result=json.loads(r.stdout)
        assert result[0]['code']=='TOOL_ARGUMENT_OBJECT'
        assert result[1]['submitted'] is False
        assert result[2]['status']=='formula_candidate',result[2]
        assert result[2]['stop_required']
        assert len(requests)==1
        accepted=s.get(t['id'])
        assert accepted['candidate']['source']==m['source'] and accepted['calls']==1
        assert accepted['formula_request']['examples']==t['formula_request']['examples']
        assert store.modules()==original_modules and not store.runs()
        # After an authenticated context read a malformed tool root must close
        # the platform call, rather than leaving an in-flight queue slot forever.
        current=s.get(t['id']);current.update(status='awaiting_worker',callback_hash=None,call_id=uuid.uuid4().hex)
        current=s.save(current,'isolated_new_wait')
        base['call_id']=current['call_id']
        write_route(ROOT,current['id'],current['call_id'],current['token'],server.server_port)
        second_route=route_file(ROOT,current['id'],current['call_id'])
        config['payloads']=[{'operation':'design_context','args':{**base,'section':'index'}},'{"broken":',dict(base,module=m)]
        file.write_text(json.dumps(config),encoding='utf-8')
        probe=subprocess.run(['node',str(ROOT/'scripts/probe_design_transport.cjs'),str(file)],capture_output=True,text=True,encoding='utf-8',timeout=40,check=True)
        observed=json.loads(probe.stdout)
        assert observed[1]['code']=='TOOL_ARGUMENT_OBJECT'
        assert s.get(t['id'])['status']=='paused' and s.get(t['id'])['calls']==1
        assert observed[2]['status']=='recorded_late_result'
        assert store.modules()==original_modules
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        route.unlink(missing_ok=True)
        if second_route:second_route.unlink(missing_ok=True)
