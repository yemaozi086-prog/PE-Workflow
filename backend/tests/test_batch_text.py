import copy,json
import pytest
from pe_workflow.models import Port,ValueType,Axis,DesignPlan,Workflow
from pe_workflow.engine import execute,validate_value,shape_dimensions,compatible,WorkflowError,validate_workflow
from pe_workflow.plan_controls import compile_plan
from pe_workflow.plan_audit import preflight
from pe_workflow.control_contracts import describe
from pe_workflow.finite_control import control_spec,grid_interface
from test_planning_protocol import builder,bag,array,scalar,inp,node
from test_api import client


def text_port():return Port(type='array',quantity='text',unit='1',value_type=ValueType(dtype='string',shape=['batch_rows']),axes=[Axis(name='batch_rows')])


def text_scan():
    raw,registry,add=builder('逐工况数值与中文失败说明')
    raw['external_inputs']={'scan':bag({'currents':array('A','current','current_count')})}
    raw['grids']=[{'id':'grid','title':'电流工况','input_sources':{'scan':inp('scan')},'axes':{'I':'scan.currents'},'axis_order':['I']}]
    add('check',{'item':bag({'I':scalar('A','current')})},{'item':inp('item')},
        {'assessment':bag({'current':scalar('A','current'),'passed':scalar('1','ratio'),'reason':Port(type='string',quantity='text',unit='1').model_dump()})},
        {'assessment':"{'current':item['I'],'passed':1 if item['I']<=10 else 0,'reason':'' if item['I']<=10 else '电流超限；请核对器件\\额定值 <检查>'}"})
    raw['loops']=[{'id':'scan_loop','title':'逐点检查','step_ids':['check'],'rows':node('grid','rows'),'captures':{},'outputs':{'assessment':node('check','assessment')}}]
    p=DesignPlan.model_validate(raw)
    return p,registry,compile_plan(p,'text_batch',registry=registry)


def test_126_rows_preserve_all_reasons_empty_strings_numeric_values_and_order():
    p,registry,w=text_scan();assert preflight(p)['valid'] and validate_workflow(w,registry)['valid']
    currents=list(range(126));result=execute(w,registry,{'scan':{'currents':currents}},[])
    rows=result['scan_loop__assessment']
    assert rows['current']==currents and len(rows['reason'])==126
    assert rows['reason'][:11]==['']*11 and all('电流超限' in r for r in rows['reason'][11:])
    assert rows['passed']==[1]*11+[0]*115
    assert json.loads(json.dumps(result,ensure_ascii=False))==result
    formal=describe(p)['nodes'][1]['outputs']['assessment']['fields']['reason']
    assert formal['value_type']=={'dtype':'string','shape':['batch_rows']} and formal['axes'][0]['name']=='batch_rows'
    assert control_spec()['version']=='finite_controls_v2'


def test_text_validation_never_coerces_or_drops_values():
    port=text_port();assert validate_value(['','007','中文','line\nnext'],port,'/reasons')==['','007','中文','line\nnext']
    for value in ([1],['ok',None],[['nested']],['x']*1001,['x'*10001]):
        with pytest.raises(WorkflowError):validate_value(value,port,'/reasons')
    with pytest.raises(WorkflowError):shape_dimensions({'reason':['a'],'number':[1,2]}, {'reason':port,'number':Port(type='array',value_type=ValueType(shape=['batch_rows']),axes=[Axis(name='batch_rows')])},'/result')
    with pytest.raises(WorkflowError):compatible(port,Port(type='array',quantity='text',unit='1',value_type=ValueType(shape=['batch_rows']),axes=[Axis(name='batch_rows')]),'/mapping')
    with pytest.raises(ValueError):Port(type='array',value_type=ValueType(dtype='string',shape=[2,2]),axes=[Axis(name='a'),Axis(name='b')])


def test_grid_rows_remain_numeric_and_text_is_output_only():
    with pytest.raises(ValueError):grid_interface({'scan':Port(type='flat_dict',fields={'text':text_port()})},{'word':'scan.text'})
    p,registry,w=text_scan();body=w.nodes[-1].body if w.nodes[-1].kind=='batch' else next(n.body for n in w.nodes if n.kind=='batch')
    body.inputs['item'].fields['I']=Port(type='string',quantity='text',unit='1')
    from pe_workflow.engine import interface
    with pytest.raises(WorkflowError):interface(next(n for n in w.nodes if n.kind=='batch'),registry)


def test_text_results_are_frozen_exported_and_replayed(client):
    from pe_workflow import api
    p,registry,w=text_scan()
    for m in registry.values():api.store.add_module(m)
    response=client.post('/api/runs',json={'workflow':w.model_dump(),'inputs':{'scan':{'currents':list(range(126))}}})
    assert response.status_code==200,response.text
    run=response.json();assert run['status']=='succeeded',run
    saved=client.get('/api/runs/'+run['id']).json()
    assert len(saved['outputs']['scan_loop__assessment']['reason'])==126
    assert saved['snapshot']['workflow']['output_ports']['scan_loop__assessment']['fields']['reason']['value_type']['dtype']=='string'
    replay=client.post('/api/runs/'+run['id']+'/replay').json()
    assert replay['status']=='succeeded' and replay['outputs']==run['outputs'] and replay['snapshot_hash']==run['snapshot_hash']


def test_invalid_text_row_fails_instead_of_silently_skipping():
    p,registry,w=text_scan();m=registry['check@1'].model_copy(deep=True)
    m.expressions['assessment']="{'current':item['I'],'passed':1,'reason':'' if item['I']<11 else 99}"
    registry={**registry,'check@1':m};trace=[]
    with pytest.raises(WorkflowError) as error:execute(w,registry,{'scan':{'currents':list(range(126))}},trace)
    assert error.value.diagnostic.code=='VALUE_TYPE'
    assert any(e.get('status')=='failed' and '/11/' in e.get('path','') for e in trace)
    from pe_workflow.numeric import array as numeric_array
    with pytest.raises(ValueError):numeric_array(['中文'],dtype='string')
