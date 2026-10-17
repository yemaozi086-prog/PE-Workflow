"""Project-scoped, user-approved extensions. Never mutate the standard tables."""
import copy,json,math,uuid,re
from .models import ExtensionProposal,ExtensionRef,ExtensionDecision,ExtensionEdit,Port
from .storage import canonical,digest,now,Conflict
from .engine import UNITS,fail,check_port


class Extensions:
    def __init__(self,get_store):
        self.store=get_store
        with self.store().connect() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS extension_requests(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_extensions(project_id TEXT,kind TEXT,name TEXT,version INTEGER,document TEXT NOT NULL,PRIMARY KEY(project_id,kind,name,version));''')

    def standards(self):
        result=[{'kind':'unit','name':name,'source':'standard','version':1,'unit_definition':{'dimension':v[0],'scale':v[1],'offset':v[2]}} for name,v in UNITS.items() if name]
        for catalog in self.store().catalogs():
            result.extend({'kind':'parameter','name':row['name'],'source':'standard','version':catalog['version'],'catalog_id':catalog['id'],'status':row['status'],'issues':row.get('issues',[]),'declaration':row['suggested_declaration']} for row in catalog['rows'])
        return result

    def catalog(self,project_id):
        with self.store().connect() as con:
            rows=[json.loads(r[0]) for r in con.execute('SELECT document FROM project_extensions WHERE project_id=? ORDER BY kind,name,version',(project_id,))]
        latest={}
        for row in rows:latest[(row['kind'],row['name'])]=row
        items=self.standards()+list(latest.values())
        return {'project_id':project_id,'items':items,'count':len(items),'policy':'standard immutable; approved project extensions only; no shadowing'}

    def standard_conflict(self,proposal):
        names={x['name'] for x in self.standards() if x['kind']==('unit' if proposal.kind=='unit' else 'parameter')}
        if proposal.name in names:fail('EXTENSION_STANDARD_CONFLICT','该名称已在标准表，不能覆盖：'+proposal.name,'/name')

    def missing(self,proposal,project):
        missing=[]
        if not proposal.description.strip():missing.append('含义和用途')
        if not proposal.evidence.strip():missing.append('定义或换算依据')
        if proposal.kind=='unit' and proposal.unit_definition is None:missing.append('量纲、倍率与偏移')
        if proposal.kind=='parameter' and proposal.declaration is None:missing.append('参数类型、物理含义和单位声明')
        if proposal.kind=='alias' and not proposal.alias_target:missing.append('指向的标准或项目参数名')
        return missing

    def read(self,key):
        with self.store().connect() as con:r=con.execute('SELECT document FROM extension_requests WHERE id=?',(key,)).fetchone()
        if not r:raise Conflict('扩展申请不存在')
        return json.loads(r[0])

    def save(self,request,event):
        old=request.get('revision',0);request=copy.deepcopy(request);request['revision']=old+1
        request.setdefault('events',[]).append({'event':event,'at':now(),'revision':old+1})
        with self.store().connect() as con:
            if old:
                r=con.execute('UPDATE extension_requests SET revision=?,document=? WHERE id=? AND revision=?',(old+1,canonical(request),request['id'],old))
                if not r.rowcount:raise Conflict('申请已改变，请重新核对')
            else:con.execute('INSERT INTO extension_requests VALUES(?,?,?)',(request['id'],1,canonical(request)))
        return request

    def propose(self,project_id,proposal,task_id=None):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',project_id):fail('EXTENSION_PROJECT','项目标识无效','/project_id')
        self.standard_conflict(proposal)
        if proposal.name!=proposal.name.strip():fail('EXTENSION_NAME','名称不能带首尾空白','/name')
        if proposal.kind!='unit':
            # Parameter names and aliases share a namespace.
            for row in self.catalog(project_id)['items']:
                if row['kind'] in ('parameter','alias') and row['name']==proposal.name and row['kind']!=proposal.kind:fail('EXTENSION_NAME_CONFLICT','同名参数或别名已存在','/name')
        missing=self.missing(proposal,project_id)
        return self.save({'id':uuid.uuid4().hex,'project_id':project_id,'task_id':task_id,'proposal':proposal.model_dump(),
            'status':'needs_information' if missing else 'pending_approval','missing':missing,'created_at':now()},'proposed')

    def edit(self,key,revision,proposal):
        old=self.read(key)
        if old['revision']!=revision or old['status'] not in ('needs_information','pending_approval'):raise Conflict('当前申请不能编辑')
        if proposal.kind!=old['proposal']['kind'] or proposal.name!=old['proposal']['name']:raise Conflict('名称或类型变化请新建申请')
        self.standard_conflict(proposal)
        old.setdefault('proposal_history',[]).append(old['proposal']);old['proposal']=proposal.model_dump()
        old['missing']=self.missing(proposal,old['project_id']);old['status']='needs_information' if old['missing'] else 'pending_approval'
        return self.save(old,'user_supplemented')

    def decision(self,key,revision,approve):
        request=self.read(key)
        if request['revision']!=revision or request['status'] not in ('needs_information','pending_approval'):raise Conflict('申请状态已改变')
        if not approve:
            request['status']='rejected';return self.save(request,'user_rejected')
        proposal=ExtensionProposal.model_validate(request['proposal']);self.standard_conflict(proposal)
        missing=self.missing(proposal,request['project_id'])
        if missing:fail('EXTENSION_INCOMPLETE','依据不完整，不能启用：'+'、'.join(missing),'/proposal')
        if proposal.kind=='unit':
            definition=proposal.unit_definition
            if not definition.dimension.strip() or not all(math.isfinite(v) for v in (definition.scale,definition.offset)):fail('EXTENSION_UNIT_INVALID','量纲不能为空，倍率和偏移必须有限','/unit_definition')
        elif proposal.kind=='parameter':
            p=self.resolve_port(proposal.declaration,request['project_id']);check_port(p,'/declaration')
            if not (p.quantity or p.semantic_id):fail('EXTENSION_SEMANTIC_REQUIRED','参数必须声明物理含义','/declaration')
            proposal.declaration=p
        else:
            if not any(r['kind']=='parameter' and r['name']==proposal.alias_target for r in self.catalog(request['project_id'])['items']):fail('EXTENSION_ALIAS_TARGET','别名只能指向已有标准或本项目参数，不允许别名链','/alias_target')
        with self.store().connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current=con.execute('SELECT revision FROM extension_requests WHERE id=?',(key,)).fetchone()
            if current[0]!=revision:raise Conflict('申请已改变')
            if proposal.kind!='unit':
                conflict=con.execute('SELECT 1 FROM project_extensions WHERE project_id=? AND name=? AND kind IN (\'parameter\',\'alias\') AND kind<>?',(request['project_id'],proposal.name,proposal.kind)).fetchone()
                if conflict:fail('EXTENSION_NAME_CONFLICT','同名参数或别名已存在','/name')
            version=con.execute('SELECT COALESCE(MAX(version),0)+1 FROM project_extensions WHERE project_id=? AND kind=? AND name=?',(request['project_id'],proposal.kind,proposal.name)).fetchone()[0]
            entry={**proposal.model_dump(),'project_id':request['project_id'],'version':version,'source':'project_extension','request_id':key,'approved_at':now()}
            entry['fingerprint']=digest(entry)
            con.execute('INSERT INTO project_extensions VALUES(?,?,?,?,?)',(request['project_id'],proposal.kind,proposal.name,version,canonical(entry)))
            request.update(status='approved',entry=entry,revision=revision+1)
            request.setdefault('events',[]).append({'event':'user_approved','at':now(),'revision':revision+1})
            con.execute('UPDATE extension_requests SET revision=?,document=? WHERE id=? AND revision=?',(revision+1,canonical(request),key,revision))
        return request

    def resolve_port(self,port,project):
        port=port.model_copy(deep=True)
        for ref in port.extension_refs:
            if ref.project_id!=project:fail('EXTENSION_SCOPE','扩展声明不属于当前项目','/extension_refs')
            with self.store().connect() as con:row=con.execute('SELECT document FROM project_extensions WHERE project_id=? AND kind=? AND name=? AND version=?',(project,ref.kind,ref.name,ref.version)).fetchone()
            if not row or json.loads(row[0])['fingerprint']!=ref.fingerprint:fail('EXTENSION_REFERENCE','扩展引用不存在或内容改变','/extension_refs')
        if port.unit and port.unit not in UNITS:
            refs=[r for r in port.extension_refs if r.kind=='unit' and r.name==port.unit]
            if refs:
                ref=refs[0]
                if ref.project_id!=project:fail('EXTENSION_SCOPE','自定义单位不属于当前项目','/unit')
                with self.store().connect() as con:r=con.execute('SELECT document FROM project_extensions WHERE project_id=? AND kind=? AND name=? AND version=?',(project,'unit',ref.name,ref.version)).fetchone()
                entry=json.loads(r[0]) if r else None
                if not entry or entry['fingerprint']!=ref.fingerprint:fail('EXTENSION_REFERENCE','自定义单位引用不存在或内容改变','/unit')
            else:entry=next((e for e in self.catalog(project)['items'] if e['kind']=='unit' and e['name']==port.unit),None)
            if entry:
                from .models import UnitDefinition
                expected=UnitDefinition.model_validate(entry['unit_definition'])
                if port.unit_definition is not None and port.unit_definition!=expected:fail('EXTENSION_DEFINITION_CONFLICT','模块与已批准的单位定义不一致','/unit_definition')
                port.unit_definition=expected
                if not refs:port.extension_refs.append(ExtensionRef(project_id=project,kind='unit',name=entry['name'],version=entry['version'],fingerprint=entry['fingerprint']))
            else:
                # Agent-supplied inline definitions are proposals, not approval.
                port.unit_definition=None
        port.fields={key:self.resolve_port(p,project) for key,p in port.fields.items()}
        if port.item:port.item=self.resolve_port(port.item,project)
        return port

    def resolve_ports(self,ports,project):return {k:self.resolve_port(p,project) for k,p in ports.items()}


def install(app,get_store):
    service=Extensions(get_store);app.state.extensions=service
    @app.get('/api/extension-catalog')
    def catalog(project_id:str='local'):return service.catalog(project_id)
    @app.get('/api/extension-requests/{key}')
    def read(key:str):return service.read(key)
    @app.post('/api/extension-requests')
    def propose(req:ExtensionProposal,project_id:str='local'):return service.propose(project_id,req)
    @app.post('/api/extension-requests/{key}/edit')
    def edit(key:str,req:ExtensionEdit):return service.edit(key,req.revision,req.proposal)
    @app.post('/api/extension-requests/{key}/decision')
    def decision(key:str,req:ExtensionDecision):return service.decision(key,req.revision,req.approve)
    return service
