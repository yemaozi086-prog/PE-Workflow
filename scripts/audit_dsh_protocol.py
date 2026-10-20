"""Read-only production inventory plus isolated protocol/state-machine probes. No LLM."""
import copy,json,os,sqlite3,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/protocol-audit-20261020'
OUT.mkdir(parents=True,exist_ok=True)
os.environ['PE_WORKFLOW_DB']=str(OUT/'bootstrap.sqlite')
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
from test_designs import fixture,create,callback,plan,tool
from test_planning_protocol import mini
from test_batch_text import text_scan
from pe_workflow.plan_controls import compile_plan
from pe_workflow.engine import execute,validate_workflow,interface,compile_workflow
from pe_workflow.control_contracts import describe
from pe_workflow.finite_control import control_spec
from pe_workflow.planning_protocol import blueprint_schema
from pe_workflow.design_context_pages import index_data
from pe_workflow.submission_protocol import continuation_action
from pe_workflow.storage import Conflict
from pe_workflow.models import Port,Node,Workflow,Binding
from pe_workflow.repair_routing import route_failure
from pe_workflow.engine import WorkflowError
from pe_workflow.models import Diagnostic


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    result={'production_writes':False,'model_calls':0,'specification':control_spec(),'probes':{}}
    cdb=sqlite3.connect('file:'+str(ROOT/'data/workbench.sqlite').replace('\\','/')+'?mode=ro',uri=True)
    result['inventory_status_counts']=dict(cdb.execute("select json_extract(document,'$.status'),count(*) from workflow_designs group by 1"))
    q=json.loads(cdb.execute('select document from tool_completion_queues where id=?',('74b59488f5cc49a58cd0bf516091916a',)).fetchone()[0])
    result['current_queue']={'status':q['status'],'items':[]}
    for item in q['items']:
        t=json.loads(cdb.execute('select document from workflow_designs where id=?',(item['task_id'],)).fetchone()[0])
        result['current_queue']['items'].append({'step_id':item['step_id'],'task_id':t['id'],'status':t['status'],'calls':t.get('calls',0),'limit':t.get('call_limit'), 'registered':item.get('registered',False),'last_error':(t.get('last_report') or {}).get('code')})
    cdb.close()
    p,registry=mini();w=compile_plan(p,'audit_numeric',registry=registry)
    output=execute(w,registry,{'scan':{'currents':[-2,0,2]},'device':{'R':0.5}})
    result['probes']['numeric_grid_and_batch']={'structure':validate_workflow(w,registry)['valid'],'expected':[2,0,2],'actual':output['scan_loop__loss']['P']}
    diagnostic=Diagnostic(code='UNDECLARED_TRANSFORM',message='需要显式变换声明：reshape',path='/module/transforms')
    result['probes']['known_transform_error_routing']=route_failure(WorkflowError(diagnostic),diagnostic.model_dump())
    p,registry,w=text_scan();output=execute(w,registry,{'scan':{'currents':list(range(126))}})
    result['probes']['text_126_rows']={'count':len(output['scan_loop__assessment']['reason']),'first_empty_preserved':output['scan_loop__assessment']['reason'][0]=='','last_failure_preserved':'电流超限' in output['scan_loop__assessment']['reason'][-1],'formal_contract_resolved':all(n['status']=='resolved' for n in describe(p)['nodes'])}
    batch=next(n for n in w.nodes if n.kind=='batch')
    _,batch_outputs=interface(batch,registry)
    foreach=batch.model_copy(update={'id':'foreach_audit','kind':'foreach'})
    _,foreach_outputs=interface(foreach,registry)
    cases=[{'name':'batch_scalar_text','node':batch.model_dump(),'workflow':w.model_dump(),'expected':{k:v.model_dump() for k,v in batch_outputs.items()}},
           {'name':'foreach_v3_outputs','node':foreach.model_dump(),'workflow':w.model_dump(),'expected':{k:v.model_dump() for k,v in foreach_outputs.items()}}]
    number=Port(type='number',quantity='power',unit='W')
    aggregate=Node(id='sum_rows',kind='aggregate',operation='sum',inputs={'items':Binding(source='input',port='values')})
    aw=Workflow(schema_version='3.0',id='audit_aggregate',name='aggregate parity only',inputs={'values':Port(type='collection',item=number)},nodes=[aggregate],outputs={'summary':Binding(source='node',node='sum_rows',port='summary')},output_ports={'summary':Port(type='flat_dict',fields={'value':number.model_copy(update={'characteristic':'sum'})})})
    _,interfaces,_=compile_workflow(aw,{})
    cases.append({'name':'aggregate_v3_outputs','node':aggregate.model_dump(),'workflow':aw.model_dump(),'expected':{k:v.model_dump() for k,v in interfaces[aggregate.id][1].items()}})
    (OUT/'frontend-control-cases.json').write_text(json.dumps(cases,ensure_ascii=False),encoding='utf-8')
    with tempfile.TemporaryDirectory(dir=OUT) as folder:
        c,s,store=fixture.__wrapped__(Path(folder))
        t=create(c);raw=s.get(t['id']);raw.update(status='awaiting_worker',phase='plan',calls=1,call_limit=4,session_id='audit-stopped-session',call_id='a'*32,token='isolated-audit-only',dispatched_at=time.time()-800);raw=s.save(raw,'audit_dispatched')
        with patch('pe_workflow.automation.rpc',return_value={'items':[{'sessionId':'audit-stopped-session','running':False,'projections':{'asOfSeq':5}}]}):
            progress=s.progress.refresh(raw['id'])
        after=s.get(raw['id'])
        result['probes']['dsh_stopped_without_result']={'dsh_running_reported':progress['running_reported'],'platform_status':after['status'],'platform_still_active':progress['active'],'continue_allowed':continuation_action(after)['allowed']}
        try:
            c.post('/api/workflow-designs/'+raw['id']+'/continue',json={'revision':after['revision'],'max_calls':1})
            result['probes']['dsh_stopped_without_result']['continue_rejected']=False
        except Conflict:
            result['probes']['dsh_stopped_without_result']['continue_rejected']=True
        # New independent formula fixture, with no reference answers in agent context.
        t=callback(c,s,create(c),kind='plan',plan=plan(False))
        t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'power W','evidence':'explicit user formula','examples':[]}).json()
        current=s.get(t['id']);context=s.context(current)
        result['probes']['initial_numeric_context']={'functions_only':isinstance(context['controlled_syntax']['numeric_functions'],list),'keyword_signatures_supplied':'numeric_catalog' in context or 'numeric_catalog' in context['controlled_syntax'],'post_failure_guidance_present':bool(context.get('concrete_protocol_repair'))}
        current.update(phase='plan',planning_protocol='blueprint_v2',format_repair={'base_hash':'b'*64,'allowed_paths':[],'base':{},'field':'blueprint','issues':[]})
        packet=s.context(current)
        result['probes']['delta_schema_alignment']={'required_result_kind':packet['required_result_kind'],'schema_keys':list(packet['schemas'])}
        result['probes']['blueprint_port_defs_schema']=blueprint_schema()['properties']['port_defs']
        current=s.get(t['id']);current.update(status='paused',questions={},last_report={'code':'RESULT_ENVELOPE','path':'/module','message':'module must be object'},failure_routing={'route':'needs_confirmation'},previous_candidate={'kind':'module','module':'malformed string'})
        current=s.save(current,'audit_envelope_rejected')
        result['probes']['envelope_recovery']={'continue_allowed':continuation_action(current)['allowed'],'retained_candidate_type':type(current['previous_candidate']['module']).__name__,'generic_recovery_api_present':any(r.path.endswith('/formula-recovery') for r in c.app.routes),'frontend_recovery_action_present':'/formula-recovery' in (ROOT/'frontend/src/DesignDialog.tsx').read_text(encoding='utf-8')}
    (OUT/'findings.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result['probes'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
