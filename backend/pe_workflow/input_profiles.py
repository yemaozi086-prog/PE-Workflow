"""Explicit local input presets; never implicit engineering defaults."""
import copy,json,uuid
from .models import AssemblyInputProfileRequest,AssemblyInputProfileApply
from .assembly_form import input_ports
from .scenarios import scene
from .engine import fail,validate_value
from .storage import canonical,digest,now


def contract(module_map):
    ports=input_ports(module_map)
    return ports,digest({k:p.model_dump() for k,p in ports.items()})


def clean_partial(values,ports,path='/inputs'):
    if not isinstance(values,dict):fail('PROFILE_TYPE','输入配置必须为字典',path)
    result={}
    for key,value in values.items():
        if key not in ports:fail('PROFILE_FIELD','配置包含当前场景未声明的字段',path+'/'+key)
        port=ports[key]
        if value is None or (value=='' and port.type!='string'):continue
        if port.type=='flat_dict':
            bag=clean_partial(value,port.fields,path+'/'+key)
            if bag:result[key]=bag
        else:
            validate_value(value,port,path+'/'+key);result[key]=copy.deepcopy(value)
    return result


def fill_missing(current,defaults,ports,prefix='',provenance=None):
    result=copy.deepcopy(current);applied=[];kept=[]
    for key,value in defaults.items():
        port=ports[key];path=prefix+'.'+key if prefix else key
        if port.type=='flat_dict':
            existing=result.get(key)
            if existing is None or existing=='':existing={}
            if not isinstance(existing,dict):kept.append(path);continue
            bag,a,k=fill_missing(existing,value,port.fields,path)
            result[key]=bag;applied+=a;kept+=k
        elif key not in result or result[key] is None or (result[key]=='' and port.type!='string'):
            result[key]=copy.deepcopy(value);applied.append(path)
        else:kept.append(path)
    return result,applied,kept


def install(app,get_store,module_map,problem):
    def connect():
        store=get_store()
        with store.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS assembly_input_profiles(id TEXT PRIMARY KEY,document TEXT NOT NULL,created_at TEXT NOT NULL)')
        return store
    @app.get('/api/assembly/input-profiles')
    def listing():
        _,fingerprint=contract(module_map)
        with connect().connect() as con:rows=con.execute('SELECT document FROM assembly_input_profiles ORDER BY created_at DESC LIMIT 100').fetchall()
        items=[]
        for row in rows:
            p=json.loads(row[0]);items.append({k:p[k] for k in ('id','name','notes','created_at','scene_id','scene_version','scope') }|{'compatible':p['contract_hash']==fingerprint and p['scene_version']==scene().version})
        return {'items':items,'count':len(items)}
    @app.post('/api/assembly/input-profiles')
    def save(req:AssemblyInputProfileRequest):
        ports,fingerprint=contract(module_map);values=clean_partial(req.inputs,ports)
        expected={r.fact:r.expected for r in scene().rules}
        facts={k:v for k,v in req.facts.items() if v.strip()}
        if any(k not in expected or v!=expected[k] for k,v in facts.items()):fail('PROFILE_CONDITIONS','配置条件不适用于当前场景','/facts')
        if not values:fail('PROFILE_EMPTY','请先填写至少一个有效参数，空白不会保存成零','/inputs')
        p={'id':uuid.uuid4().hex,'version':1,'name':req.name,'notes':req.notes,'inputs':values,'facts':facts,'contract_hash':fingerprint,'scene_id':scene().id,'scene_version':scene().version,'scope':'local_workspace','created_at':now(),'review_status':'user_saved_not_engineering_certified'}
        with connect().connect() as con:con.execute('INSERT INTO assembly_input_profiles VALUES(?,?,?)',(p['id'],canonical(p),p['created_at']))
        return p
    @app.post('/api/assembly/input-profiles/{key}/apply')
    def apply(key:str,req:AssemblyInputProfileApply):
        with connect().connect() as con:row=con.execute('SELECT document FROM assembly_input_profiles WHERE id=?',(key,)).fetchone()
        if not row:problem('NOT_FOUND','输入配置不存在',404)
        profile=json.loads(row[0]);ports,fingerprint=contract(module_map)
        if profile['contract_hash']!=fingerprint or profile['scene_id']!=scene().id or profile['scene_version']!=scene().version:
            fail('PROFILE_STALE','模块声明或场景版本已改变，请重新核对配置，不能自动应用','/profile')
        inputs,applied,kept=fill_missing(req.inputs,profile['inputs'],ports)
        facts=copy.deepcopy(req.facts);facts_applied=[];facts_kept=[]
        for k,v in profile['facts'].items():
            if not facts.get(k,'').strip():facts[k]=v;facts_applied.append(k)
            else:facts_kept.append(k)
        provenance=copy.deepcopy(req.provenance)
        for path in applied:provenance[path]='常用配置 '+profile['name']+' · '+profile['id'][:8]+'（用户保存，适用性需核对）'
        return {'inputs':inputs,'facts':facts,'provenance':provenance,'applied':applied,'preserved':kept,'facts_applied':facts_applied,'facts_preserved':facts_kept,'profile':{'id':profile['id'],'name':profile['name'],'notes':profile['notes'],'contract_hash':fingerprint},'automatic_application':False}
