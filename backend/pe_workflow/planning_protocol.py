"""Small authoring protocol -> strict persisted contracts, with hashed local repair."""
import copy
from pydantic import ValidationError
from .models import Port, DesignPlan, Diagnostic, Binding
from .storage import digest


class ProtocolError(ValueError):
    def __init__(self,issues):
        self.issues=issues
        self.diagnostic=Diagnostic(code='PLAN_PROTOCOL_FORMAT',message='规划格式需要局部修正',path='/blueprint')
        super().__init__('规划声明检查失败：'+'；'.join(x['path']+' '+x['message'] for x in issues))


def binding_locations(base):
    """Every Binding position in the finite planning grammar, never arbitrary JSON."""
    def key(value):return str(value).replace('~','~0').replace('/','~1')
    if not isinstance(base,dict):return
    for section in ('steps','grids'):
        for i,row in enumerate(base.get(section,[])):
            if not isinstance(row,dict) or not isinstance(row.get('input_sources',{}),dict):continue
            for name,b in row.get('input_sources',{}).items():yield f'/{section}/{i}/input_sources/{key(name)}',b
    for i,loop in enumerate(base.get('loops',[])):
        if not isinstance(loop,dict):continue
        yield f'/loops/{i}/rows',loop.get('rows')
        for section in ('captures','outputs'):
            for name,b in loop.get(section,{}).items():yield f'/loops/{i}/{section}/{key(name)}',b


def declared_outputs(base,node_id,registry=None):
    matches=[(section,row) for section in ('steps','grids','loops') for row in base.get(section,[]) if row.get('id')==node_id]
    if len(matches)!=1:return []
    section,row=matches[0]
    if section=='grids':return ['rows']
    if section=='steps' and row.get('module_ref'):
        m=(registry or {}).get(row['module_ref'])
        if m is not None:return sorted(m.outputs if hasattr(m,'outputs') else m['outputs'])
        # Without the selected immutable contract do not trust a partial summary.
        return []
    return sorted(row.get('outputs',{}))


def normalize_binding_ports(base,registry=None):
    """Fill only an omitted port on an already selected node with ONE exact output."""
    result=copy.deepcopy(base);audit=[]
    if not isinstance(result,dict):return result,audit
    for path,b in binding_locations(result):
        if not isinstance(b,dict) or b.get('source')!='node' or not b.get('node') or b.get('port') not in (None,''):continue
        outputs=declared_outputs(result,b['node'],registry)
        if len(outputs)==1:
            before=copy.deepcopy(b);b['port']=outputs[0]
            audit.append({'path':path,'before':before,'after':copy.deepcopy(b),'basis':'explicit_selected_node_single_output','available_ports':outputs})
    # Batch carries whole dictionaries. A selected field of a known, frozen
    # member output can be retained inside that dictionary without choosing any
    # new source, changing a formula or spending another model call.
    steps={s.get('id'):s for s in result.get('steps',[])}
    for i,loop in enumerate(result.get('loops',[])):
        for alias,b in loop.get('outputs',{}).items():
            if not isinstance(b,dict) or b.get('source')!='node' or not b.get('field') or b.get('field_map') or b.get('value') is not None:continue
            step=steps.get(b.get('node'));module=(registry or {}).get((step or {}).get('module_ref'))
            if not step or step.get('id') not in loop.get('step_ids',[]) or module is None:continue
            port=(module.outputs if hasattr(module,'outputs') else module.get('outputs',{})).get(b.get('port'))
            fields=port.fields if hasattr(port,'fields') else (port or {}).get('fields',{})
            if b['field'] not in fields:continue
            before=copy.deepcopy(b);retained=b.pop('field')
            audit.append({'path':f'/loops/{i}/outputs/'+str(alias).replace('~','~0').replace('/','~1'),
                          'before':before,'after':copy.deepcopy(b),'basis':'frozen_batch_whole_dictionary','retained_field':retained,
                          'notice':'批量输出采用完整命名字典；所选字段仍保留。来源节点、端口、别名及所有计算内容不变；待用户确认方案。'})
    return result,audit


def binding_issues(base):
    issues=[]
    for path,b in binding_locations(base):
        try:Binding.model_validate(b)
        except ValidationError as e:issues.extend({'path':path,'message':x['msg'],'binding':True} for x in e.errors())
    return issues


def expand_port(raw,path):
    if not isinstance(raw,dict):raise ProtocolError([{'path':path,'message':'声明必须为对象'}])
    value=copy.deepcopy(raw)
    if value.get('type')=='array' and any(k not in value for k in ('shape','axis_names','dtype')):
        raise ProtocolError([{'path':path,'message':'数组声明一次补齐 shape、axis_names、dtype；不猜轴或固定长度'}])
    if 'shape' in value:
        if 'value_type' in value or 'axes' in value:raise ProtocolError([{'path':path,'message':'简化与底层数组声明不能混用'}])
        shape=value.pop('shape');names=value.pop('axis_names',None);dtype=value.pop('dtype',None)
        if value.get('type')!='array' or names is None or dtype is None:
            raise ProtocolError([{'path':path,'message':'数组必须明确 type=array、shape、axis_names、dtype；不猜维数/轴/精度'}])
        value['value_type']={'dtype':dtype,'shape':shape};value['axes']=[{'name':n} for n in names]
    if 'kind' not in value:
        value['kind']=value.get('type') if value.get('type') in ('flat_dict','collection') else 'bare'
    if 'fields' in value:
        fields={};errors=[]
        for k,v in value['fields'].items():
            try:fields[k]=expand_port(v,path+'/fields/'+k)
            except ProtocolError as e:errors.extend(e.issues)
            except ValidationError as e:errors.extend({'path':path+'/fields/'+k+'/'+('/'.join(map(str,x['loc']))),'message':x['msg']} for x in e.errors())
        if errors:raise ProtocolError(errors)
        value['fields']=fields
    if 'item' in value and value['item'] is not None:value['item']=expand_port(value['item'],path+'/item')
    return Port.model_validate(value).model_dump()


def compile_blueprint(blueprint, registry=None):
    if not isinstance(blueprint,dict):raise ProtocolError([{'path':'/blueprint','message':'需要规划对象'}])
    value=copy.deepcopy(blueprint);definitions=value.pop('port_defs',{})
    errors=binding_issues(value);ports={};source_inputs=[]
    step_rows={s.get('id'):s for s in value.get('steps',[])}
    for i,loop in enumerate(value.get('loops',[])):
        for alias,b in loop.get('outputs',{}).items():
            if isinstance(b,dict) and b.get('field'):
                errors.append({'path':f'/loops/{i}/outputs/{alias}','message':'循环输出必须是成员的完整命名字典，不是 field 标量；保留已选节点、端口和别名，仅移除 field，所选字段仍包含在该字典中。','binding':True})
        scope={'item',*loop.get('captures',{})}
        for sid in loop.get('step_ids',[]):
            s=step_rows.get(sid,{})
            for name,b in s.get('input_sources',{}).items():
                if name=='item' and b.get('source')=='input' and b.get('port') not in scope:
                    idx=next(j for j,row in enumerate(value['steps']) if row.get('id')==sid)
                    errors.append({'path':f'/steps/{idx}/input_sources/{name}','message':'子图当前行的正式输入端口是 item；此输入已明确命名为 item，不能引用未声明的行别名。','binding':True})
    for key,raw in definitions.items():
        try:ports[key]=expand_port(raw,'/port_defs/'+key)
        except ProtocolError as e:errors.extend(e.issues)
        except ValidationError as e:
            errors.extend({'path':'/port_defs/'+key+'/'+('/'.join(map(str,x['loc']))),'message':x['msg']} for x in e.errors())
    def resolve(mapping,path):
        out={}
        for name,raw in mapping.items():
            if isinstance(raw,dict) and set(raw)=={'ref'}:
                if raw['ref'] in ports:
                    out[name]=copy.deepcopy(ports[raw['ref']])
                    if out[name]['type']!='flat_dict':errors.append({'path':path+'/'+name,'message':'计算接口必须是命名扁平字典，不能直接使用标量或数组端口；待补工具输入可通过 from_source 显式复制已选来源的字典字段。','code':'DICTIONARY_PORT_REQUIRED'})
                elif raw['ref'] not in definitions:errors.append({'path':path+'/'+name,'message':'未声明的引用：'+str(raw['ref'])})
            else:errors.append({'path':path+'/'+name,'message':'使用 {ref:定义名} 引用已声明接口；不重复填写底层Port'})
        return out
    value['external_inputs']=resolve(value.get('external_inputs',{}),'/external_inputs')
    for i,step in enumerate(value.get('steps',[])):
        authored=step.get('inputs',{})
        derived={name:raw for name,raw in authored.items() if isinstance(raw,dict) and raw.get('from_source') is True}
        for name,raw in derived.items():
            if set(raw)-{'from_source','fields'} or ('fields' in raw and (not isinstance(raw['fields'],list) or not raw['fields'] or len(raw['fields'])!=len(set(raw['fields'])))):
                errors.append({'path':f'/steps/{i}/inputs/{name}','message':'来源契约仅允许 from_source=true 与可选的非空、无重复字段列表 fields'})
            source_inputs.append((i,name,raw.get('fields')))
        step['inputs']=resolve({k:v for k,v in authored.items() if k not in derived},f'/steps/{i}/inputs')
        step['outputs']=resolve(step.get('outputs',{}),f'/steps/{i}/outputs')
    if errors:raise ProtocolError(errors)
    try:
        plan=DesignPlan.model_validate(value)
        # Registered members may intentionally omit interfaces. Freeze their exact
        # declarations before deriving the enclosing batch and its consumers.
        for step in plan.steps:
            selected=(registry or {}).get(step.module_ref)
            if selected is not None:
                from .models import Module
                selected=selected if hasattr(selected,'outputs') else Module.model_validate(selected)
                if not step.inputs:step.inputs=copy.deepcopy(selected.inputs)
                if not step.outputs:step.outputs=copy.deepcopy(selected.outputs)
        if source_inputs:
            from .control_contracts import describe
            from .finite_control import row_port
            controls={n['id']:n for n in describe(plan)['nodes']}
            formal={key:n for key,n in controls.items() if n['status']=='resolved'}
            steps={s.id:s for s in plan.steps};owners={sid:l for l in plan.loops for sid in l.step_ids}
            for i,name,fields in source_inputs:
                step=plan.steps[i];path=f'/steps/{i}/inputs/{name}';binding=step.input_sources.get(name);loop=owners.get(step.id)
                if step.module_ref:
                    errors.append({'path':path,'message':'已注册工具必须使用冻结接口；来源派生仅用于待补齐工具'});continue
                source=None
                if binding and binding.source=='input':
                    if loop and loop.id in formal:
                        env=formal[loop.id]['inputs'];source=row_port(Port.model_validate(env['rows'])) if binding.port=='item' else Port.model_validate(env[binding.port]) if binding.port in env and binding.port!='rows' else None
                    elif not loop:source=plan.external_inputs.get(binding.port)
                elif binding and binding.source=='node':
                    if binding.node in steps and ((loop and binding.node in loop.step_ids) or (not loop and binding.node not in owners)):source=steps[binding.node].outputs.get(binding.port)
                    elif not loop and binding.node in formal:
                        raw=formal[binding.node]['outputs'].get(binding.port)
                        if raw:source=Port.model_validate(raw)
                if source is None or source.type!='flat_dict' or binding.field or binding.source=='constant':
                    if binding and binding.source=='node' and binding.node in controls and controls[binding.node]['status']!='resolved':
                        errors.append({'code':'UPSTREAM_CONTROL_UNRESOLVED','path':path,'message':'上游循环接口尚未解析；连接来源已经声明，不需要用户重填。原因：'+controls[binding.node].get('message','未知'), 'source_binding':binding.model_dump(),'producer':binding.node})
                    else:
                        errors.append({'path':f'/steps/{i}/input_sources/{name}','message':'来源契约无法解析；请明确作用域内节点与端口，不猜测来源','binding':binding.model_dump() if binding else None})
                    continue
                mapping=binding.field_map
                chosen=fields if fields is not None else list(mapping) if mapping else list(source.fields)
                if set(mapping)-set(chosen):
                    errors.append({'path':path,'message':'字段映射超出选定字段'});continue
                result=source.model_copy(deep=True);result.default=None;result.fields={}
                for field in chosen:
                    origin=mapping.get(field,field)
                    if origin not in source.fields:
                        errors.append({'code':'SOURCE_CONTRACT_FIELD','path':path+'/fields/'+field,'message':'显式来源缺少字段：'+origin+'；可用字段（区分大小写）：'+', '.join(source.fields),'available_fields':list(source.fields),'source_binding':binding.model_dump()});continue
                    result.fields[field]=source.fields[origin].model_copy(deep=True)
                step.inputs[name]=result
        if errors:raise ProtocolError(errors)
        return plan
    except ValidationError as error:
        raise ProtocolError([{'path':'/'+'/'.join(map(str,e['loc'])),'message':e['msg']} for e in error.errors()]) from error


def repair_manifest(raw,error,registry=None):
    """Only invalid declaration locations can be patched; not formulas or topology."""
    if raw.get('kind') not in ('plan','plan_blueprint'):return None
    field='blueprint' if raw['kind']=='plan_blueprint' else 'plan'
    base=copy.deepcopy(raw.get(field))
    if not isinstance(base,dict):return None
    paths=[];modes={};questions=[];locations=dict(binding_locations(base))
    def add_binding(path):
        b=locations.get(path)
        if not isinstance(b,dict):b={}
        bits=path.split('/')
        if len(bits)==5 and bits[1]=='loops' and bits[2].isdigit() and bits[3]=='outputs' and b.get('source')=='node' and b.get('field'):
            loop=base['loops'][int(bits[2])];step=next((s for s in base.get('steps',[]) if s.get('id')==b.get('node') and s.get('id') in loop.get('step_ids',[])),None)
            output=(step or {}).get('outputs',{}).get(b.get('port'),{})
            declaration=base.get('port_defs',{}).get(output.get('ref'),{}) if isinstance(output,dict) and 'ref' in output else output
            if step and step.get('module_ref') and step['module_ref'] in (registry or {}):
                selected=registry[step['module_ref']]
                declared=(selected.outputs if hasattr(selected,'outputs') else selected.get('outputs',{})).get(b.get('port'))
                if declared is not None:declaration=declared.model_dump() if hasattr(declared,'model_dump') else declared
            if isinstance(declaration,dict) and b['field'] in declaration.get('fields',{}) and not b.get('field_map'):
                paths.append(path);modes[path]='whole_output';return
        if len(bits)==5 and bits[1]=='steps' and bits[2].isdigit() and bits[4]=='item' and b.get('source')=='input':
            step=base['steps'][int(bits[2])];loop=next((l for l in base.get('loops',[]) if step.get('id') in l.get('step_ids',[])),None)
            if loop and b.get('port') not in {'item',*loop.get('captures',{})} and not b.get('field'):
                paths.append(path);modes[path]='item_port';return
        if len(bits)==5 and bits[1]=='steps' and bits[2].isdigit() and b.get('source')=='node' and not b.get('node'):
            step=base.get('steps',[])[int(bits[2])]
            loop=next((l for l in base.get('loops',[]) if step.get('id') in l.get('step_ids',[])),None)
            scope={'item',*loop.get('captures',{})} if loop else set(base.get('external_inputs',{}))
            if b.get('port') in scope and not b.get('field') and b.get('value') is None:
                paths.append(path);modes[path]='scope_binding'
                return
        outputs=declared_outputs(base,b.get('node'),registry) if b.get('source')=='node' else []
        # A selected source with multiple outputs is a physical-choice ambiguity,
        # never a reason to let a format patch silently pick a different source.
        questions.append({'path':path,'selected_binding':copy.deepcopy(b),'available_ports':outputs,
                          'question':'请明确此连接的来源节点与输出端口；已有来源需改变时派生修订，不能根据同名字段或单位推断。'})
        paths.append(path);modes[path]='binding'
    if isinstance(error,ProtocolError):
        for issue in error.issues:
            bits=issue['path'].split('/')
            if len(bits)>2 and bits[1]=='port_defs':paths.append('/port_defs/'+bits[2])
            elif issue.get('code')=='SOURCE_CONTRACT_FIELD' and len(bits)>=5 and bits[1]=='steps' and bits[3]=='inputs':
                target='/'+('/'.join(bits[1:5]));paths.append(target);modes[target]='input_contract'
            elif issue.get('code')=='DICTIONARY_PORT_REQUIRED' and len(bits)==5 and bits[1]=='steps' and bits[2].isdigit() and bits[3]=='inputs':
                step=base.get('steps',[])[int(bits[2])];b=step.get('input_sources',{}).get(bits[4])
                if not step.get('module_ref') and isinstance(b,dict) and b.get('port') and not b.get('field'):
                    paths.append(issue['path']);modes[issue['path']]='input_contract'
            elif issue.get('binding'):add_binding(issue['path'])
            elif issue.get('code')=='PLAN_DEPENDENCY_MISMATCH' and len(bits)==4 and bits[1]=='steps' and bits[2].isdigit() and bits[3]=='dependencies':
                paths.append(issue['path']);modes[issue['path']]='dependencies'
    elif isinstance(error,ValidationError):
        for issue in error.errors():
            loc=list(issue['loc'])
            if loc and loc[0]=='plan':loc=loc[1:]
            # Authorize only a complete failed Port. Everything else stays frozen.
            if len(loc)>=2 and loc[0]=='external_inputs':paths.append('/external_inputs/'+str(loc[1]))
            elif len(loc)>=4 and loc[0]=='steps' and loc[2] in ('inputs','outputs'):
                paths.append('/'+'/'.join(map(str,loc[:4])))
            else:
                candidate='/'+('/'.join(map(str,loc)))
                candidates=[p for p in locations if candidate==p or candidate.startswith(p+'/')]
                if candidates:add_binding(max(candidates,key=len))
    if not paths:return None
    return {'base':base,'base_hash':digest(base),'field':field,'allowed_paths':sorted(set(paths)),
            'issues':getattr(error,'issues',[]) or ([{'path':'/'+('/'.join(map(str,e['loc']))),'message':e['msg']} for e in error.errors()] if hasattr(error,'errors') else []),
            'path_modes':modes,'questions':list({q['path']:q for q in questions}.values())}


def local_patch(manifest,patch):
    if not isinstance(patch,dict) or set(patch)!={'base_hash','edits'} or patch['base_hash']!=manifest['base_hash']:raise ValueError('局部修改的冻结哈希不一致')
    result=copy.deepcopy(manifest['base']);seen=set()
    for edit in patch['edits']:
        if set(edit)!={'path','value'} or edit['path'] not in manifest['allowed_paths'] or edit['path'] in seen:raise ValueError('局部修改超出诊断允许范围或重复')
        seen.add(edit['path']);bits=[s.replace('~1','/').replace('~0','~') for s in edit['path'].strip('/').split('/')];parent=result
        for bit in bits[:-1]:parent=parent[int(bit)] if isinstance(parent,list) else parent[bit]
        key=int(bits[-1]) if isinstance(parent,list) else bits[-1]
        if manifest.get('path_modes',{}).get(edit['path'])=='input_contract':
            value=edit['value']
            if not isinstance(value,dict) or value.get('from_source') is not True or set(value)-{'from_source','fields'}:raise ValueError('输入契约修复只能显式复制现有绑定的来源字典；不能改来源或任意改写字段声明')
            if 'fields' in value and (not isinstance(value['fields'],list) or not value['fields'] or any(not isinstance(f,str) for f in value['fields'])):raise ValueError('需要显式的非空字段名列表')
            parent[key]=copy.deepcopy(value);continue
        if manifest.get('path_modes',{}).get(edit['path']) in ('whole_output','item_port'):
            expected=copy.deepcopy(parent[key])
            if manifest['path_modes'][edit['path']]=='whole_output':expected.pop('field',None)
            else:expected['port']='item'
            if Binding.model_validate(edit['value'])!=Binding.model_validate(expected):raise ValueError('控制协议修复不能改变已选节点、字典、字段映射或其他来源')
            Binding.model_validate(edit['value']);parent[key]=copy.deepcopy(edit['value']);continue
        if manifest.get('path_modes',{}).get(edit['path'])=='scope_binding':
            old=parent[key];expected=copy.deepcopy(old);expected['source']='input';expected.pop('node',None)
            if Binding.model_validate(edit['value'])!=Binding.model_validate(expected):raise ValueError('作用域修复仅允许将无节点 ID 的绑定改为已明确命名的局部输入；端口与映射不变')
            Binding.model_validate(edit['value']);parent[key]=copy.deepcopy(edit['value']);continue
        if manifest.get('path_modes',{}).get(edit['path'])=='dependencies':
            expected=sorted({b['node'] for b in parent.get('input_sources',{}).values() if b.get('source')=='node'})
            if not isinstance(edit['value'],list) or sorted(edit['value'])!=expected:raise ValueError('依赖修正必须严格等于已有显式来源节点；禁止修改来源或插入新依赖')
            parent[key]=copy.deepcopy(edit['value']);continue
        if manifest.get('path_modes',{}).get(edit['path'])=='binding':
            old=parent.get(key) or {};new=edit['value']
            if not isinstance(new,dict) or any(v not in (None,'') and new.get(k,{} if k=='field_map' else None)!=v for k,v in old.items()):raise ValueError('连接格式补丁不能改变已明确的节点、端口、字段映射或来源')
            if new.get('field_map',{})!=old.get('field_map',{}):raise ValueError('连接格式补丁不能改变字段映射')
            if new.get('source')=='constant' or new.get('field') or new.get('value') is not None:raise ValueError('连接补丁不能植入常量或改成标量抽取')
            Binding.model_validate(new)
            parent[key]=copy.deepcopy(new)
            continue
        # Semantics, required flags, defaults and unit evidence cannot be weakened.
        def protected(value):
            if isinstance(value,dict):return {k:protected(v) for k,v in value.items() if k not in ('kind','type','shape','axis_names','dtype','value_type','axes')}
            return value
        if protected(parent[key])!=protected(edit['value']):raise ValueError('格式修正不能改变单位、字段、语义、必填性或默认值')
        def frozen_dimensions(old,new):
            if not isinstance(old,dict) or not isinstance(new,dict):return
            for name in ('type','shape','axis_names','dtype','axes'):
                if old.get(name) not in (None,[],{}) and old[name]!=new.get(name):raise ValueError('不能通过格式补丁改变已明确的数据类型、维数或坐标轴')
            if old.get('value_type'):
                for name in ('dtype','shape'):
                    if old['value_type'].get(name) not in (None,[],{}) and old['value_type'][name]!=(new.get('value_type') or {}).get(name):raise ValueError('不能更改明确的数组精度或形状')
            for name,field in old.get('fields',{}).items():frozen_dimensions(field,new.get('fields',{}).get(name))
        frozen_dimensions(parent[key],edit['value'])
        parent[key]=copy.deepcopy(edit['value'])
    return result


def repair_context(manifest):
    """Expose only diagnosed fragments to the worker, not two full plan copies."""
    view={k:copy.deepcopy(v) for k,v in manifest.items() if k!='base'}
    values={}
    for path in manifest['allowed_paths']:
        value=manifest['base']
        for key in path.strip('/').split('/'):
            key=key.replace('~1','/').replace('~0','~');value=value[int(key)] if isinstance(value,list) else value.get(key)
        values[path]=copy.deepcopy(value)
    view['current_values']=values
    view['path_rules']={path:'Binding has source=node but no node ID; port exactly names item or an explicit capture/external input in this scope. Change only source to input and omit node; retain port and field_map. This is a local address-kind correction, not a choice of physical producer.' for path,mode in manifest.get('path_modes',{}).items() if mode=='scope_binding'}
    view['path_rules'].update({path:('Remove field only; preserve selected member node, port and output alias. Batch transports whole dictionaries; the named field remains present, with batch_rows added.' if mode=='whole_output' else 'Change only port to item; this input is explicitly named item inside a batch and its old port is not declared in that scope.') for path,mode in manifest.get('path_modes',{}).items() if mode in ('whole_output','item_port')})
    view['path_rules'].update({path:'Replace this invalid bare scalar/array input reference with {from_source:true,fields:[explicit fields needed by the unchanged formula]}. Copy from the existing input_sources binding only. Cannot change source, formula, output, guards or authored unit definitions. Compiler checks fields and exact producer metadata.' for path,mode in manifest.get('path_modes',{}).items() if mode=='input_contract'})
    view['validation']='修改位置检查 → 受影响依赖检查 → 整体结构检查；通过不代表物理正确性已验证。'
    view['preserve']='未授权路径全部冻结；禁止改写公式、单位、保护条件、来源、验收答案。'
    view['instruction']='仅提交这些路径的完整修正值；平台保留并应用到原始候选，不能重写整份方案。'
    return view


def guidance():
    from .finite_control import control_spec
    return {'formal_controls':control_spec(),'version':'plan_blueprint_v2','kind':'plan_blueprint','scope':'one-level finite numeric-input batch with numeric and scalar-text outputs; no nested/convergence loops',
            'result_envelope':{'kind':'plan_blueprint','blueprint':{'title':'...','summary':'...','port_defs':{},'external_inputs':{},'steps':[],'limitations':[]}},
            'envelope_note':'The example shows the submission envelope, not a valid empty plan. Put the complete object in blueprint, NOT plan; do not submit kind alone.',
            'port_example':{'type':'flat_dict','fields':{'frequency_values':{'type':'array','quantity':'frequency','unit':'Hz','dtype':'float64','shape':['frequency_count'],'axis_names':['switching_frequency']}}},
            'instruction':'Put each interface once in blueprint.port_defs; external_inputs and step inputs/outputs reference it with {ref:name}. For missing tools only, step inputs may use {from_source:true,fields:[explicit target field names]} to copy exact contracts from their explicit input_sources, including field_map. Omit fields only to consume the entire dictionary. Platform derives batch arrays and axes, never scalarizes them. Registered tools must retain exact frozen interfaces. Omit kind/value_type/axes: platform constructs them. Explicit dtype, shape and axis_names required for authored arrays. Unknown semantics must be asked, never guessed.',
            'controls':{'grids':'id,title,input_sources,axes(output_field -> local_bag.field),axis_order(explicit ordered output names)',
                        'loops':'id,title,kind=batch,step_ids(unique compute members),rows(whole numeric-column dictionary),captures(shared input name -> explicit root Binding),outputs(alias -> member node Binding)',
                        'scope':'Member steps see input item and named captures only. Their dependencies refer to member steps only. Outer steps consume loop outputs, never member IDs. Grid/loop contracts and body are constructed by platform.'},
            'repair':'When required_result_kind=plan_delta, return kind=plan_delta and delta={base_hash,edits:[{path,value}]}; only allowed_paths, no whole-plan rewrite. Physical changes require confirmation.'}


def blueprint_schema():
    schema=DesignPlan.model_json_schema()
    reference={'type':'object','properties':{'ref':{'type':'string'}},'required':['ref'],'additionalProperties':False}
    port_map={'type':'object','additionalProperties':reference}
    compact=copy.deepcopy(Port.model_json_schema()['$defs']['Port'])
    compact['properties']={k:v for k,v in compact['properties'].items() if k not in ('kind','value_type','axes')}
    compact['properties'].update(dtype=copy.deepcopy(schema['$defs']['ValueType']['properties']['dtype']),shape={'type':'array','items':{'anyOf':[{'type':'integer','minimum':1,'maximum':100000},{'type':'string','pattern':'^[A-Za-z][A-Za-z0-9_]*$'}]}},axis_names={'type':'array','items':{'type':'string','minLength':1}})
    def references(v):
        if isinstance(v,dict):
            if v.get('$ref')=='#/$defs/Port':v['$ref']='#/$defs/CompactPort'
            for x in v.values():references(x)
        elif isinstance(v,list):
            for x in v:references(x)
    references(compact)
    compact['allOf']=[{'if':{'properties':{'type':{'const':'array'}},'required':['type']},'then':{'required':['dtype','shape','axis_names']}}]
    schema['$defs']['CompactPort']=compact
    schema['properties']['port_defs']={'type':'object','additionalProperties':{'$ref':'#/$defs/CompactPort'}}
    schema['properties']['external_inputs']=port_map
    # Loop output bindings transport whole named dictionaries, never a field.
    schema['$defs']['PlanLoop']['properties']['outputs']['additionalProperties']={
        'type':'object','additionalProperties':False,
        'properties':{'source':{'const':'node'},'node':{'type':'string','minLength':1},'port':{'type':'string','minLength':1},
                      'field':{'type':'null'},'value':{'type':'null'},'field_map':{'type':'object','maxProperties':0}},
        'required':['source','node','port']}

    for side in ('inputs','outputs'):schema['$defs']['DesignStep']['properties'][side]=port_map
    source_reference={'type':'object','properties':{'from_source':{'const':True},'fields':{'type':'array','minItems':1,'uniqueItems':True,'items':{'type':'string'}}},'required':['from_source'],'additionalProperties':False}
    schema['$defs']['DesignStep']['properties']['inputs']={'type':'object','additionalProperties':{'oneOf':[reference,source_reference]}}
    # Only retain definitions actually referenced after replacing verbose Port contracts.
    used=set()
    def visit(value):
        if isinstance(value,dict):
            if '$ref' in value:
                name=value['$ref'].split('/')[-1]
                if name not in used:used.add(name);visit(schema['$defs'][name])
            for k,v in value.items():
                if k!='$defs':visit(v)
        elif isinstance(value,list):
            for v in value:visit(v)
    visit(schema);schema['$defs']={k:v for k,v in schema['$defs'].items() if k in used}
    def strip_titles(value):
        if isinstance(value,dict):
            value.pop('title',None)
            for child in value.values():strip_titles(child)
        elif isinstance(value,list):
            for child in value:strip_titles(child)
    strip_titles(schema)
    return schema


def delta_schema(paths):
    return {'type':'object','additionalProperties':False,'properties':{
        'base_hash':{'type':'string','pattern':'^[0-9a-f]{64}$'},
        'edits':{'type':'array','items':{'type':'object','additionalProperties':False,
            'properties':{'path':{'type':'string',**({'enum':paths} if paths else {})},'value':{}},'required':['path','value']}}},
        'required':['base_hash','edits']}
