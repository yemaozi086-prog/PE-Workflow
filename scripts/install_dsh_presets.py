"""Explicit optional integration. Does not change DSH's default agent."""
import argparse,shutil,datetime,uuid,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from pe_workflow.preset_runtime import render_prompt,tool_filename

def copy_tree(source,target,**kwargs):
    # Backups add a suffix to already deep Windows workspaces. Use the native
    # extended path form so a valid preset update cannot fail at MAX_PATH.
    def native(p):
        value=str(Path(p).resolve())
        if sys.platform=='win32' and not value.startswith('\\\\?\\'):
            return '\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value
        return value
    return shutil.copytree(native(source),native(target),**kwargs)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dsh-home',type=Path,default=Path.home()/'.dsh');ap.add_argument('--only',choices=('flux','flux-worker','flux-assembly','flux-design','pe-quick'));args=ap.parse_args()
    for name in ('flux','flux-worker','flux-assembly','flux-design','pe-quick'):
        if args.only and name!=args.only:continue
        source=ROOT/'dsh/preset'/name;target=args.dsh_home/'.agent-presets'/('flux-design-v3' if name=='flux-design' else name)
        if not source.exists():continue
        expected={p.relative_to(source):p.read_bytes() for p in source.rglob('*') if p.is_file() and p.name!='flux-root.txt'}
        if name=='flux-design':
            code=expected[Path('flux-design-tools.js')]
            expected[Path(tool_filename(code))]=code
            expected[Path('agent.cordis.yml')]=render_prompt(expected[Path('agent.cordis.yml')].decode('utf-8'),code).encode('utf-8')
        if name=='pe-quick':
            import hashlib
            code=expected[Path('quick-tools.js')];filename='quick-tools-'+hashlib.sha256(code).hexdigest()[:20]+'.js'
            expected[Path(filename)]=code
            expected[Path('agent.cordis.yml')]=expected[Path('agent.cordis.yml')].decode('utf-8').replace('./quick-tools.js','./'+filename).encode('utf-8')
        if target.exists():
            if all((target/p).is_file() and (target/p).read_bytes()==data for p,data in expected.items()) and (target/'flux-root.txt').is_file() and (target/'flux-root.txt').read_text(encoding='utf-8')==ROOT.as_posix():
                print('Already current: '+target.name);continue
            backup=target.with_name(target.name+'.before-flux-update-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
            copy_tree(target,backup)
        copy_tree(source,target,dirs_exist_ok=True)
        for relative,data in expected.items():(target/relative).write_bytes(data)
        (target/'flux-root.txt').write_text(ROOT.as_posix(),encoding='utf-8')
        print('Installed '+name+'; default agent unchanged')
if __name__=='__main__':main()
