"""Human-confirmed form suggestions. No LLM call, no automatic application."""
import re
from .models import AssemblyInterpretRequest
from .scenarios import scene


ALIASES={'Vin':('输入电压','Vin'),'Vout':('输出电压','Vout'),'Pout':('输出功率','Pout')}


def suggestions(text):
    proposals=[]
    for field,names in ALIASES.items():
        target='W' if field=='Pout' else 'V'
        unit_pattern=r'([A-Za-z]+|千瓦|千伏|瓦|伏)'
        pattern=r'(?i:'+ '|'.join(re.escape(n) for n in names)+r')\s*(?:为|是|取|=|：|:)?\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*'+unit_pattern+r'?(?![A-Za-z])'
        for match in re.finditer(pattern,text):
            raw=match.group(2)
            if not raw:
                proposals.append({'path':['point',field],'evidence':match.group(),'status':'needs_unit','message':'原文没有明确单位，请在表单中声明并修正目标描述'});continue
            unit={'千瓦':'kW','瓦':'W','千伏':'kV','伏':'V'}.get(raw,raw)
            # Only these explicit aliases/units are supported; never infer engineering defaults.
            canonical_unit=unit
            if canonical_unit not in (('kW','mW','W') if target=='W' else ('kV','mV','V')):
                proposals.append({'path':['point',field],'evidence':match.group(),'status':'needs_unit','message':'原文单位不受支持或物理类别不一致，请明确单位'});continue
            scale=1000 if canonical_unit in ('kW','kV') else .001 if canonical_unit in ('mW','mV') else 1
            proposals.append({'path':['point',field],'value':float(match.group(1))*scale,'unit':target,
                              'original_unit':canonical_unit,'evidence':match.group(),'status':'suggestion'})
    grouped={field:{p.get('value') for p in proposals if p['path'][-1]==field and p['status']=='suggestion'} for field in ALIASES}
    for p in proposals:
        if len(grouped[p['path'][-1]])>1:p['status']='conflict';p['message']='同一工况字段出现多个不同值；当前只支持单工况，请明确目标'
    return proposals


def install(app,module_map):
    @app.get('/api/assembly/form')
    def form():
        registry=module_map()
        ports={**registry['dbsrc_modulation@1'].inputs,'settings':registry['dbsrc_loss_capacitor@1'].inputs['settings']}
        labels={'point':'本次工况','design':'设计参数','settings':'旧模型配置','capacitor':'电容器件信息','switch':'开关器件信息','transformer':'变压器信息','inductor':'电感信息'}
        data={k:p.model_dump() for k,p in ports.items()}
        for key,port in data.items():port['chinese_name']=labels.get(key,key)
        for key,label in [('Vin','输入电压'),('Vout','输出电压'),('Pout','输出功率')]:data['point']['fields'][key]['chinese_name']=label
        names={'modulation':'调制方式','scope':'计算范围','assessment':'本次评价','upstream_constraints':'上游约束','control':'候选遍历方式'}
        values={'EPS':'EPS','single_workpoint':'单个工况','loss_only':'只计算损耗，不判整机可行性','not_evaluated':'不评价上游器件约束','finite_batch':'保留全部候选，有限逐行计算'}
        return {'input_ports':data,'conditions':[{'key':r.fact,'label':names[r.fact],'value':r.expected,'text':values[r.expected]} for r in scene().rules]}
    @app.post('/api/assembly/interpret')
    def interpret(req:AssemblyInterpretRequest):return {'proposals':suggestions(req.instruction),'automatic_application':False,
        'scope':'只提取明确的输入电压、输出电压和输出功率；其他参数须用声明表单填写。缺少单位不推断。'}
