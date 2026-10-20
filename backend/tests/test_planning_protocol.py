import copy
import math
import runpy
from pathlib import Path
import pytest
from pe_workflow.models import Port,Module,DesignPlan,Binding,BindingAssemblyConfirm
from pe_workflow.planning_protocol import compile_blueprint,expand_port,ProtocolError,repair_manifest,local_patch,blueprint_schema
from pe_workflow.plan_controls import compile_plan
from pe_workflow.plan_audit import preflight
from pe_workflow.engine import execute,validate_workflow,WorkflowError
from pe_workflow.storage import digest,Conflict
from test_designs import fixture,callback,create


def scalar(unit='1',quantity='ratio'):
    return {'type':'number','unit':unit,'quantity':quantity}


def bag(fields):return Port(type='flat_dict',fields=fields).model_dump()


def array(unit,quantity,name):
    return expand_port({'type':'array','unit':unit,'quantity':quantity,'shape':[name],'axis_names':[name],'dtype':'float64'},'/array')


def builder(title):
    p={'title':title,'summary':'明确的有限子图测试','steps':[],'external_inputs':{},'grids':[],'loops':[],'limitations':['近似模型，仅用于本次验收']};registry={}
    def step(sid,ins,sources,outs,expressions,checks=None):
        m=Module(id=sid,version=1,name=sid,contract_version='3.0',inputs=ins,outputs=outs,expressions=expressions,provenance='测试专用明确公式，不注册到用户工具库')
        registry[sid+'@1']=m
        p['steps'].append({'id':sid,'title':sid,'purpose':'按明确公式计算','inputs':ins,'outputs':outs,'module_ref':sid+'@1',
            'input_sources':sources,'dependencies':sorted({b['node'] for b in sources.values() if b['source']=='node'}),'input_checks':checks or [],'formula_latex':sid})
    return p,registry,step


def inp(name):return {'source':'input','port':name}
def node(sid,port):return {'source':'node','node':sid,'port':port}


def mini():
    p,r,add=builder('电流网格铜损')
    p['external_inputs']={'scan':bag({'currents':array('A','current','current_count')}),'device':bag({'R':scalar('ohm','resistance')})}
    p['grids']=[{'id':'grid','title':'电流候选','input_sources':{'scan':inp('scan')},'axes':{'I':'scan.currents'},'axis_order':['I']}]
    add('copper',{'item':bag({'I':scalar('A','current')}),'device':p['external_inputs']['device']},
        {'item':inp('item'),'device':inp('device')},{'loss':bag({'P':scalar('W','power')})},
        {'loss':"{'P': item['I']**2*device['R']}"},['device["R"] >= 0'])
    p['loops']=[{'id':'scan_loop','title':'逐电流计算','step_ids':['copper'],'rows':node('grid','rows'),
        'captures':{'device':inp('device')},'outputs':{'loss':node('copper','loss')}}]
    return DesignPlan.model_validate(p),r


def test_generic_subgraph_and_scope_checks():
    p,r=mini();assert preflight(p)['valid']
    w=compile_plan(p,'mini',registry=r);assert validate_workflow(w,r)['valid']
    trace=[];out=execute(w,r,{'scan':{'currents':[-2,0,2]},'device':{'R':0.5}},trace)
    assert out['scan_loop__loss']['P']==[2,0,2]
    assert len([x for x in trace if x['node_id']=='copper'])==3
    assert next(n for n in w.nodes if n.id=='scan_loop').body.nodes[0].id=='copper'
    bad=p.model_copy(deep=True);bad.steps[0].input_sources['device']=Binding(source='input',port='undeclared')
    assert not preflight(bad)['valid']
    bad=p.model_copy(deep=True);bad.loops[0].step_ids=['scan_loop'];assert not preflight(bad)['valid']
    for values in ([],list(range(1001))):
        with pytest.raises(WorkflowError):execute(w,r,{'scan':{'currents':values},'device':{'R':0.5}})


def test_cartesian_order_and_control_contracts():
    p,r=mini();p.external_inputs['scan'].fields['other']=Port.model_validate(array('A','current','other_count'))
    p.grids[0].axes={'I':'scan.currents','other':'scan.other'};p.grids[0].axis_order=['I','other']
    w=compile_plan(p,'grid_test',registry=r)
    out=execute(w,r,{'scan':{'currents':[1,2],'other':[3,4]},'device':{'R':1}})
    assert out['grid__rows']=={'I':[1,1,2,2],'other':[3,4,3,4]}
    bad=p.model_copy(deep=True);bad.grids[0].axis_order=['I','I'];assert not preflight(bad)['valid']
    bad=p.model_copy(deep=True);bad.loops[0].captures['rows']=Binding(source='input',port='device');assert not preflight(bad)['valid']


def test_compact_ports_no_shape_or_semantic_guess_and_single_definition():
    good={'title':'test','summary':'test','limitations':[],
          'port_defs':{'scan':{'type':'flat_dict','fields':{'fs':{'type':'array','unit':'Hz','quantity':'frequency','dtype':'float64','shape':['frequency_count'],'axis_names':['switching_frequency']}}}},
          'external_inputs':{'scan':{'ref':'scan'}},'steps':[{'id':'one','title':'one','purpose':'test','inputs':{'scan':{'ref':'scan'}},'outputs':{'scan':{'ref':'scan'}}}]}
    plan=compile_blueprint(good)
    assert plan.external_inputs['scan']==plan.steps[0].inputs['scan']
    assert plan.external_inputs['scan'].fields['fs'].value_type.shape==['frequency_count']
    bad=copy.deepcopy(good);del bad['port_defs']['scan']['fields']['fs']['shape']
    with pytest.raises(ProtocolError) as e:compile_blueprint(bad)
    assert 'shape、axis_names、dtype' in str(e.value)
    manifest=repair_manifest({'kind':'plan_blueprint','blueprint':bad},e.value)
    patch={'base_hash':manifest['base_hash'],'edits':[{'path':'/port_defs/scan','value':good['port_defs']['scan']}]}
    fixed=local_patch(manifest,patch);assert compile_blueprint(fixed)==plan
    for field,value in [('unit','kHz'),('required',False),('default',100000),('quantity','scalar')]:
        altered=copy.deepcopy(patch);altered['edits'][0]['value']['fields']['fs'][field]=value
        with pytest.raises(ValueError):local_patch(manifest,altered)
    patch['base_hash']='stale'
    with pytest.raises(ValueError):local_patch(manifest,patch)
    assert len(str(blueprint_schema()))<len(str(DesignPlan.model_json_schema()))


def test_raw_callback_requires_local_delta_and_preserves_formula(fixture):
    c,s,_=fixture;t=create(c)
    raw={'title':'test','summary':'test','steps':[{'id':'one','title':'one','purpose':'test','inputs':{'data':{'type':'array','kind':'array','unit':'V','quantity':'voltage'}},'outputs':{'out':bag({'v':scalar('V','voltage')})},'formula_latex':'x^2'}]}
    t=callback(c,s,t,kind='plan',plan=raw)
    assert t['status']=='dispatch_ready' and t['format_repair']['allowed_paths']==['/steps/0/inputs/data']
    t=callback(c,s,t,kind='plan',plan=raw)
    assert t['last_report']['code']=='PLAN_DELTA_SCOPE'
    current=s.get(t['id']);assert current['format_repair']['base']['steps'][0]['formula_latex']=='x^2'


def test_local_delta_success_and_blueprint_callback(fixture):
    from test_designs import plan
    c,s,_=fixture;t=create(c);p=plan();p['steps'][0]['inputs']['data']['kind']='array'
    t=callback(c,s,t,kind='plan',plan=p);manifest=t['format_repair']
    fixed=copy.deepcopy(p['steps'][0]['inputs']['data']);fixed['kind']='flat_dict'
    t=callback(c,s,t,kind='plan_delta',delta={'base_hash':manifest['base_hash'],'edits':[{'path':'/steps/0/inputs/data','value':fixed}]})
    assert t['status']=='awaiting_confirmation' and 'format_repair' not in t
    p=plan();blueprint=copy.deepcopy(p);blueprint['port_defs']={}
    for step in blueprint['steps']:
        for side in ('inputs','outputs'):
            for name,port in list(step[side].items()):
                ref=side+'_'+name;blueprint['port_defs'][ref]=port;step[side][name]={'ref':ref}
    t=callback(c,s,create(c),kind='plan_blueprint',blueprint=blueprint)
    assert t['status']=='awaiting_confirmation'
    assert t['planning_protocol']=='blueprint_v2'


def test_control_assembly_and_publish_through_api(fixture):
    c,s,store=fixture;p,r=mini()
    for m in r.values():store.add_module(m)
    t=callback(c,s,create(c),kind='plan',plan=p.model_dump())
    assert t['status']=='awaiting_confirmation',t
    t=c.post('/api/workflow-designs/'+t['id']+'/confirm',json={'revision':t['revision'],'allow_unverified':True}).json()
    assert t['assembly_pending']
    view=s.binding_assembly.preview(t['id']);assert view['valid'],view
    child=s.binding_assembly.confirm(t['id'],BindingAssemblyConfirm(revision=t['revision'],preview_hash=view['preview_hash'],allow_unverified=True))
    saved=c.post('/api/workflow-designs/'+child['id']+'/publish',json={'revision':child['revision']}).json()
    assert saved['status']=='saved'
    actual=s.worker({'workflow':saved['workflow'],'modules':[m.model_dump() for m in r.values()],'inputs':{'scan':{'currents':[1,2]},'device':{'R':2}}},30)
    assert actual['status']=='succeeded' and actual['outputs']['scan_loop__loss']['P']==[2,8]


def llc():
    p,r,add=builder('LLC有限FHA扫频验收')
    design=bag({'Lr':scalar('H','inductance'),'Lm':scalar('H','inductance'),'Cr':scalar('F','capacitance'),'n':scalar()})
    losses=bag({k:scalar('ohm','resistance') for k in ('R_Lr','ESR_Cr','R_on')})
    limits=bag({'V_target':scalar('V','voltage'),'voltage_tolerance':scalar(),'Ir_max':scalar('A','current'),'Vcr_ac_peak_max':scalar('V','voltage')})
    scan=bag({'vin_values':array('V','voltage','vin_count'),'load_values':array('ohm','resistance','load_count'),'frequency_values':array('Hz','frequency','frequency_count')})
    p['external_inputs']={'design':design,'losses':losses,'limits':limits,'scan':scan}
    tank=bag({'fr':scalar('Hz','frequency'),'Ln':scalar()})
    add('tank',{'design':design},{'design':inp('design')},{'tank':tank},{'tank':"{'fr': 1/(2*pi*sqrt(design['Lr']*design['Cr'])), 'Ln': design['Lm']/design['Lr']}"},['design["Lr"] > 0','design["Lm"] > 0','design["Cr"] > 0','design["n"] > 0'])
    item=bag({'Vin':scalar('V','voltage'),'R_load':scalar('ohm','resistance'),'fs':scalar('Hz','frequency')})
    p['grids']=[{'id':'grid','title':'工况频率笛卡尔积','input_sources':{'scan':inp('scan')},'axes':{'Vin':'scan.vin_values','R_load':'scan.load_values','fs':'scan.frequency_values'},'axis_order':['Vin','R_load','fs']}]
    load=bag({'Rac':scalar('ohm','resistance'),'fn':scalar(),'Q':scalar()})
    add('load',{'item':item,'design':design,'tank':tank},{'item':inp('item'),'design':inp('design'),'tank':inp('tank')},{'load':load},
        {'load':"{'Rac':8*design['n']**2*item['R_load']/pi**2,'fn':item['fs']/tank['fr'],'Q':sqrt(design['Lr']/design['Cr'])/(8*design['n']**2*item['R_load']/pi**2)}"},['item["Vin"] > 0','item["R_load"] > 0','item["fs"] > 0'])
    angular={'type':'number','quantity':'angular_frequency','unit':'rad/s','unit_definition':{'dimension':'angular_frequency','scale':1,'offset':0}}
    z=bag({'Rp':scalar('ohm','resistance'),'Xp':scalar('ohm','resistance'),'w':angular})
    add('parallel',{'item':item,'design':design,'load':load},{'item':inp('item'),'design':inp('design'),'load':node('load','load')},{'z':z},
        {'z':"{'w':2*pi*item['fs'],'Rp':load['Rac']*(2*pi*item['fs']*design['Lm'])**2/(load['Rac']**2+(2*pi*item['fs']*design['Lm'])**2),'Xp':load['Rac']**2*(2*pi*item['fs']*design['Lm'])/(load['Rac']**2+(2*pi*item['fs']*design['Lm'])**2)}"})
    impedance=bag({k:scalar('ohm','resistance') for k in ('Xin','Zabs','Zpabs')})
    add('impedance',{'z':z,'design':design},{'z':node('parallel','z'),'design':inp('design')},{'impedance':impedance},
        {'impedance':"{'Xin':z['w']*design['Lr']-1/(z['w']*design['Cr'])+z['Xp'],'Zabs':sqrt(z['Rp']**2+(z['w']*design['Lr']-1/(z['w']*design['Cr'])+z['Xp'])**2),'Zpabs':sqrt(z['Rp']**2+z['Xp']**2)}"})
    transfer=bag({'V1_rms':scalar('V','voltage'),'gain':scalar(),'Vout':scalar('V','voltage'),'Pout':scalar('W','power')})
    add('transfer',{'item':item,'design':design,'impedance':impedance},{'item':inp('item'),'design':inp('design'),'impedance':node('impedance','impedance')},{'transfer':transfer},
        {'transfer':"{'V1_rms':sqrt(2)*item['Vin']/pi,'gain':impedance['Zpabs']/impedance['Zabs'],'Vout':impedance['Zpabs']/impedance['Zabs']*item['Vin']/(2*design['n']),'Pout':(impedance['Zpabs']/impedance['Zabs']*item['Vin']/(2*design['n']))**2/item['R_load']}"})
    currents=bag({'Ir_rms':scalar('A','current'),'Im_rms':scalar('A','current')})
    add('currents',{'transfer':transfer,'impedance':impedance,'z':z,'design':design},{'transfer':node('transfer','transfer'),'impedance':node('impedance','impedance'),'z':node('parallel','z'),'design':inp('design')},{'currents':currents},
        {'currents':"{'Ir_rms':transfer['V1_rms']/impedance['Zabs'],'Im_rms':transfer['gain']*transfer['V1_rms']/(z['w']*design['Lm'])}"})
    voltage=bag({'Vcr_ac_peak':scalar('V','voltage')})
    add('capacitor',{'currents':currents,'z':z,'design':design},{'currents':node('currents','currents'),'z':node('parallel','z'),'design':inp('design')},{'voltage':voltage},
        {'voltage':"{'Vcr_ac_peak':sqrt(2)*currents['Ir_rms']/(z['w']*design['Cr'])}"})
    loss=bag({'P':scalar('W','power')})
    for sid,key in [('copper','R_Lr'),('esr','ESR_Cr'),('bridge','R_on')]:
        add(sid,{'currents':currents,'losses':losses},{'currents':node('currents','currents'),'losses':inp('losses')},{'loss':loss},
            {'loss':"{'P':currents['Ir_rms']**2*losses['"+key+"']}"})
    add('total',{'a':loss,'b':loss,'c':loss},{'a':node('copper','loss'),'b':node('esr','loss'),'c':node('bridge','loss')},{'loss':loss},{'loss':"{'P':a['P']+b['P']+c['P']}"})
    checks=bag({'overall_pass':scalar()})
    add('checks',{'transfer':transfer,'currents':currents,'voltage':voltage,'impedance':impedance,'limits':limits},
        {'transfer':node('transfer','transfer'),'currents':node('currents','currents'),'voltage':node('capacitor','voltage'),'impedance':node('impedance','impedance'),'limits':inp('limits')},{'checks':checks},
        {'checks':"{'overall_pass':1 if abs(transfer['Vout']-limits['V_target'])/limits['V_target']<=limits['voltage_tolerance'] and currents['Ir_rms']<=limits['Ir_max'] and voltage['Vcr_ac_peak']<=limits['Vcr_ac_peak_max'] and impedance['Xin']>0 else 0}"})
    p['loops']=[{'id':'sweep','title':'有限单点FHA分析子图','step_ids':[s['id'] for s in p['steps'] if s['id']!='tank'],
        'rows':node('grid','rows'),'captures':{'design':inp('design'),'losses':inp('losses'),'limits':inp('limits'),'tank':node('tank','tank')},
        'outputs':{'transfer':node('transfer','transfer'),'currents':node('currents','currents'),'voltage':node('capacitor','voltage'),'loss':node('total','loss'),'checks':node('checks','checks')}}]
    return DesignPlan.model_validate(p),r


def test_llc_126_point_compiler_against_complex_reference():
    root=Path(__file__).resolve().parents[2]/'docs/demos/llc-frequency-sweep'
    reference=runpy.run_path(str(root/'reference.py'))
    import json
    inputs=json.loads((root/'02-运行输入-A.json').read_text(encoding='utf-8'))
    p,r=llc();assert preflight(p)['valid']
    w=compile_plan(p,'llc_acceptance',registry=r);assert validate_workflow(w,r)['valid']
    actual=execute(w,r,inputs);expected=reference['sweep'](inputs)
    assert len(actual['sweep__transfer']['gain'])==126
    for i,row in enumerate(expected['results']):
        for port,fields in [('transfer',['gain','Vout','Pout']),('currents',['Ir_rms','Im_rms']),('voltage',['Vcr_ac_peak'])]:
            address={'currents':'stress','voltage':'stress'}.get(port,port)
            for key in fields:assert actual['sweep__'+port][key][i]==pytest.approx(row[address][key],rel=1e-10,abs=1e-10)
        assert actual['sweep__loss']['P'][i]==pytest.approx(row['loss']['P_selected'])
        assert actual['sweep__checks']['overall_pass'][i]==row['checks']['overall_pass']
    tight=copy.deepcopy(inputs);tight['limits']['Ir_max']=0.1
    assert not any(execute(w,r,tight)['sweep__checks']['overall_pass'])
    invalid=copy.deepcopy(inputs);invalid['design']['Cr']=0
    with pytest.raises(WorkflowError):execute(w,r,invalid)


def test_protocol_migration_retains_budget_and_requires_explicit_resume(fixture):
    c,s,_=fixture;t=create(c);raw=s.get(t['id']);raw.pop('planning_protocol');raw.update(status='paused',calls=3,call_limit=4,repeat_count=1)
    t=s.save(raw,'legacy');old=copy.deepcopy(t)
    view=s.planning_migration.preview(t['id']);req=BindingAssemblyConfirm(revision=t['revision'],preview_hash=view['preview_hash'],allow_unverified=True)
    child=s.planning_migration.confirm(t['id'],req)
    assert child['status']=='paused' and child['calls']==3 and child['remaining_calls']==1
    assert s.get(t['id'])==old
    assert s.planning_migration.confirm(t['id'],req)['id']==child['id']
    result=c.post('/api/workflow-designs/'+child['id']+'/resume-migrated-planning',json={'revision':child['revision']}).json()
    assert result['status']=='dispatch_ready' and result['calls']==3


def test_wait_warning_is_observational_not_a_retry(fixture,monkeypatch):
    import time
    c,s,_=fixture;t=create(c);raw=s.get(t['id']);raw.update(status='awaiting_worker',dispatched_at=time.time()-700)
    t=s.save(raw,'waiting');before=copy.deepcopy(s.get(t['id']))
    s.progress.tool(t,'design_context:rules')
    result=s.progress.snapshot(t['id'])
    assert result['long_wait'] and result['warning_after_seconds']==600 and s.get(t['id'])==before
    result=c.post('/api/workflow-designs/'+t['id']+'/wait-warning',json={'revision':t['revision'],'seconds':1200})
    assert result.status_code==200 and result.json()['long_wait'] is False
    assert c.post('/api/workflow-designs/'+t['id']+'/wait-warning',json={'revision':t['revision'],'seconds':0}).status_code==422


def test_migration_refuses_active_stale_and_exhausted_resume(fixture):
    c,s,_=fixture;t=create(c);raw=s.get(t['id']);raw.pop('planning_protocol');raw.update(status='awaiting_worker',calls=4,call_limit=4)
    t=s.save(raw,'legacy_active');view=s.planning_migration.preview(t['id'])
    with pytest.raises(Conflict):s.planning_migration.confirm(t['id'],BindingAssemblyConfirm(revision=t['revision'],preview_hash=view['preview_hash'],allow_unverified=True))
    raw=s.get(t['id']);raw['status']='paused';t=s.save(raw,'legacy_stopped')
    with pytest.raises(Conflict):s.planning_migration.confirm(t['id'],BindingAssemblyConfirm(revision=t['revision'],preview_hash=view['preview_hash'],allow_unverified=True))
    view=s.planning_migration.preview(t['id']);child=s.planning_migration.confirm(t['id'],BindingAssemblyConfirm(revision=t['revision'],preview_hash=view['preview_hash'],allow_unverified=True))
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+child['id']+'/resume-migrated-planning',json={'revision':child['revision']})


def test_bad_blueprint_reports_all_missing_arrays_at_once():
    blueprint={'port_defs':{'a':{'type':'flat_dict','fields':{'x':{'type':'array','unit':'V'},'y':{'type':'array','unit':'A'}}},
                            'b':{'type':'flat_dict','fields':{'z':{'type':'array','unit':'Hz'}}}},
               'title':'bad','summary':'bad','steps':[]}
    with pytest.raises(ProtocolError) as error:compile_blueprint(blueprint)
    assert len(error.value.issues)==3
    manifest=repair_manifest({'kind':'plan_blueprint','blueprint':blueprint},error.value)
    assert manifest['allowed_paths']==['/port_defs/a','/port_defs/b']
