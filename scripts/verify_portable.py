"""No-network smoke test using only a relocated release and a fresh database."""
import argparse,hashlib,json,math,os,socket,subprocess,sys,tempfile,time,urllib.request,urllib.error
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parents[1]
def request(base,path,body=None):
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=90) as r:return json.load(r)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output');args=ap.parse_args()
    manifest=json.loads((ROOT/'release-manifest.json').read_text())
    for name,h in manifest['files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=h:raise ValueError('Release file hash mismatch: '+name)
    with tempfile.TemporaryDirectory(prefix='flux-clean-') as temp:
        temp=Path(temp);config=temp/'config.toml'
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        db=temp/'fresh.sqlite';config.write_text('[server]\nhost="127.0.0.1"\nport='+str(port)+'\nopen_browser=false\n[storage]\ndatabase='+json.dumps(db.as_posix())+'\n[dsh]\nenabled=false\nurl="http://127.0.0.1:3080"\n',encoding='utf-8')
        env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','PE_WORKFLOW','PE_DSH','VIRTUAL_ENV'))}
        env['PATH']=os.path.join(os.environ.get('SystemRoot','C:/Windows'),'System32');env['PYTHONDONTWRITEBYTECODE']='1'
        python=ROOT/'runtime/python.exe';command=[str(python),str(ROOT/'scripts/launch.py'),'--config',str(config)]
        check=json.loads(subprocess.check_output(command+['--check'],cwd=temp,env=env,text=True,encoding='utf-8'))
        assert Path(check['prefix']).resolve()==(ROOT/'runtime').resolve()
        logfile=(temp/'server.log').open('w',encoding='utf-8');proc=subprocess.Popen(command+['--no-browser'],cwd=temp,env=env,stdout=logfile,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{port}'
        try:
            for _ in range(120):
                if proc.poll() is not None:raise RuntimeError('Server exited; '+(temp/'server.log').read_text(encoding='utf-8'))
                try:health=request(base,'/api/health');break
                except Exception:time.sleep(.5)
            else:raise TimeoutError('Server did not become ready')
            assert health['status']=='ok' and not health['automation']['enabled']
            with urllib.request.urlopen(base,timeout=10) as r:assert b'<html' in r.read()
            rows=request(base,'/api/workspace/workflows');assert rows['count']==0
            demo=request(base,'/api/examples/dictionaries');run=request(base,'/api/runs',{'workflow':demo['workflow'],'inputs':demo['inputs'],'timeout_seconds':60})
            assert run['status']=='succeeded',run.get('diagnostics')
            assert math.isclose(run['outputs']['loss']['cap1_loss'],1.1368210220849665,rel_tol=1e-10)
            replay=request(base,'/api/runs/'+run['id']+'/replay',{});assert replay['outputs']==run['outputs']
            # New registry must work without local data, credentials or a model service.
            proposal={'kind':'unit','name':'portable_kW_probe','description':'Portable verification fixture only'}
            pending=request(base,'/api/extension-requests?project_id=portable_check',proposal)
            assert pending['status']=='needs_information'
            try:request(base,'/api/extension-requests/'+pending['id']+'/decision',{'revision':pending['revision'],'approve':True})
            except urllib.error.HTTPError as error:
                assert json.load(error)['diagnostics'][0]['code']=='EXTENSION_INCOMPLETE'
            else:raise AssertionError('Incomplete extension was enabled')
            proposal.update(evidence='Test definition: 1 portable_kW_probe = 1000 W',unit_definition={'dimension':'power','scale':1000,'offset':0})
            pending=request(base,'/api/extension-requests/'+pending['id']+'/edit',{'revision':pending['revision'],'proposal':proposal})
            approved=request(base,'/api/extension-requests/'+pending['id']+'/decision',{'revision':pending['revision'],'approve':True})['entry']
            assert any(x['name']==proposal['name'] for x in request(base,'/api/extension-catalog?project_id=portable_check')['items'])
            assert not any(x['name']==proposal['name'] for x in request(base,'/api/extension-catalog?project_id=other_project')['items'])
            field={'type':'number','quantity':'power','unit':'W'}
            bag={'type':'flat_dict','kind':'flat_dict','fields':{'power':field}}
            module={'id':'portable_power_probe','version':1,'name':'Portable verification fixture','contract_version':'3.0','inputs':{'values':bag},'outputs':{'loss':bag},'expressions':{'loss':"{'power': values['power']}"},'provenance':'Portable verification fixture only'}
            registered=request(base,'/api/harness/submit-candidate',{'module':module,'anchors':[{'inputs':{'values':{'power':2}},'expected':{'loss':{'power':2}}}]})
            assert registered['status']=='registered',registered
            custom={'type':'flat_dict','kind':'flat_dict','fields':{'power':{**field,'unit':proposal['name'],'unit_definition':proposal['unit_definition'],'extension_refs':[{k:approved[k] for k in ('project_id','kind','name','version','fingerprint')}]}}}
            workflow={'schema_version':'3.0','id':'portable_custom_unit','name':'Custom unit conversion probe','project_id':'portable_check','inputs':{'values':custom},'nodes':[{'id':'calculate','module_id':module['id'],'module_version':1,'inputs':{'values':{'source':'input','port':'values'}}}],'outputs':{'loss':{'source':'node','node':'calculate','port':'loss'}},'output_ports':{'loss':bag}}
            unit_run=request(base,'/api/runs',{'workflow':workflow,'inputs':{'values':{'power':2}}})
            assert unit_run['status']=='succeeded' and unit_run['outputs']['loss']['power']==2000,unit_run
            unit_replay=request(base,'/api/runs/'+unit_run['id']+'/replay',{});assert unit_replay['outputs']==unit_run['outputs']
            config_check=request(base,'/api/design-configuration');assert config_check['preset']=='flux-design-v2'
            dbsrc=request(base,'/api/examples/dbsrc');db_run=request(base,'/api/runs',{'workflow':dbsrc['workflow'],'inputs':dbsrc['inputs'],'timeout_seconds':60})
            assert db_run['status']=='succeeded',db_run.get('diagnostics')
            db_replay=request(base,'/api/runs/'+db_run['id']+'/replay',{});assert db_replay['outputs']==db_run['outputs']
            result=db_run['outputs']['result'];assert math.isclose(result['reward'],0.704378924970644,rel_tol=1e-9),result
            report={'status':'passed','isolation':'relocated runtime, fresh DB, unrelated CWD, system-only PATH, no DSH or network install','runtime':check,'checked_files':len(manifest['files']),'dictionary_loss':run['outputs'],'replay_equal':True,'project_extensions':{'incomplete_approval_rejected':True,'project_isolation':True,'custom_unit_watts':unit_run['outputs']['loss']['power'],'frozen_replay_equal':True},'design_preset':config_check['preset'],'dbsrc_result':{k:result[k] for k in ('valid','reward','efficiency','volume','cost')},'dbsrc_replay_equal':True,'scope':'Same Windows host, not a pristine OS VM; real DSH calls excluded.'}
        finally:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            logfile.close()
    text=json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:Path(args.output).write_text(text,encoding='utf-8')
    print(text)
if __name__=='__main__':main()
