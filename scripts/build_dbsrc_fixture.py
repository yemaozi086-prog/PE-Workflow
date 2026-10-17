"""Build pinned declarations and the explicit EPS single-step workflow.

Development tool only: reviewed source operations produce declaration samples;
scientific correctness is checked separately against the original ENV.
"""
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from pe_workflow import dbsrc as d
from pe_workflow.models import Port,Module,Workflow,Node,Binding,ValueType,Axis
from pe_workflow.engine import interface,check_module,validate_workflow

modules=[]


def leaf(key,value,shape=None):
    arr=np.asarray(value)
    kwargs={'quantity':'legacy_value','semantic_id':'dbsrc.v1.'+key,'semantic_evidence':'Frozen WDY source; original field or explicitly declared control/axis, not inferred from user code.', 'description':key}
    local=key.split('__')[-1]
    declarations={'Vin':('voltage','V'),'Vout':('voltage','V'),'Pout':('power','W'),'I_limit':('current','A'),
                  'inductance':('inductance','H'),'capacitance':('capacitance','F'),'Cin':('capacitance','F'),'Cout':('capacitance','F'),
                  'frequency':('frequency','Hz'),'frequency_offset':('frequency','Hz'),'resonant_frequency':('frequency','Hz'),'fs':('frequency','Hz'),
                  'volume':('volume','mm^3'),'V_T':('volume','mm^3'),'V_L':('volume','mm^3'),'V_L_total':('volume','mm^3'),
                  'Ae_T':('area','m^2'),'Ae_L':('area','m^2'),'Ve_T':('volume','m^3'),'Ve_L':('volume','m^3'),
                  'Ap_target':('area_product','m^4'),'Ap_core':('area_product','m^4'),
                  'cost':('cost','元'),'cost_s':('cost','元'),'p_s_limit':('power','W'),'p_cap_limit':('power','W'),
                  'trans_loss_density':('power_density','kW/m^3'),'inductor_loss_density':('power_density','kW/m^3'),
                  'ripple_limit':('ratio','%'),'B_T':('flux_density','T'),'B_L':('flux_density','T')}
    if local in declarations:kwargs['quantity'],kwargs['unit']=declarations[local]
    elif local.startswith('i_'):kwargs.update(quantity='current',unit='A')
    elif local.startswith('v_'):kwargs.update(quantity='voltage',unit='V')
    elif local.startswith('p_') or (local.startswith('cap') and local.endswith('_loss')):kwargs.update(quantity='power',unit='W')
    elif local.startswith('wire_length'):kwargs.update(quantity='length',unit='mm')
    elif local.startswith('wire_area'):kwargs.update(quantity='area',unit='mm^2')
    elif local.startswith('inductance'):kwargs.update(quantity='inductance',unit='H')
    elif local.startswith('capacitance'):kwargs.update(quantity='capacitance',unit='F')
    if isinstance(value,str):return Port(type='string',**kwargs)
    if arr.ndim:
        dims=shape or list(arr.shape)
        axes=[Axis(name='batch_rows' if size=='batch_rows' else f'axis_{i}') for i,size in enumerate(dims)]
        return Port(type='array',value_type=ValueType(shape=dims),axes=axes,**kwargs)
    return Port(type='number',**kwargs)


def bag(value,columns=False):
    return Port(type='flat_dict',fields={k:leaf(k,v,shape=['batch_rows',*np.asarray(v).shape[1:]] if columns else None) for k,v in value.items()})


def add(op,ports,sample,columns=False):
    result=d.OPERATIONS[op](sample)
    out={k:bag(v,columns) for k,v in result.items()}
    m=Module(id='dbsrc_'+op,version=1,name='DBSRC · '+op,description='Frozen WDY EPS primitive. Invalid numerical padding is inert; valid=0 must propagate. Default 7×4 workpoints; independent fresh step.',inputs=ports,outputs=out,contract_version='3.0',implementation='trusted_builtin',builtin_id=op,builtin_hash=d.runtime_hash(),provenance='WDY original DBSRC; docs/dbsrc-reference.json records original byte hashes; platform-owned static execution only.',capabilities=['dbsrc_eps',op],applies_to={'topology':['DBSRC'],'modulation':['EPS'],'state_policy':['fresh_step']})
    modules.append(m)
    return m,result


def bind(node,port):return Binding(source='node',node=node,port=port)
def inp(port):return Binding(source='input',port=port)
def node(m,bindings,id=None):return Node(id=id or m.builtin_id,module_id=m.id,module_version=m.version,inputs=bindings)
def batch(id,body,bindings):return Node(id=id,kind='batch',body=body,inputs=bindings)


def main():
    action={'values':[-.1638,0,-.3891,-.7529]}
    settings={'Vin':390.,'I_limit':22.,'Pout':6600.,'ripple_limit':5.,'p_s_limit':80.,'p_cap_limit':50.,'trans_loss_density':1000.,'inductor_loss_density':1000.}
    ap,sp=bag(action),bag(settings)
    ma,ra=add('action',{'action':ap},{'action':action}); design=ra['design']; dp=ma.outputs['design']
    ms,rs=add('sizing',{'design':dp,'settings':sp},{'design':design,'settings':settings}); ep=ms.outputs['electrical']; electrical=rs['electrical']
    selections={}; components={}
    for kind in ('transformer','inductor','switch','capacitor','filter_input','filter_output'):
        m,r=add('select_'+kind,{'electrical':ep},{'electrical':electrical});selections[kind]=m;components[kind]=r['component']
    mw,rw=add('workpoints',{'settings':sp},{'settings':settings},columns=True)
    point={k:v[0] for k,v in rw['points'].items()}; pp=bag(point)
    shared={'design':dp,'settings':sp,**{k:selections[k].outputs['component'] for k in ('transformer','inductor','switch','capacitor')}}
    mm,rm=add('modulation',{'point':pp,'design':dp,**{k:shared[k] for k in ('transformer','inductor','switch','capacitor')}},{'point':point,'design':design,**{k:components[k] for k in ('transformer','inductor','switch','capacitor')}},columns=True)
    candidate={k:v[0] for k,v in rm['candidates'].items()}; cp=bag(candidate)
    loss_modules={}; loss_results={}; loss_nodes=[]
    valid=Port(type='flat_dict',fields={'valid':cp.fields['valid']})
    for i,kind in enumerate(('switch','transformer','inductor','capacitor')):
        ports={'candidate':cp,'settings':sp};samples={'candidate':candidate,'settings':settings}
        bindings={'candidate':inp('item'),'settings':inp('settings')}
        if kind!='capacitor':ports['component']=shared[kind];samples['component']=components[kind];bindings['component']=inp(kind)
        if i:ports['previous']=valid;samples['previous']={'valid':1.};bindings['previous']=bind('check_'+('switch','transformer','inductor')[i-1],'loss')
        m,r=add('loss_'+kind,ports,samples);loss_nodes.append(node(m,bindings))
        check_ports={'loss':m.outputs['loss'],'settings':sp};check_sample={'loss':r['loss'],'settings':settings};check_bindings={'loss':bind('loss_'+kind,'loss'),'settings':inp('settings')}
        if kind in ('transformer','inductor'):
            check_ports['component']=shared[kind];check_sample['component']=components[kind];check_bindings['component']=inp(kind)
        check_m,check_r=add('check_'+kind,check_ports,check_sample)
        loss_modules[kind]=check_m;loss_results[kind]=check_r['loss'];loss_nodes.append(node(check_m,check_bindings))
    eff_ports={'candidate':cp,'point':pp,**{k+'_loss':loss_modules[k].outputs['loss'] for k in loss_modules}}
    eff_samples={'candidate':candidate,'point':point,**{k+'_loss':{**loss_results[k],'valid':1.} for k in loss_modules}}
    me,re=add('candidate_efficiency',eff_ports,eff_samples)
    candidate_body=Workflow(schema_version='3.0',id='dbsrc_candidate_body',name='候选调制 · 损耗与约束',inputs={'item':cp,'point':pp,'settings':sp,**{k:shared[k] for k in ('transformer','inductor','switch')}},nodes=[*loss_nodes,node(me,{'candidate':inp('item'),'point':inp('point'),**{k+'_loss':bind('check_'+k,'loss') for k in loss_modules}})],outputs={'result':bind('candidate_efficiency','result')},output_ports=me.outputs)
    inner=batch('modulations',candidate_body,{'rows':bind('modulation','candidates'),'point':inp('item'),'settings':inp('settings'),**{k:inp(k) for k in ('transformer','inductor','switch')}})
    _,inner_outputs=interface(inner,{})
    mb,rb=add('best',{'evaluations':inner_outputs['result']},{'evaluations':{k:[v] for k,v in re['result'].items()}})
    point_body=Workflow(schema_version='3.0',id='dbsrc_workpoint_body',name='工况 · 移相遍历与最优可行效率',inputs={'item':pp,**shared},nodes=[node(mm,{'point':inp('item'),'design':inp('design'),**{k:inp(k) for k in ('transformer','inductor','switch','capacitor')}}),inner,node(mb,{'evaluations':bind('modulations','result')})],outputs={'result':bind('best','result')},output_ports=mb.outputs)
    outer=batch('workpoints',point_body,{'rows':bind('workpoint_grid','points'),'design':bind('action','design'),'settings':inp('settings'),**{k:bind('select_'+k,'component') for k in ('transformer','inductor','switch','capacitor')}})
    _,outer_outputs=interface(outer,{})
    metric_ports={'evaluations':outer_outputs['result'],**{k:selections[k].outputs['component'] for k in selections}}
    metric_samples={'evaluations':{k:[v]*28 for k,v in rb['result'].items()},**components}
    mt,rt=add('metrics',metric_ports,metric_samples)
    mr,rr=add('reward',mt.outputs,rt)
    wf=Workflow(schema_version='3.0',id='dbsrc_eps_single_step',name='DBSRC EPS · 独立单步奖励计算',description='Original WDY compatibility, fresh state. Expand batch bodies to replace an individual loss function. Not device-validated physics.',inputs={'action':ap,'settings':sp},nodes=[node(ma,{'action':inp('action')}),node(ms,{'design':bind('action','design'),'settings':inp('settings')}),*[node(selections[k],{'electrical':bind('sizing','electrical')}) for k in selections],node(mw,{'settings':inp('settings')},'workpoint_grid'),outer,node(mt,{'evaluations':bind('workpoints','result'),**{k:bind('select_'+k,'component') for k in selections}}),node(mr,{'metrics':bind('metrics','metrics')})],outputs={'result':bind('reward','result')},output_ports=mr.outputs)
    for i,n in enumerate(wf.nodes):wf.layout[n.id]={'x':float(360+(i//4)*420),'y':float((i%4)*360)}
    wf.layout['$input']={'x':-80.,'y':100.}
    wf.layout['$output']={'x':2100.,'y':100.}
    data=ROOT/'backend/pe_workflow/science';data.mkdir(exist_ok=True)
    (data/'dbsrc_modules.json').write_text(json.dumps([m.model_dump() for m in modules],ensure_ascii=False,indent=2),encoding='utf-8')
    document={'workflow':wf.model_dump(),'inputs':{'action':action,'settings':settings}}
    (data/'dbsrc_workflow.json').write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
    examples=ROOT/'examples/dbsrc';examples.mkdir(parents=True,exist_ok=True)
    (examples/'single-step.json').write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
    d.registry.cache_clear()
    for m in modules:check_module(m)
    result=validate_workflow(wf,{f'{m.id}@1':m for m in modules},document['inputs'])
    print(json.dumps({'modules':len(modules),'validation':result['valid'],'diagnostics':result['diagnostics'][:3]},ensure_ascii=False))
    if not result['valid']:raise SystemExit(1)


if __name__=='__main__':main()
