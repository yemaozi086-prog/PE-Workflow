"""Read-only family view; candidate tasks are never executable registrations."""
import json
from .changes import locate
from .models import Workflow


def catalog(store):
    families={}
    with store.connect() as con:
        relations={(r[0],r[1]):json.loads(r[2]) for r in con.execute('SELECT id,version,document FROM module_relations')}
        # Recover older proven lineage from frozen task contexts, never vN-1.
        for task_doc,context_doc in con.execute("SELECT t.document,c.document FROM change_tasks t JOIN contexts c ON c.id=json_extract(t.document,'$.context_id') WHERE json_extract(t.document,'$.status')='applied'"):
            task,ctx=json.loads(task_doc),json.loads(context_doc);base=ctx.get('base_module',{})
            if task.get('module_ref') and base:
                module_id,version=task['module_ref'].rsplit('@',1)
                relations.setdefault((module_id,int(version)),{'parent_version':base['version'],'summary':task.get('instruction',''),'acceptance_label':task.get('label',''),'task_id':task['id']})
        has_automation=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_tasks'").fetchone()
        tasks=[json.loads(r[0]) for r in con.execute('SELECT document FROM automation_tasks')] if has_automation else []
    for m in store.modules():
        f=families.setdefault(m['id'],{'id':m['id'],'name':m['name'],'versions':[],'candidates':[]})
        f['name']=m['name']
        f['versions'].append({'version':m['version'],'state':'available','name':m['name'],'description':m.get('description',''),'implementation':m['implementation'],'lineage':relations.get((m['id'],m['version'])),'uses':[]})
    for head in store.workflows():
        document=store.workflow(head['id']);graph=Workflow.model_validate(document['workflow'])
        def visit(w,path):
            for node in w.nodes:
                if node.module_id in families:
                    version=next((v for v in families[node.module_id]['versions'] if v['version']==node.module_version),None)
                    if version:version['uses'].append({'workflow_id':head['id'],'name':head['name'],'revision':head['revision'],'node_path':[*path,node.id]})
                if node.body:visit(node.body,[*path,node.id,'body'])
                for key,case in node.cases.items():visit(case,[*path,node.id,'cases',key])
        visit(graph,[])
    for task in tasks:
        if task['status'] in ('applied','already_current'):continue
        req=task.get('request',{})
        try:
            _,node=locate(Workflow.model_validate(req['workflow']),req['node_path'])
            module_id=node.module_id
        except (KeyError,ValueError):continue
        if module_id not in families:continue
        candidate=task.get('candidate') or {}
        families[module_id]['candidates'].append({'task_id':task['id'],'version':candidate.get('version'),'parent_version':node.module_version,'state':task['status'],'summary':task.get('instruction',''),'message':task.get('message',''),'diagnostics':task.get('last_report',{}).get('diagnostics',[]),'created_at':task['created_at']})
    items=list(families.values())
    return {'items':items,'count':len(items)}
