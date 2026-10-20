import sys,json,subprocess
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bridge'))
import flux_worker_client as bridge

def test_large_file_payload_bypasses_command_line_limit_and_is_cleaned(tmp_path,monkeypatch):
    monkeypatch.setattr(bridge,'_config_root',tmp_path)
    inbox=tmp_path/'data/dsh-inbox';inbox.mkdir(parents=True);path=inbox/('a'*32+'.json')
    data={'source':'x'*100000};path.write_text(json.dumps(data),encoding='utf-8')
    assert bridge.load_payload(['client','design-result','--file',str(path)])==data
    assert not path.exists()

def test_outside_inbox_is_rejected_without_deleting(tmp_path,monkeypatch):
    monkeypatch.setattr(bridge,'_config_root',tmp_path);path=tmp_path/('a'*32+'.json');path.write_text('{}')
    with pytest.raises(ValueError):bridge.load_payload(['client','design-result','--file',str(path)])
    assert path.exists()

def test_invalid_json_is_cleaned(tmp_path,monkeypatch):
    monkeypatch.setattr(bridge,'_config_root',tmp_path);inbox=tmp_path/'data/dsh-inbox';inbox.mkdir(parents=True);path=inbox/('b'*32+'.json');path.write_text('{')
    with pytest.raises(ValueError):bridge.load_payload(['client','design-result','--file',str(path)])
    assert not path.exists()


@pytest.mark.parametrize('data,code',[("malformed raw tool text",'TOOL_ARGUMENT_OBJECT'),({'_tool_runtime_hash':'stale'},'TOOL_RUNTIME_MISMATCH')])
def test_bridge_rejects_bad_root_or_stale_runtime_before_http(data,code):
    import uuid
    root=Path(__file__).resolve().parents[2]
    inbox=root/'data/dsh-inbox';inbox.mkdir(exist_ok=True,parents=True)
    path=inbox/(uuid.uuid4().hex+'.json');path.write_text(json.dumps(data),encoding='utf-8')
    result=subprocess.run([sys.executable,str(root/'bridge/flux_worker_client.py'),'design-result','--file',str(path)],capture_output=True,text=True,encoding='utf-8',check=True)
    value=json.loads(result.stdout)
    assert value['code']==code and value['submitted'] is False and value['stop_required']
    assert not path.exists() and '.pop' not in result.stdout
