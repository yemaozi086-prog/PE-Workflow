"""Optional one-command preparation, explicitly run by a developer.

Registers the verified baseline, runs it, saves a dedicated workflow and prepares
a DSH task. Never submits the developer reference answer or edits an existing graph.
Requires the local PE-Workflow server on 8020. No DSH credentials needed.
"""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

HERE=Path(__file__).resolve().parent
BASE='http://127.0.0.1:8020/api'


def request(path,data=None):
    req=Request(BASE+path,data=json.dumps(data,ensure_ascii=False).encode('utf-8') if data is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=120) as response:return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(exc.read().decode('utf-8')) from exc


def main():
    registration=json.loads((HERE/'baseline-registration.json').read_text(encoding='utf-8'))
    fixture=json.loads((HERE/'workflow.json').read_text(encoding='utf-8'))
    modules=request('/modules');base=registration['module']
    existing=next((m for m in modules if m['id']==base['id'] and m['version']==1),None)
    if existing and existing!=base:raise RuntimeError('同名 v1 已存在但内容不同；停止，未覆盖任何模块')
    if not existing:
        result=request('/harness/submit-candidate',registration)
        if result['status']!='registered':raise RuntimeError(json.dumps(result,ensure_ascii=False))
    workflows=request('/workflows');current=next((w for w in workflows if w['id']==fixture['workflow']['id']),None)
    if current:
        saved=request('/workflows/'+fixture['workflow']['id'])
        if saved['workflow']!=fixture['workflow'] or saved['inputs']!=fixture['inputs']:
            raise RuntimeError('实验工作流已被修改；停止，不重置用户的替换结果')
        revision=saved['revision']
    else:
        run=request('/runs',fixture)
        if run['status']!='succeeded':raise RuntimeError(json.dumps(run,ensure_ascii=False))
        revision=request('/workflows',{**fixture,'expected_revision':0})['revision']
    task_request=json.loads((HERE/'task-request.json').read_text(encoding='utf-8'));task_request['expected_revision']=revision
    task=request('/change-tasks',task_request)
    if task['status']!='awaiting_candidate':raise RuntimeError(json.dumps(task,ensure_ascii=False))
    (HERE/'prepared-task.local.json').write_text(json.dumps(task,ensure_ascii=False,indent=2),encoding='utf-8')
    print('真实电容损耗实验已准备。工作流：'+fixture['workflow']['name'])
    print('DSH 任务 ID：'+task['id'])
    print('状态：等待真实 DSH 候选，尚未执行替换。')


if __name__=='__main__':main()
