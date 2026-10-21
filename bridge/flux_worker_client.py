"""Two task-bound worker operations. No generic HTTP or publication endpoint."""
import json
from pathlib import Path
import tomllib
_config_root=Path(__file__).resolve().parents[1]
_config_path=_config_root/('config.local.toml' if (_config_root/'config.local.toml').exists() else 'config.example.toml')
_config=tomllib.loads(_config_path.read_text(encoding='utf-8')) if _config_path.exists() else {}
_service_port=_config.get('server',{}).get('port',8020)
if type(_service_port)!=int or not 1<=_service_port<=65535:raise ValueError('Invalid configured service port')
import base64
import re
import sys
import urllib.request
import urllib.error
import urllib.parse

def load_payload(argv):
    if argv[2]!='--file':return json.loads(base64.b64decode(argv[2]).decode('utf-8'))
    path=Path(argv[3]).resolve();inbox=(_config_root/'data/dsh-inbox').resolve()
    if path.parent!=inbox or not re.fullmatch(r'[a-f0-9]{32}\.json',path.name):raise ValueError('Invalid bridge inbox path')
    try:
        if path.stat().st_size>2_000_000:raise ValueError('Bridge payload exceeds 2 MB')
        return json.loads(path.read_text(encoding='utf-8'))
    finally:path.unlink(missing_ok=True)


def result_receipt(value):
    """A submission closes this call. Never echo human-only acceptance data."""
    keys=('id','revision','status','message','remaining_calls','execution_state','worker_feedback','failure_routing','last_report')
    return {**{k:value[k] for k in keys if k in value},'stop_required':True,
            'instruction':'This submission is complete, including when validation failed. End this turn. The platform schedules a separate call for any permitted repair; do not resubmit or read context using these credentials.'}

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    operation=sys.argv[1];data=load_payload(sys.argv)
    if not isinstance(data,dict):
        print(json.dumps({'status':'not_submitted','submitted':False,'code':'TOOL_ARGUMENT_OBJECT','diagnostics':[{'path':'/','message':'工具参数必须为完整 JSON 对象；不解析或修补字符串候选。'}],'stop_required':True},ensure_ascii=False));return
    if operation.startswith('design-'):
        import hashlib
        expected=hashlib.sha256((_config_root/'dsh/preset/flux-design/flux-design-tools.js').read_bytes()).hexdigest()
        if data.pop('_tool_runtime_hash',None)!=expected:
            if not all(isinstance(data.get(k),str) for k in ('task_id','call_id','token')):
                print(json.dumps({'status':'not_submitted','submitted':False,'code':'TOOL_RUNTIME_MISMATCH','stop_required':True}));return
            data={k:v for k,v in data.items() if k in ('task_id','call_id','token')}
            data['code']='TOOL_RUNTIME_MISMATCH';operation='design-failure'
    key=data.pop('task_id');call=data['call_id'];token=data['token']
    data.pop('service_port',None)  # model arguments cannot choose a callback destination
    sys.path.insert(0,str(_config_root/'backend'))
    from pe_workflow.worker_routes import resolve_route
    service_port=resolve_route(_config_root,key,call,token,_service_port)
    if not re.fullmatch('[0-9a-f]{32}',key) or not re.fullmatch('[0-9a-f]{32}',call):raise ValueError('Invalid task/call id')
    design=operation in ('design-context','design-result','design-module','design-failure','design-precheck')
    if design:operation=operation.removeprefix('design-')
    assembly=operation in ('assembly-context','assembly-result')
    if assembly:operation=operation.removeprefix('assembly-')
    base='http://127.0.0.1:'+str(service_port)+'/api/'+('workflow-designs/' if design else 'assembly-tasks/' if assembly else 'automation-tasks/')+key
    if design and operation=='module':
        operation='context';call+='&module_ref='+urllib.parse.quote(data['module_ref'],safe='')
    if design and operation=='context':
        call+='&section='+urllib.parse.quote(data.get('section','index'),safe='')
        call+='&offset='+str(int(data.get('offset',0)))
        if data.get('step_id'):call+='&step_id='+urllib.parse.quote(data['step_id'],safe='')
    if operation=='context':req=urllib.request.Request(base+'/context?call_id='+call,headers={'Authorization':'Bearer '+token})
    elif design and operation=='failure':req=urllib.request.Request(base+'/transport-failure',data=json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'},method='POST')
    elif design and operation=='precheck':req=urllib.request.Request(base+'/formula-precheck',data=json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'},method='POST')
    elif operation=='result':req=urllib.request.Request(base+'/result',data=json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'},method='POST')
    else:raise ValueError('Only context/result are allowed')
    try:
        with urllib.request.urlopen(req,timeout=90 if design and operation=='precheck' else 30) as response:
            value=json.load(response)
            print(json.dumps(result_receipt(value) if design and operation=='result' else value,ensure_ascii=False))
    except urllib.error.HTTPError as exc:print(json.dumps({'http_status':exc.code,'response':json.loads(exc.read().decode('utf-8')),**({'stop_required':True,'instruction':'Precheck rejected; stop and inspect the current task, never resubmit blindly.'} if design and operation=='precheck' else {})},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as exc:print(json.dumps({'status':'failed','error':str(exc)},ensure_ascii=False))
