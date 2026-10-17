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

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    operation=sys.argv[1];data=json.loads(base64.b64decode(sys.argv[2]).decode('utf-8'))
    key=data.pop('task_id');call=data['call_id'];token=data['token']
    if not re.fullmatch('[0-9a-f]{32}',key) or not re.fullmatch('[0-9a-f]{32}',call):raise ValueError('Invalid task/call id')
    assembly=operation in ('assembly-context','assembly-result')
    if assembly:operation=operation.removeprefix('assembly-')
    base='http://127.0.0.1:'+str(_service_port)+'/api/'+('assembly-tasks/' if assembly else 'automation-tasks/')+key
    if operation=='context':req=urllib.request.Request(base+'/context?call_id='+call,headers={'Authorization':'Bearer '+token})
    elif operation=='result':req=urllib.request.Request(base+'/result',data=json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'},method='POST')
    else:raise ValueError('Only context/result are allowed')
    try:
        with urllib.request.urlopen(req,timeout=30) as response:print(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:print(json.dumps({'http_status':exc.code,'response':json.loads(exc.read().decode('utf-8'))},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as exc:print(json.dumps({'status':'failed','error':str(exc)},ensure_ascii=False))
