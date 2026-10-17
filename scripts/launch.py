"""Relocatable launcher: stdlib bootstrap, no implicit package installation."""
import argparse,json,os,socket,sys,threading,tomllib,webbrowser
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parents[1]
def configuration(path=None):
    file=Path(path).resolve() if path else ROOT/('config.local.toml' if (ROOT/'config.local.toml').exists() else 'config.example.toml')
    with file.open('rb') as f:cfg=tomllib.load(f)
    server=cfg.get('server',{});host=server.get('host','127.0.0.1');port=server.get('port',8020)
    if host not in ('127.0.0.1','localhost'):raise ValueError('Only loopback binding is supported')
    if type(port)!=int or not 1<=port<=65535:raise ValueError('port must be 1..65535')
    from urllib.parse import urlsplit
    dsh=cfg.get('dsh',{});url=dsh.get('url','http://127.0.0.1:3080').rstrip('/');parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:raise ValueError('DSH URL must be a local HTTP origin')
    database=Path(cfg.get('storage',{}).get('database','data/workbench.sqlite'))
    if not database.is_absolute():database=ROOT/database
    return {'host':host,'port':port,'database':str(database.resolve()),'dsh_url':url,'dsh_enabled':bool(dsh.get('enabled',False)),'open_browser':bool(server.get('open_browser',True))}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config');parser.add_argument('--port',type=int);parser.add_argument('--no-browser',action='store_true');parser.add_argument('--check',action='store_true');args=parser.parse_args()
    cfg=configuration(args.config)
    if args.port is not None:
        if not 1<=args.port<=65535:raise ValueError('port must be 1..65535')
        cfg['port']=args.port
    os.environ.update(PE_WORKFLOW_ROOT=str(ROOT),PE_WORKFLOW_DB=cfg['database'],PE_DSH_URL=cfg['dsh_url'],PE_DSH_ENABLED='1' if cfg['dsh_enabled'] else '0',PYTHONUTF8='1')
    # Child workers use exactly the same relocated source and interpreter.
    sys.path.insert(0,str(ROOT/'backend'));os.environ['PYTHONPATH']=str(ROOT/'backend')
    if args.check:
        import fastapi,numpy,scipy,uvicorn
        from pe_workflow.dbsrc import runtime_hash,registry
        assert all(m.builtin_hash==runtime_hash() for m in registry().values()),'Frozen scientific source hash mismatch'
        print(json.dumps({'status':'ok','python':sys.version.split()[0],'prefix':sys.prefix,'root':str(ROOT),'configuration':cfg,'scientific_hash':runtime_hash()},ensure_ascii=False));return
    if not (ROOT/'frontend/dist/index.html').exists():raise RuntimeError('Frontend build is missing; run setup.ps1 or use the portable release')
    with socket.socket() as probe:probe.bind((cfg['host'],cfg['port']))
    url=f"http://{cfg['host']}:{cfg['port']}"
    print('Flux '+url+' | Ctrl+C stops server',flush=True)
    if cfg['open_browser'] and not args.no_browser:
        def open_when_ready():
            import time,urllib.request
            for _ in range(60):
                try:
                    with urllib.request.urlopen(url+'/api/health',timeout=1) as r:
                        if r.status==200:webbrowser.open(url);return
                except Exception:time.sleep(.5)
        threading.Thread(target=open_when_ready,daemon=True).start()
    import uvicorn
    uvicorn.run('pe_workflow.api:app',host=cfg['host'],port=cfg['port'])
if __name__=='__main__':
    try:main()
    except Exception as exc:print('Startup failed: '+str(exc),file=sys.stderr);sys.exit(1)
