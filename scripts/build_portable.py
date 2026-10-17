"""Build an offline Windows x64 release from a clean, hash-locked venv."""
import argparse,hashlib,json,platform,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
IGNORE=shutil.ignore_patterns('__pycache__','*.pyc','.pytest_cache','*.egg-info')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--environment',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    chain=json.loads((ROOT/'toolchain.json').read_text());assert platform.system()=='Windows' and platform.machine().lower() in ('amd64','x86_64')
    env=Path(args.environment).resolve();python=env/'Scripts/python.exe'
    info=json.loads(subprocess.check_output([str(python),'-c','import sys,json;print(json.dumps({"base":sys.base_prefix,"version":sys.version.split()[0]}))'],text=True))
    if info['version']!=chain['python']:raise ValueError('Python runtime version does not match toolchain.json')
    target=Path(args.output).resolve()
    if target.exists():raise ValueError('Output already exists; choose a new path, never overwrite user data')
    if not (ROOT/'frontend/dist/index.html').is_file():raise ValueError('Build frontend first')
    target.mkdir(parents=True);runtime=target/'runtime';base=Path(info['base']);runtime.mkdir()
    for f in base.iterdir():
        if f.is_file() and (f.suffix.lower() in ('.dll','.exe') or f.name=='LICENSE.txt'):shutil.copy2(f,runtime/f.name)
    shutil.copytree(base/'DLLs',runtime/'DLLs',ignore=IGNORE)
    shutil.copytree(base/'Lib',runtime/'Lib',ignore=shutil.ignore_patterns('site-packages','__pycache__','*.pyc','test','tests','tkinter','idlelib','ensurepip'))
    shutil.copytree(env/'Lib/site-packages',runtime/'Lib/site-packages',ignore=IGNORE)
    # Isolated search path excludes host Python/user site/PYTHONPATH.
    (runtime/'python314._pth').write_text('.\nLib\nDLLs\nLib/site-packages\n../backend\nimport site\n',encoding='utf-8')
    for name in ('backend','bridge','resources','examples','dsh'):
        shutil.copytree(ROOT/name,target/name,ignore=IGNORE)
    shutil.copytree(ROOT/'frontend/dist',target/'frontend/dist')
    (target/'scripts').mkdir()
    for name in ('launch.py','start.ps1','stop.ps1','verify_portable.py','install_flux_worker.cjs','install_dsh_presets.py'):
        shutil.copy2(ROOT/'scripts'/name,target/'scripts'/name)
    for name in ('Start.cmd','config.example.toml','toolchain.json','pytest.ini','README.md'):shutil.copy2(ROOT/name,target/name)
    # Ship user documentation, not local reports, histories or handoff materials.
    (target/'docs').mkdir()
    for p in (ROOT/'docs').iterdir():
        if p.is_file() and p.suffix.lower() in ('.md','.png','.jpg'):shutil.copy2(p,target/'docs'/p.name)
    if (ROOT/'docs/screenshots').exists():shutil.copytree(ROOT/'docs/screenshots',target/'docs/screenshots',ignore=IGNORE)
    for name in ('dbsrc-reference.json','portability-verification.json'):
        if (ROOT/'docs'/name).exists():shutil.copy2(ROOT/'docs'/name,target/'docs'/name)
    (target/'data').mkdir()
    files={p.relative_to(target).as_posix():sha(p) for p in sorted(target.rglob('*')) if p.is_file()}
    manifest={'format':'flux-release-v1','platform':chain['platform'],'toolchain':chain,'dependency_lock_sha256':sha(ROOT/'backend/requirements-win-py314.lock'),'frontend_lock_sha256':sha(ROOT/'frontend/package-lock.json'),'files':files}
    (target/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    archive=Path(str(target)+'.zip')
    if archive.exists():raise ValueError('Archive already exists')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(target.rglob('*')):
            if p.is_file():z.write(p,Path(target.name)/p.relative_to(target))
    archive.with_suffix('.zip.sha256').write_text(sha(archive)+'  '+archive.name+'\n',encoding='utf-8')
    print(json.dumps({'directory':str(target),'archive':str(archive),'sha256':sha(archive),'files':len(files),'bytes':archive.stat().st_size}))
if __name__=='__main__':main()
