"""Platform-owned loopback callback routing, bound to one call's credentials."""
import hashlib
import hmac
import json
import re
from pathlib import Path


def route_file(root,task_id,call_id):
    if any(not re.fullmatch(r'[0-9a-f]{32}',v) for v in (task_id,call_id)):
        raise ValueError('Invalid task/call id')
    return Path(root)/'data/dsh-routes'/(task_id+'-'+call_id+'.json')


def write_route(root,task_id,call_id,token,port):
    if type(port)!=int or not 1024<=port<=65535:raise ValueError('Invalid loopback service port')
    path=route_file(root,task_id,call_id);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'port':port,'credential_hash':hashlib.sha256(token.encode()).hexdigest()}),encoding='utf-8')


def resolve_route(root,task_id,call_id,token,default):
    path=route_file(root,task_id,call_id)
    if not path.exists():return default
    row=json.loads(path.read_text(encoding='utf-8'))
    if not hmac.compare_digest(row['credential_hash'],hashlib.sha256(token.encode()).hexdigest()):
        raise ValueError('Callback route credentials do not match')
    port=row['port']
    if type(port)!=int or not 1024<=port<=65535:raise ValueError('Invalid loopback service port')
    return port
