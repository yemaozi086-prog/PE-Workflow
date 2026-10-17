"""Two upstream bags feed the audited single-group legacy capacitor formula."""
from functools import lru_cache
from .models import Module,Workflow,Port
from .storage import ROOT
from .catalog import compile_catalog


@lru_cache(maxsize=1)
def fixture():
    catalog=compile_catalog((ROOT/'resources/parameter_dictionary-v1.csv').read_text(encoding='utf-8-sig'))
    def field(name,unit=None,**changes):
        row=next(r for r in catalog['rows'] if r['name']==name)
        data=dict(row['suggested_declaration'])
        if unit is not None:data['unit']=unit
        data.update(changes)
        if row['issues'] or unit is not None:data['semantic_evidence']='真实电容实验明确声明：第 1 组电容，数值及单位由实验夹具给出；保留原词典含义与来源。'
        return Port.model_validate(data)
    def bag(fields,description=''):return Port(type='flat_dict',fields=fields,description=description)
    electrical=bag({'f_sw':field('fs','kHz'),'current_rms':field('i_rms_c1','mA'),
                    'voltage':Port(type='number',quantity='voltage',unit='V',chinese_name='上游额外电压'),
                    'note':Port(type='string',quantity='note',entry_type='control',chinese_name='上游说明')},'电气计算完整输出；下游只取频率和电流')
    component=bag({'C_total':field('C_total'),'tan_delta':field('tan_delta'),
                   'cost':field('cost')},'器件计算完整输出；下游不使用成本')
    shared={'contract_version':'3.0','version':1,'provenance':'双上游字典接口演示；源自旧 DBSRC 单组 calc_Cr_loss 有效输入公式，不是完整拓扑或实测器件模型'}
    upstream_a=Module(id='electrical_bag',name='电气参数字典',inputs={'request':electrical},outputs={'electrical':electrical},expressions={'electrical':'request'},**shared)
    upstream_b=Module(id='component_bag',name='器件参数字典',inputs={'request':component},outputs={'component':component},expressions={'component':'request'},**shared)
    required_a=bag({'fs':field('fs'),'i_rms_c1':field('i_rms_c1')},'只依赖开关频率和第 1 组电容 RMS 电流')
    required_b=bag({'C_total':field('C_total','F'),'tan_delta':field('tan_delta')},'只依赖电容和损耗角正切')
    loss=bag({'cap1_loss':field('cap1_loss'),'p_cap_total':field('p_cap_total')},'单组有效输入下的两项旧函数返回，单位 W')
    source=(ROOT/'examples/real-capacitor/baseline_single_group.py').read_text(encoding='utf-8')
    compute=Module(id='cr_loss_flat',name='真实电容损耗 · 双字典接口',inputs={'electrical':required_a,'component':required_b},outputs={'loss':loss},
        implementation='python_function',entrypoint='capacitor_group_loss',source=source,
        adapter={'arguments':{'fs_hz':{'from':['inputs','electrical','fs']},'i_rms_a':{'from':['inputs','electrical','i_rms_c1']},
                             'capacitance_f':{'from':['inputs','component','C_total']},'tan_delta':{'from':['inputs','component','tan_delta']}},
                 'returns':{'loss':{'from':['return']}}},
        domains={'electrical.fs':{'minimum':1},'electrical.i_rms_c1':{'minimum':0},'component.C_total':{'minimum':1e-15},'component.tan_delta':{'minimum':0}},
        capabilities=['resonant_capacitor_loss'],applies_to={'group_count':['one']},**shared)
    wf=Workflow(schema_version='3.0',id='dictionary_capacitor_demo',name='双上游字典 · 真实电容损耗',
        description='两条字典连线；按声明提取字段、改名与单位换算。旧函数单组工况的参考结果约 1.136821 W。',
        inputs={'electrical_request':electrical,'component_request':component},nodes=[
            {'id':'electrical_source','label':'电气参数字典','module_id':upstream_a.id,'module_version':1,'inputs':{'request':{'source':'input','port':'electrical_request'}}},
            {'id':'component_source','label':'器件参数字典','module_id':upstream_b.id,'module_version':1,'inputs':{'request':{'source':'input','port':'component_request'}}},
            {'id':'loss','label':'真实电容损耗','module_id':compute.id,'module_version':1,'inputs':{
                'electrical':{'source':'node','node':'electrical_source','port':'electrical','field_map':{'fs':'f_sw','i_rms_c1':'current_rms'}},
                'component':{'source':'node','node':'component_source','port':'component'}}}],
        outputs={'loss':{'source':'node','node':'loss','port':'loss'}},output_ports={'loss':loss},
        layout={'$input':{'x':0,'y':180},'electrical_source':{'x':340,'y':40},'component_source':{'x':340,'y':310},'loss':{'x':700,'y':180},'$output':{'x':1070,'y':180}})
    values={'electrical_request':{'f_sw':70,'current_rms':10000,'voltage':400,'note':'额外字段仍被上游契约校验，下游不接收'},'component_request':{'C_total':200,'tan_delta':.001,'cost':12}}
    return [upstream_a,upstream_b,compute],wf,values


def upload_candidate():
    modules,_,_=fixture();data=modules[-1].model_dump();data['version']=2
    data.update(source=(ROOT/'examples/real-capacitor/upload_equivalent.py').read_text(encoding='utf-8'),entrypoint='calculate_uploaded_loss')
    def take(bag,key,a=None,b=None):return {'from':['inputs',bag,key],**({'conversion':{'from_unit':a,'to_unit':b}} if a else {})}
    data['adapter']={'arguments':{'operating':{'assemble':{'switching':{'assemble':{'frequency_khz':take('electrical','fs','Hz','kHz')}},'current_ma':take('electrical','i_rms_c1','A','mA')}},
                                 'component':{'assemble':{'capacitance_nf':take('component','C_total','F','nF'),'dissipation_factor':take('component','tan_delta')}}},
                     'returns':{'loss':{'assemble':{k:{'from':['return',0,'capacitor','dissipation_mw'],'conversion':{'from_unit':'mW','to_unit':'W'}} for k in ('cap1_loss','p_cap_total')}}}}
    return Module.model_validate(data)
