"""Verify the local deployed API, save the new DBSRC example, run and replay.

Never overwrite an existing workflow revision. No DSH request is sent.
"""
import json
from pathlib import Path
import time
from urllib.request import Request,urlopen
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


def api(path,data=None):
    req=Request('http://127.0.0.1:8020/api'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=120) as response:return json.load(response)


def main():
    example=api('/examples/dbsrc'); request={k:example[k] for k in ('workflow','inputs')}
    assert api('/validate',request)['valid']
    existing=next((w for w in api('/workflows') if w['id']==example['workflow']['id']),None)
    if existing:
        # Respect the saved user graph; verification uses the frozen example.
        revision=existing['revision']
    else:revision=api('/workflows',{**request,'expected_revision':0})['revision']
    start=time.perf_counter()
    run=api('/runs',{**request,'timeout_seconds':60})
    assert run['status']=='succeeded',run.get('diagnostics')
    replay=api('/runs/'+run['id']+'/replay',{})
    assert replay['status']=='succeeded',replay.get('diagnostics')
    reference=json.loads((ROOT/'docs/dbsrc-reference.json').read_text(encoding='utf-8'))['cases']['fresh_default']
    result=run['outputs']['result']; repeat=replay['outputs']['result']
    np.testing.assert_allclose(result['efficiency_matrix'],reference['efficiency_matrix'],rtol=1e-9,atol=1e-10)
    np.testing.assert_allclose(result['detail_matrix'],reference['detailed_params_matrix'],rtol=1e-9,atol=1e-10)
    assert abs(result['reward']-reference['reward'])<1e-10
    assert result==repeat
    record={'kind':'deployed_api_and_frozen_replay','workflow_id':example['workflow']['id'],'saved_revision':revision,
            'run_id':run['id'],'replay_id':replay['id'],'snapshot_hash':run['snapshot_hash'],
            'reward':result['reward'],'efficiency':result['efficiency'],'volume':result['volume'],'cost':result['cost'],
            'efficiency_max_abs_error':float(np.max(abs(np.array(result['efficiency_matrix'])-reference['efficiency_matrix']))),
            'detail_max_abs_error':float(np.max(abs(np.array(result['detail_matrix'])-reference['detailed_params_matrix']))),
            'replay_identical':True,'trace_steps':len(run['trace']),'elapsed_including_replay_seconds':time.perf_counter()-start,
            'dsh_called':False}
    (ROOT/'docs/dbsrc-api-verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__':main()
