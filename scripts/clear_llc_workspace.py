"""Remove LLC test records from the live library, with a row-exact SQLite backup.

Dry-run by default. Stop the local service before --apply. Preserves shared
modules and historical snapshots belonging to unrelated workflows.
"""
import argparse,json,re,sqlite3,sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATTERN=re.compile(r'\bLLC\b|llc_',re.I)

def named(d):
    return bool(PATTERN.search(' '.join(str(d.get(k,'')) for k in ('id','name','title','instruction','description'))))

def graph_refs(w):
    refs=set()
    if not isinstance(w,dict):return refs
    for n in w.get('nodes',[]):
        if n.get('module_id'):refs.add((n['module_id'],n.get('module_version')))
        refs.update(graph_refs(n.get('body')))
        for g in n.get('cases',{}).values():refs.update(graph_refs(g))
    return refs

def inventory(c):
    c.row_factory=sqlite3.Row
    tables={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    selected={};cache={}
    def rows(table):
        if table not in cache:cache[table]=[dict(r) for r in c.execute('SELECT rowid AS __rowid__,* FROM '+table)] if table in tables else []
        return cache[table]
    def choose(table,predicate):
        selected[table]={r['__rowid__'] for r in rows(table) if predicate(r,json.loads(r['document']))}
    designs=rows('workflow_designs');design_map={r['id']:json.loads(r['document']) for r in designs}
    ids={k for k,t in design_map.items() if named(t.get('request',{})) or named(t.get('plan') or {})}
    queues=rows('tool_completion_queues');queue_ids=set()
    changed=True
    while changed:
        before=(len(ids),len(queue_ids))
        for key,t in design_map.items():
            if t.get('parent_id') in ids or t.get('superseded_by') in ids or t.get('queue_id') in queue_ids:ids.add(key)
        for row in queues:
            q=json.loads(row['document']);linked={q.get('source_id'),q.get('result_design_id')}
            for item in q.get('items',[]):linked.update([item.get('task_id'),*item.get('previous_task_ids',[])])
            if q['id'] in queue_ids or linked & ids:
                queue_ids.add(q['id']);ids.update(x for x in linked if x in design_map)
        changed=before!=(len(ids),len(queue_ids))
    modules=rows('modules');module_keys={(r['id'],r['version']) for r in modules if named(json.loads(r['document']))}
    for key in ids:
        t=design_map[key]
        if t.get('status')=='formula_registered' and t.get('candidate'):
            m=t['candidate'];module_keys.add((m['id'],m['version']))
    # The five registered tools include the generically named scan assembler.
    for row in queues:
        q=json.loads(row['document'])
        if q['id'] in queue_ids:
            for item in q.get('items',[]):
                t=design_map.get(item.get('task_id'),{})
                if item.get('registered') and t.get('candidate'):
                    m=t['candidate'];module_keys.add((m['id'],m['version']))
    existing={(r['id'],r['version']) for r in modules};module_keys &= existing
    workflows=rows('workflows');workflow_ids=set()
    for row in workflows:
        w=json.loads(row['document']).get('workflow',{})
        if named(w):workflow_ids.add(row['id'])
    for key in ids:
        w=design_map[key].get('workflow')
        if isinstance(w,dict):workflow_ids.add(w['id'])
    # Do not remove a tool used by any unrelated saved workflow.
    shared=set()
    for row in workflows:
        if row['id'] not in workflow_ids:shared |= graph_refs(json.loads(row['document']).get('workflow',{})) & module_keys
    module_keys-=shared
    choose('workflow_designs',lambda r,d:r['id'] in ids)
    choose('tool_completion_queues',lambda r,d:r['id'] in queue_ids)
    choose('modules',lambda r,d:(r['id'],r['version']) in module_keys)
    choose('workflows',lambda r,d:r['id'] in workflow_ids)
    # Large frozen run records are filtered using only their executed graph,
    # never the all-modules snapshot (which may contain unrelated catalog tools).
    run_ids=set();run_rows=set()
    for row in c.execute("SELECT rowid,id,json_extract(document,'$.snapshot.workflow') AS graph, json_extract(document,'$.workflow_name') AS name FROM runs"):
        w=json.loads(row['graph']) if row['graph'] else {}
        if named(w) or PATTERN.search(row['name'] or '') or w.get('id') in workflow_ids or graph_refs(w)&module_keys:
            run_ids.add(row['id']);run_rows.add(row['rowid'])
    selected['runs']=run_rows
    for table in ('change_tasks','automation_tasks','assembly_tasks'):
        choose(table,lambda r,d:named(d) or named(d.get('request') or {}) or d.get('workflow_id') in workflow_ids or d.get('change_task_id') in ids)
        ids.update(r['id'] for r in rows(table) if r['__rowid__'] in selected[table])
    for table in ('automation_events','assembly_events','design_late_results','design_progress','design_candidate_receipts','design_closed_calls'):
        if table in tables:selected[table]={r['rowid'] for r in c.execute('SELECT rowid,task_id FROM '+table) if r['task_id'] in ids}
    for table in ('module_relations','naming_rules'):
        choose(table,lambda r,d:(r['id'],r['version']) in module_keys or named(d))
    for table in ('workflow_meta','workflow_drafts'):
        if table in tables:selected[table]={r['rowid'] for r in c.execute('SELECT rowid,id FROM '+table) if r['id'] in workflow_ids}
    for table in ('assembly_input_profiles','extension_requests','project_extensions'):
        choose(table,lambda r,d:named(d) or d.get('source_id') in ids or d.get('source_task_id') in ids or d.get('design_id') in ids)
    # Delete catalog snapshots only when exclusively owned by removed tasks.
    own=set();retain=set()
    for table in ('workflow_designs','change_tasks','automation_tasks','assembly_tasks'):
        for r in rows(table):
            d=json.loads(r['document']);refs={d.get('catalog_context_id'),d.get('context_id')}-{None}
            (own if r['__rowid__'] in selected[table] else retain).update(refs)
    for row in c.execute("SELECT json_extract(document,'$.context_id') FROM runs"):
        if row[0]:retain.add(row[0])
    selected['contexts']={r['rowid'] for r in c.execute('SELECT rowid,id FROM contexts') if r['id'] in own-retain}
    active=[key for key in ids if key in design_map and design_map[key]['status'] in ('dispatch_ready','dispatching','awaiting_worker','delivery_unknown') and not design_map[key].get('archived')]
    return selected,{'counts':{k:len(v) for k,v in selected.items() if v},'design_ids':sorted(k for k in ids if k in design_map),
                     'removed_module_refs':[f'{i}@{v}' for i,v in sorted(module_keys)],'preserved_shared_module_refs':[f'{i}@{v}' for i,v in sorted(shared)],
                     'workflow_ids':sorted(workflow_ids),'run_ids':sorted(run_ids),'active_tasks':active}

def main():
    sys.stdout.reconfigure(encoding='utf-8');p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args()
    database=ROOT/'data/workbench.sqlite';c=sqlite3.connect('file:'+database.as_posix()+'?mode='+('rw' if args.apply else 'ro'),uri=True,timeout=30)
    selected,report=inventory(c)
    if not args.apply:print(json.dumps(report,ensure_ascii=False,indent=2));return
    if report['active_tasks']:raise RuntimeError('Refusing cleanup while LLC calls are active')
    backup=ROOT/'data/backups'/('llc-reset-'+datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,exist_ok=False)
    archive=backup/'removed-rows.sqlite';c.execute('ATTACH DATABASE ? AS archive',(str(archive),))
    c.execute('BEGIN IMMEDIATE')
    try:
        for table,rowids in selected.items():
            if not rowids:continue
            quoted='"'+table.replace('"','""')+'"';marks=','.join('?' for _ in rowids);values=sorted(rowids)
            c.execute('CREATE TABLE archive.'+quoted+' AS SELECT * FROM main.'+quoted+' WHERE rowid IN ('+marks+')',values)
            assert c.execute('SELECT count(*) FROM archive.'+quoted).fetchone()[0]==len(rowids)
            c.execute('DELETE FROM main.'+quoted+' WHERE rowid IN ('+marks+')',values)
        c.commit()
    except Exception:c.rollback();raise
    remaining,post=inventory(c)
    if post['counts']:raise RuntimeError('Cleanup left matching records: '+str(post['counts']))
    report.update(applied=True,backup=str(archive),post_cleanup_counts=post['counts'])
    (backup/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (backup/'RESTORE.txt').write_text('Rows were copied exactly before removal. Keep this SQLite archive private. Restore into a separate database for inspection first; never overwrite newly generated modules with identical ids/versions. Source database: '+str(database),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
