import subprocess,sys
from pathlib import Path
from pe_workflow.preset_runtime import tool_filename,render_prompt

ROOT=Path(__file__).resolve().parents[2]

def test_rendered_prompt_is_identical_for_windows_and_unix_newlines():
    source=b'fixed tool source'
    assert render_prompt('a\r\n./flux-design-tools.js\r\n',source)==render_prompt('a\n./flux-design-tools.js\n',source)

def test_preset_install_is_repeatable_and_backs_up_relocation(tmp_path):
    home=tmp_path/'isolated dsh home'
    command=[sys.executable,str(ROOT/'scripts/install_dsh_presets.py'),'--dsh-home',str(home),'--only','flux-design']
    subprocess.run(command,check=True,capture_output=True)
    target=home/'.agent-presets/flux-design-v3'
    assert (target/'flux-root.txt').read_text(encoding='utf-8')==ROOT.as_posix()
    source=(ROOT/'dsh/preset/flux-design/flux-design-tools.js').read_bytes()
    assert tool_filename(source) in (target/'agent.cordis.yml').read_text(encoding='utf-8')
    assert (target/tool_filename(source)).read_bytes()==source
    assert tool_filename(source)!=tool_filename(source+b'\n// changed runtime')
    result=subprocess.run(command,check=True,capture_output=True,text=True)
    assert 'Already current' in result.stdout
    assert not list(target.parent.glob('*.before-flux-update-*'))
    (target/'flux-root.txt').write_text('old/location',encoding='utf-8')
    subprocess.run(command,check=True,capture_output=True)
    backups=list(target.parent.glob('flux-design-v3.before-flux-update-*'))
    assert len(backups)==1 and (backups[0]/'flux-root.txt').read_text(encoding='utf-8')=='old/location'
    assert (target/'flux-root.txt').read_text(encoding='utf-8')==ROOT.as_posix()
