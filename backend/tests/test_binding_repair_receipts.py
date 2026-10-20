import copy,json
import pytest
from pe_workflow.planning_protocol import normalize_binding_ports,repair_manifest,local_patch,compile_blueprint,ProtocolError
from pe_workflow.storage import digest
from test_designs import fixture,callback,create
from test_planning_protocol import mini


def register_mini(store):
    plan,registry=mini()
    for module in registry.values():store.add_module(module)
    return plan.model_dump(),registry


def blueprint_from(plan):
    result=copy.deepcopy(plan);result['port_defs']={}
    def author(p):
        p=copy.deepcopy(p)
        p.pop('kind',None)
        if p['type']=='array':
            vt=p.pop('value_type');axes=p.pop('axes');p.update(dtype=vt['dtype'],shape=vt['shape'],axis_names=[a['name'] for a in axes])
        for key,value in p.get('fields',{}).items():p['fields'][key]=author(value)
        return p
    def refs(mapping,prefix):
        for key,p in list(mapping.items()):
            name=prefix+'_'+key;result['port_defs'][name]=author(p);mapping[key]={'ref':name}
    refs(result['external_inputs'],'external')
    for step in result['steps']:
        for side in ('inputs','outputs'):refs(step[side],step['id']+'_'+side)
    return result


def test_missing_loop_port_is_deterministic_no_retry_and_raw_is_preserved(fixture):
    c,s,store=fixture;plan,registry=register_mini(store)
    del plan['loops'][0]['rows']['port']
    raw=blueprint_from(plan);original=copy.deepcopy(raw)
    t=callback(c,s,create(c),kind='plan_blueprint',blueprint=raw)
    assert t['status']=='awaiting_confirmation',t
    assert t['calls']==1 and t['plan']['loops'][0]['rows']['port']=='rows'
    assert raw==original and t['binding_repair_audit'][0]['basis']=='explicit_selected_node_single_output'
    receipts=c.get('/api/workflow-designs/'+t['id']+'/candidate-receipts').json()['items']
    assert len(receipts)==1 and receipts[0]['processed']
    assert 'port' not in receipts[0]['candidate']['blueprint']['loops'][0]['rows']
    assert 'token' not in receipts[0]['candidate'] and 'call_id' not in receipts[0]['candidate']
    assert len(store.modules())==2  # fixture double plus explicitly registered mini, no generated tool


def test_every_binding_position_and_immutable_contract_are_used():
    base={'grids':[{'id':'grid','input_sources':{'scan':{'source':'node','node':'producer'}}}],
          'steps':[{'id':'producer','outputs':{'result':{}}},{'id':'member','outputs':{'loss':{}}},
                   {'id':'consumer','input_sources':{'x':{'source':'node','node':'producer'}}}],
          'loops':[{'id':'loop','rows':{'source':'node','node':'grid'},'captures':{'x':{'source':'node','node':'producer'}},
                    'outputs':{'out':{'source':'node','node':'member'}}}]}
    result,audit=normalize_binding_ports(base)
    assert len(audit)==5
    assert result['loops'][0]['rows']['port']=='rows' and 'port' not in base['loops'][0]['rows']
    base['steps'][0]['module_ref']='chosen@1'
    result,audit=normalize_binding_ports(base,{'chosen@1':{'outputs':{'one':{},'two':{}}}})
    assert 'port' not in result['steps'][2]['input_sources']['x']
    assert len(audit)==2


def test_ambiguous_output_asks_once_without_model_retry(fixture):
    c,s,store=fixture;plan,registry=mini()
    m=registry['copper@1'].model_copy(deep=True)
    m.outputs['extra']=copy.deepcopy(m.outputs['loss']);m.expressions['extra']=m.expressions['loss']
    store.add_module(m)
    plan.steps[0].outputs=m.outputs
    raw=blueprint_from(plan.model_dump());del raw['loops'][0]['outputs']['loss']['port']
    t=callback(c,s,create(c),kind='plan_blueprint',blueprint=raw)
    assert t['status']=='needs_clarification' and t['calls']==1
    assert t['format_repair']['allowed_paths']==['/loops/0/outputs/loss']
    assert t['format_repair']['path_modes']['/loops/0/outputs/loss']=='binding'
    assert sorted(t['format_repair']['questions'][0]['available_ports'])==['extra','loss']
    assert t['previous_candidate']['blueprint']==raw
    assert t['failure_routing']['automatic'] is False
    context=s.context(s.get(t['id']))
    assert 'base' not in context['format_repair']
    assert context['format_repair']['current_values']['/loops/0/outputs/loss']['node']=='copper'
    assert 'blueprint' not in context['previous_candidate']
    # Human chooses the already declared loss output; using remaining quota, no reset.
    t=c.post('/api/workflow-designs/'+t['id']+'/answer',json={'revision':t['revision'],'answers':{key:'选择 copper 的 loss 输出' for key in t['questions']}}).json()
    assert t['calls']==1 and t['remaining_calls']==3
    manifest=t['format_repair']
    t=callback(c,s,t,kind='plan_delta',delta={'base_hash':manifest['base_hash'],'edits':[{'path':'/loops/0/outputs/loss','value':{'source':'node','node':'copper','port':'loss'}}]})
    assert t['status']=='awaiting_confirmation' and t['calls']==2,t
    assert t['plan']['steps'][0]['formula_latex']=='copper'


def test_binding_patch_cannot_rewire_or_weaken_checks(fixture):
    c,s,store=fixture;plan,_=register_mini(store);del plan['loops'][0]['rows']['node']
    raw=blueprint_from(plan)
    t=callback(c,s,create(c),kind='plan_blueprint',blueprint=raw)
    assert t['status']=='needs_clarification'
    manifest=t['format_repair'];path='/loops/0/rows'
    for value in ({'source':'input','port':'rows'}, {'source':'node','node':'grid','port':'other'},
                  {'source':'node','node':'grid','port':'rows','value':0}, {'source':'node','node':'grid','port':'rows','field':'Vin'}):
        with pytest.raises(ValueError):local_patch(manifest,{'base_hash':manifest['base_hash'],'edits':[{'path':path,'value':value}]})
    with pytest.raises(ValueError):local_patch(manifest,{'base_hash':manifest['base_hash'],'edits':[{'path':'/steps/0/input_checks','value':[]}]})


def test_invalid_receipt_replay_is_idempotent_and_no_credentials(fixture):
    c,s,_=fixture;t=create(c)
    raw={'title':'bad','summary':'bad','steps':[]}
    t=callback(c,s,t,kind='plan_blueprint',blueprint=raw,explanation='格式待修')
    assert t['status']=='paused' and t['failure_routing']['automatic'] is False
    revision=t['revision'];saved=s.get(t['id'])
    payload={'call_id':saved['call_id'],'token':saved['token'],'kind':'plan_blueprint','blueprint':raw,'explanation':'格式待修'}
    again=c.post('/api/workflow-designs/'+t['id']+'/result',json=payload).json()
    assert again['revision']==revision
    docs=s.receipts.list(t['id'])['items'];assert len(docs)==1 and saved['token'] not in json.dumps(docs)


def test_port_and_binding_errors_are_collected_in_same_pass():
    raw={'title':'test','summary':'test','port_defs':{'scan':{'type':'flat_dict','fields':{'fs':{'type':'array','unit':'Hz'}}}},
         'external_inputs':{'scan':{'ref':'scan'}},'steps':[],
         'loops':[{'id':'sweep','title':'sweep','step_ids':['compute'],'rows':{'source':'node','node':'grid'},'outputs':{}}]}
    with pytest.raises(ProtocolError) as error:compile_blueprint(raw)
    manifest=repair_manifest({'kind':'plan_blueprint','blueprint':raw},error.value)
    assert '/loops/0/rows' in manifest['allowed_paths'] and '/port_defs/scan' in manifest['allowed_paths']


def test_configuration_review_uses_remaining_budget_and_is_idempotent(fixture):
    c,s,_=fixture;t=create(c);raw=s.get(t['id']);raw.update(status='paused',calls=2,call_limit=4)
    raw['configuration']['provider_hash']='previous-provider'
    t=s.save(raw,'context_configuration_changed')
    assert s.public(t)['configuration_review_required']
    url='/api/workflow-designs/'+t['id']+'/configuration-review'
    view=c.get(url).json();assert view['remaining']==2 and view['changes']==['provider_hash']
    request={'revision':view['revision'],'preview_hash':view['preview_hash'],'allow_unverified':True}
    result=c.post(url,json=request).json()
    assert result['status']=='dispatch_ready' and result['calls']==2 and result['call_limit']==4
    assert result['configuration_history'][-1]['configuration']['provider_hash']=='previous-provider'
    assert c.post(url,json=request).json()['revision']==result['revision']


def test_configuration_review_cannot_bypass_exhaustion_or_stop_rules(fixture):
    from pe_workflow.storage import Conflict
    c,s,_=fixture
    for calls,repeats in ((4,0),(2,2)):
        t=create(c);raw=s.get(t['id']);raw.update(status='paused',calls=calls,call_limit=4,repeat_count=repeats)
        t=s.save(raw,'context_configuration_changed');url='/api/workflow-designs/'+t['id']+'/configuration-review';v=c.get(url).json()
        with pytest.raises(Conflict):c.post(url,json={'revision':v['revision'],'preview_hash':v['preview_hash'],'allow_unverified':True})


def test_completely_missing_rows_is_question_not_inferred():
    plan,_=mini();raw=blueprint_from(plan.model_dump());del raw['loops'][0]['rows']
    with pytest.raises(ProtocolError) as error:compile_blueprint(raw)
    manifest=repair_manifest({'kind':'plan_blueprint','blueprint':raw},error.value)
    assert manifest['questions'][0]['selected_binding']=={}
    fixed=local_patch(manifest,{'base_hash':manifest['base_hash'],'edits':[{'path':'/loops/0/rows','value':{'source':'node','node':'grid','port':'rows'}}]})
    assert compile_blueprint(fixed).loops[0].rows.port=='rows'


def test_false_connection_clarification_replays_without_model_call(fixture):
    c,s,store=fixture;plan,_=register_mini(store);b=blueprint_from(plan)
    b['steps'][0]['inputs']={};b['steps'][0]['outputs']={}
    b['steps'].append({'id':'summary','title':'summary','purpose':'all points',
        'inputs':{'loss':{'from_source':True}},'outputs':{},
        'input_sources':{'loss':{'source':'node','node':'scan_loop','port':'loss'}},'dependencies':['scan_loop']})
    t=callback(c,s,create(c),kind='plan_blueprint',blueprint=b)
    assert t['status']=='awaiting_confirmation',t
    old=s.get(t['id']);old.pop('plan');old.update(status='needs_clarification',
        questions={'connection_1':'old platform question'},
        last_report={'code':'PLAN_PROTOCOL_FORMAT'},format_repair={'base':b})
    s.save(old,'test_old_false_clarification')
    preview=c.get('/api/workflow-designs/'+t['id']+'/candidate-replay').json()['items'][0]
    assert preview['recovery_available'] and preview['status']=='structure_passed'
    result=c.post('/api/workflow-designs/'+t['id']+'/recover-candidate',json={
        'revision':preview['revision'],'receipt_hash':preview['receipt_hash'],
        'preview_hash':preview['preview_hash'],'confirm':True}).json()
    assert result['status']=='awaiting_confirmation' and result['calls']==1,result
    assert not result.get('questions') and not result.get('format_repair')
    assert result['candidate_recovery_history'][-1]['previous_questions']
