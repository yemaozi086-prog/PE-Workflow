"""Recoverable workflow identities and drafts, separate from immutable versions."""
import json
import uuid
from .models import DraftRequest,WorkspaceName,WorkspaceCopy,WorkspaceSnapshotCopy,Workflow
from .storage import now,canonical,digest,Conflict


class Workspace:
    def __init__(self,store):self.store=store

    def migrate(self,con):
        con.execute('''INSERT OR IGNORE INTO workflow_meta(id,name,updated_at)
            SELECT w.id,json_extract(w.document,'$.workflow.name'),w.created_at FROM workflows w
            JOIN (SELECT id,MAX(revision) r FROM workflows GROUP BY id) h ON h.id=w.id AND h.r=w.revision''')

    def list(self,archived=False):
        with self.store.connect() as con:
            self.migrate(con)
            rows=con.execute('''SELECT m.id,m.name,m.archived,m.origin,m.updated_at,
                COALESCE(h.r,0),COALESCE(d.revision,0),d.updated_at FROM workflow_meta m
                LEFT JOIN (SELECT id,MAX(revision) r FROM workflows GROUP BY id) h ON h.id=m.id
                LEFT JOIN workflow_drafts d ON d.id=m.id WHERE m.archived=? ORDER BY m.updated_at DESC''',(int(archived),)).fetchall()
        return [{'id':r[0],'name':r[1],'archived':bool(r[2]),'origin':json.loads(r[3]),'updated_at':r[4],
                 'revision':r[5],'draft_revision':r[6],'draft_updated_at':r[7]} for r in rows]

    def load(self,key):
        with self.store.connect() as con:
            self.migrate(con)
            meta=con.execute('SELECT name,archived,origin FROM workflow_meta WHERE id=?',(key,)).fetchone()
            draft=con.execute('SELECT revision,base_revision,document,updated_at FROM workflow_drafts WHERE id=?',(key,)).fetchone()
        if not meta:return None
        saved=self.store.workflow(key);head=saved['revision'] if saved else 0
        if draft:data=json.loads(draft[2]);base=draft[1]
        elif saved:data={'workflow':saved['workflow'],'inputs':saved['inputs'],'provenance':{}};base=head
        else:return None
        return {**data,'display_name':meta[0],'archived':bool(meta[1]),'origin':json.loads(meta[2]),
                'revision':head,'base_revision':base,'draft_revision':draft[0] if draft else 0,
                'draft_updated_at':draft[3] if draft else None}

    def save_draft(self,key,req):
        if key!=req.workflow.id:raise Conflict('草稿 ID 与工作流不一致')
        data={'workflow':req.workflow.model_dump(),'inputs':req.inputs,'provenance':req.provenance}
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.store.assert_workflow_active(con,key)
            row=con.execute('SELECT revision FROM workflow_drafts WHERE id=?',(key,)).fetchone()
            revision=row[0] if row else 0
            if revision!=req.expected_draft_revision:raise Conflict('其他页面已修改草稿；请比较或另存副本，不能直接覆盖')
            head=con.execute('SELECT COALESCE(MAX(revision),0) FROM workflows WHERE id=?',(key,)).fetchone()[0]
            if head!=req.base_revision:raise Conflict('已保存版本已改变；当前草稿保留，请重新加载或另存副本')
            self.store.ensure_workflow_meta(con,key,req.workflow.name)
            con.execute('INSERT INTO workflow_drafts VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,base_revision=excluded.base_revision,document=excluded.document,updated_at=excluded.updated_at',
                        (key,revision+1,head,canonical(data),now()))
            con.execute('UPDATE workflow_meta SET updated_at=? WHERE id=?',(now(),key))
        return {'status':'draft_saved','draft_revision':revision+1,'base_revision':head}

    def rename(self,key,name):
        if not name.strip():raise Conflict('名称不能为空')
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.migrate(con);self.store.assert_workflow_active(con,key)
            cursor=con.execute('UPDATE workflow_meta SET name=?,updated_at=? WHERE id=?',(name,now(),key))
            if not cursor.rowcount:raise Conflict('工作流不存在')
        return self.load(key)

    def archive(self,key,archived):
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.migrate(con)
            cursor=con.execute('UPDATE workflow_meta SET lifecycle_revision=lifecycle_revision+CASE WHEN archived!=? THEN 1 ELSE 0 END,archived=?,updated_at=? WHERE id=?',(int(archived),int(archived),now(),key))
            if not cursor.rowcount:raise Conflict('工作流不存在')
        return {'status':'archived' if archived else 'restored','id':key}

    def copy(self,key,req):
        if req.use_draft:
            source=self.load(key)
            if not source or not source['draft_revision']:raise Conflict('没有可复制的已保存草稿')
            origin={'workflow_id':key,'draft_revision':source['draft_revision'],'snapshot_hash':digest({'workflow':source['workflow'],'inputs':source['inputs']})}
        else:
            source=self.store.workflow(key,req.revision)
            if not source:raise Conflict('来源版本不存在；请先保存草稿或选择已有版本')
            origin={'workflow_id':key,'revision':source['revision']}
        wf=Workflow.model_validate(source['workflow']).model_copy(deep=True)
        wf.id='wf_'+uuid.uuid4().hex;wf.name=req.name
        provenance={k:'复制来源 '+key for k in source['inputs']}
        request=DraftRequest(workflow=wf,inputs=source['inputs'],expected_draft_revision=0,base_revision=0,provenance=provenance)
        # Draft and provenance commit together, never duplicate run history.
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO workflow_meta(id,name,archived,origin,updated_at) VALUES(?,?,0,?,?)',(wf.id,wf.name,canonical(origin),now()))
            con.execute('INSERT INTO workflow_drafts VALUES(?,1,0,?,?)',(wf.id,canonical({'workflow':wf.model_dump(),'inputs':request.inputs,'provenance':provenance}),now()))
        return self.load(wf.id)


def install(app,get_store,problem):
    def workspace():return Workspace(get_store())
    @app.get('/api/workspace/workflows')
    def listing(archived:bool=False):
        items=workspace().list(archived);return {'items':items,'count':len(items)}
    @app.get('/api/workspace/workflows/{key}')
    def load(key:str):
        result=workspace().load(key)
        if result is None:problem('NOT_FOUND','工作流不存在',404)
        return result
    @app.post('/api/workspace/workflows/{key}/draft')
    def draft(key:str,req:DraftRequest):return workspace().save_draft(key,req)
    @app.post('/api/workspace/workflows/{key}/rename')
    def rename(key:str,req:WorkspaceName):return workspace().rename(key,req.name.strip())
    @app.post('/api/workspace/workflows/{key}/copy')
    def copy(key:str,req:WorkspaceCopy):return workspace().copy(key,req)
    @app.post('/api/workspace/copy-snapshot')
    def snapshot(req:WorkspaceSnapshotCopy):
        if not req.name.strip():problem('NAME_REQUIRED','名称不能为空')
        wf=req.workflow.model_copy(deep=True);source=wf.id;wf.id='wf_'+uuid.uuid4().hex;wf.name=req.name.strip()
        inputs=req.inputs or {};origin={'workflow_id':source,'kind':'local_unsaved_snapshot','snapshot_hash':digest({'workflow':req.workflow.model_dump(),'inputs':inputs})}
        provenance={k:'用户明确复制的本机编辑快照' for k in inputs}
        with get_store().connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO workflow_meta(id,name,archived,origin,updated_at) VALUES(?,?,0,?,?)',(wf.id,wf.name,canonical(origin),now()))
            con.execute('INSERT INTO workflow_drafts VALUES(?,1,0,?,?)',(wf.id,canonical({'workflow':wf.model_dump(),'inputs':inputs,'provenance':provenance}),now()))
        return workspace().load(wf.id)
    @app.post('/api/workspace/workflows/{key}/archive')
    def archive(key:str):return workspace().archive(key,True)
    @app.post('/api/workspace/workflows/{key}/restore')
    def restore(key:str):return workspace().archive(key,False)
