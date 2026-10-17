"""Explicit optional integration. Does not change DSH's default agent."""
import argparse,shutil,datetime,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dsh-home',type=Path,default=Path.home()/'.dsh');ap.add_argument('--only',choices=('flux','flux-worker','flux-assembly','flux-design'));args=ap.parse_args()
    for name in ('flux','flux-worker','flux-assembly','flux-design'):
        if args.only and name!=args.only:continue
        source=ROOT/'dsh/preset'/name;target=args.dsh_home/'.agent-presets'/('flux-design-v2' if name=='flux-design' else name)
        if not source.exists():continue
        if target.exists():
            files=[p for p in source.rglob('*') if p.is_file() and p.name!='flux-root.txt']
            if all((target/p.relative_to(source)).is_file() and (target/p.relative_to(source)).read_bytes()==p.read_bytes() for p in files) and (target/'flux-root.txt').is_file() and (target/'flux-root.txt').read_text(encoding='utf-8')==ROOT.as_posix():
                print('Already current: '+target.name);continue
            backup=target.with_name(target.name+'.before-flux-update-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
            shutil.copytree(target,backup)
        shutil.copytree(source,target,dirs_exist_ok=True)
        (target/'flux-root.txt').write_text(ROOT.as_posix(),encoding='utf-8')
        print('Installed '+name+'; default agent unchanged')
if __name__=='__main__':main()
