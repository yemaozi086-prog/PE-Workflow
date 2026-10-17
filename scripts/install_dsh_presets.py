"""Explicit optional integration. Does not change DSH's default agent."""
import argparse,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dsh-home',type=Path,default=Path.home()/'.dsh');args=ap.parse_args()
    for name in ('flux','flux-worker','flux-assembly'):
        source=ROOT/'dsh/preset'/name;target=args.dsh_home/'.agent-presets'/name
        if not source.exists():continue
        if target.exists():
            backup=target.with_name(name+'.before-flux-update')
            if backup.exists():raise ValueError('Backup exists; inspect it before repeating integration: '+str(backup))
            shutil.copytree(target,backup)
        shutil.copytree(source,target,dirs_exist_ok=True)
        (target/'flux-root.txt').write_text(ROOT.as_posix(),encoding='utf-8')
        print('Installed '+name+'; default agent unchanged')
if __name__=='__main__':main()
