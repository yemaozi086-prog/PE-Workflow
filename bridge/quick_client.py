"""Task-bound local transport for the dedicated direct-conversation preset."""
import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    filename=Path(sys.argv[1]).resolve()
    if filename.parent!=(ROOT/'data/dsh-inbox').resolve() or not re.fullmatch(r'[a-f0-9]{32}\.json',filename.name):raise ValueError('Invalid inbox path')
    if filename.stat().st_size>2_000_000:raise ValueError('Payload exceeds 2 MB')
    try:data=json.loads(filename.read_text(encoding='utf-8'))
    finally:filename.unlink(missing_ok=True)
    chat=data.pop('chat_id');turn=data.get('turn_id');token=data.get('token')
    if not all(isinstance(k,str) and re.fullmatch(r'[a-f0-9]{32}',k) for k in (chat,turn)):raise ValueError('Invalid turn identity')
    from pe_workflow.worker_routes import resolve_route
    port=resolve_route(ROOT,chat,turn,token,8020)
    req=urllib.request.Request(f'http://127.0.0.1:{port}/api/quick-chats/{chat}/tool',data=json.dumps(data,ensure_ascii=False).encode('utf-8'),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=90) as r:print(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:print(e.read().decode('utf-8'))


if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'status':'transport_error','error':str(e),'notice':'Do not assume saved. Report the transport failure to the user.'},ensure_ascii=False))
