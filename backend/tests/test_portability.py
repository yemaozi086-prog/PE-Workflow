import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('flux_launcher',ROOT/'scripts/launch.py');launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
def test_config_relative_to_root_not_cwd(tmp_path,monkeypatch):
    cfg=tmp_path/'config.toml';cfg.write_text('[storage]\ndatabase="data/new.sqlite"\n[dsh]\nenabled=false\n',encoding='utf-8');monkeypatch.chdir(tmp_path)
    parsed=launcher.configuration(cfg)
    assert Path(parsed['database'])==ROOT/'data/new.sqlite'
    assert parsed['dsh_enabled'] is False
@pytest.mark.parametrize('content',['[server]\nhost="0.0.0.0"','[server]\nport=0','[server]\nport=true','[dsh]\nurl="https://example.com"','[dsh]\nurl="http://127.0.0.1:3080/path"'])
def test_config_rejects_invalid_or_remote_bindings(tmp_path,content):
    cfg=tmp_path/'config.toml';cfg.write_text(content,encoding='utf-8')
    with pytest.raises(ValueError):launcher.configuration(cfg)
