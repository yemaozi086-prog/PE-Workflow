import pytest
import runpy
from pathlib import Path
from pe_workflow.worker_routes import resolve_route,write_route


def test_callback_destination_is_platform_owned_and_call_bound(tmp_path):
    task,call='a'*32,'b'*32
    assert resolve_route(tmp_path,task,call,'test-secret',8020)==8020
    write_route(tmp_path,task,call,'test-secret',8022)
    assert resolve_route(tmp_path,task,call,'test-secret',8020)==8022
    assert 'test-secret' not in next((tmp_path/'data/dsh-routes').iterdir()).read_text()
    with pytest.raises(ValueError,match='credentials'):resolve_route(tmp_path,task,call,'wrong',8020)
    assert resolve_route(tmp_path,task,'c'*32,'test-secret',8020)==8020
    with pytest.raises(ValueError):write_route(tmp_path,'../bad',call,'test-secret',8022)
    for port in (True,80,70000,'8022'):
        with pytest.raises(ValueError):write_route(tmp_path,task,call,'test-secret',port)


def test_worker_receipt_closes_call_without_echoing_independent_answers():
    module=runpy.run_path(str(Path(__file__).resolve().parents[2]/'bridge/flux_worker_client.py'))
    result=module['result_receipt']({'status':'paused','remaining_calls':2,'last_report':{'code':'EXAMPLE_RESULT_MISMATCH'},'verification_feedback':{'expected':42},'configuration':{'prompt':'large'},'plan':{'steps':['large']}})
    assert result['stop_required'] and result['remaining_calls']==2
    assert 'verification_feedback' not in result and 'configuration' not in result and 'plan' not in result
