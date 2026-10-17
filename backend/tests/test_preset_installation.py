import subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def test_preset_install_is_repeatable_and_backs_up_relocation(tmp_path):
    home=tmp_path/'isolated dsh home'
    command=[sys.executable,str(ROOT/'scripts/install_dsh_presets.py'),'--dsh-home',str(home),'--only','flux-design']
    subprocess.run(command,check=True,capture_output=True)
    target=home/'.agent-presets/flux-design-v2'
    assert (target/'flux-root.txt').read_text(encoding='utf-8')==ROOT.as_posix()
    result=subprocess.run(command,check=True,capture_output=True,text=True)
    assert 'Already current' in result.stdout
    assert not list(target.parent.glob('*.before-flux-update-*'))
    (target/'flux-root.txt').write_text('old/location',encoding='utf-8')
    subprocess.run(command,check=True,capture_output=True)
    backups=list(target.parent.glob('flux-design-v2.before-flux-update-*'))
    assert len(backups)==1 and (backups[0]/'flux-root.txt').read_text(encoding='utf-8')=='old/location'
    assert (target/'flux-root.txt').read_text(encoding='utf-8')==ROOT.as_posix()
