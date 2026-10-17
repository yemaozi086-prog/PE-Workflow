"""Read one project-owned, portable JSON preset. All content is data, never code."""
import json,hashlib
from pathlib import Path
from .models import AssemblyInputConfig
from .storage import ROOT,digest
from .scenarios import scene
from .engine import fail,fill_inputs

CONFIG_PATH=ROOT/'resources/input-profiles/dbsrc-eps-baseline-v1.json'

def unique_object(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError('配置字段重复：'+k)
        out[k]=v
    return out

def load_file(module_map,path=None):
    path=Path(path) if path is not None else CONFIG_PATH
    if not path.exists():return None
    raw=path.read_bytes()
    if len(raw)>2_000_000:raise ValueError('输入配置文件超过 2 MB')
    config=AssemblyInputConfig.model_validate(json.loads(raw.decode('utf-8-sig'),object_pairs_hook=unique_object))
    from .assembly_form import input_ports
    ports=input_ports(module_map);fingerprint=digest({k:p.model_dump() for k,p in ports.items()})
    expected={b.module_id:b.module_version for b in scene().bindings}
    if config.scene_id!=scene().id or config.scene_version!=scene().version or config.module_refs!=expected or config.contract_hash!=fingerprint:
        fail('PROFILE_STALE','文件配置与当前场景或模块声明不兼容，请核对后更新配置','/profile')
    catalog=ROOT/'resources/parameter_dictionary-v1.csv'
    if config.catalog_source.get('file_sha256')!=hashlib.sha256(catalog.read_bytes()).hexdigest():
        fail('PROFILE_CATALOG_CHANGED','参数命名表已改变，请重新核对文件配置','/profile/catalog_source')
    expected_facts={r.fact:r.expected for r in scene().rules}
    if config.facts!=expected_facts:fail('PROFILE_CONDITIONS','文件配置的场景条件缺失或不兼容','/profile/facts')
    fill_inputs(config.inputs,ports,'/profile/inputs')
    fields={bag+'.'+key:p for bag,port in ports.items() for key,p in port.fields.items()}
    if set(config.parameter_declarations)!=set(fields):fail('PROFILE_DECLARATIONS','文件必须对每个输入字段提供声明','/profile/parameter_declarations')
    for key,p in fields.items():
        record=config.parameter_declarations[key]
        if record.get('runtime_type')!=p.type or record.get('runtime_unit')!=p.unit:
            fail('PROFILE_UNIT','文件中的运行单位或类型与模块契约不一致，不能自动换算','/profile/parameter_declarations/'+key)
    document=config.model_dump()
    return {**document,'scope':'project_file','created_at':'','review_status':'frozen_legacy_example_needs_review','source_file':'resources/input-profiles/'+path.name,'file_sha256':hashlib.sha256(raw).hexdigest()}
