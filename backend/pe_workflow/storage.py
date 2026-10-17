import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(os.getenv("PE_WORKFLOW_ROOT", Path(__file__).resolve().parents[2])).resolve()


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("PE_WORKFLOW_DB", ROOT / "data" / "workbench.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS modules(id TEXT, version INTEGER, document TEXT NOT NULL, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS workflows(id TEXT, revision INTEGER, document TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(id,revision));
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, document TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS definitions(id TEXT, version INTEGER, document TEXT NOT NULL, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS change_tasks(id TEXT PRIMARY KEY, document TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalogs(id TEXT, version INTEGER, document TEXT NOT NULL, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS naming_rules(id TEXT, version INTEGER, document TEXT NOT NULL, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS module_relations(id TEXT, version INTEGER, document TEXT NOT NULL, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS workflow_meta(id TEXT PRIMARY KEY, name TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_drafts(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, base_revision INTEGER NOT NULL, document TEXT NOT NULL, updated_at TEXT NOT NULL);
            """)
            if 'lifecycle_revision' not in {r[1] for r in con.execute('PRAGMA table_info(workflow_meta)')}:
                con.execute('ALTER TABLE workflow_meta ADD COLUMN lifecycle_revision INTEGER NOT NULL DEFAULT 0')
            for run_id, document in con.execute("SELECT id,document FROM runs WHERE json_extract(document,'$.status')='running'").fetchall():
                record = json.loads(document)
                if record.get("status") == "running":
                    record.update(status="interrupted", finished_at=now(), outputs={}, diagnostics=[{"code": "INTERRUPTED", "message": "上次计算服务停止，运行未完成；可从原始快照重新回放", "path": "/", "severity": "error"}])
                    con.execute("UPDATE runs SET document=? WHERE id=?", (canonical(record), run_id))

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def seed_module(self, module):
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO modules VALUES(?,?,?)", (module.id, module.version, canonical(module.model_dump())))

    def definitions(self):
        with self.connect() as con:
            return [json.loads(r[0]) for r in con.execute("SELECT document FROM definitions ORDER BY id,version")]

    def add_definition(self, definition):
        with self.connect() as con:
            latest=con.execute("SELECT COALESCE(MAX(version),0) FROM definitions WHERE id=?",(definition.id,)).fetchone()[0]
            if definition.version!=latest+1:raise Conflict(f"参数定义需派生 v{latest+1}")
            con.execute("INSERT INTO definitions VALUES(?,?,?)",(definition.id,definition.version,canonical(definition.model_dump())))

    def put_context(self, context):
        key=digest(context)
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO contexts VALUES(?,?)",(key,canonical(context)))
        return key

    def catalogs(self):
        with self.connect() as con:return [json.loads(r[0]) for r in con.execute('SELECT document FROM catalogs ORDER BY id,version')]

    def rules(self):
        with self.connect() as con:return [json.loads(r[0]) for r in con.execute('SELECT document FROM naming_rules ORDER BY id,version')]

    def add_catalog(self,catalog,seed=False):
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT document FROM catalogs WHERE id=? AND version=?',(catalog['id'],catalog['version'])).fetchone()
            if row:
                if seed and json.loads(row[0])==catalog:return
                raise Conflict('词典版本已存在，不可覆盖')
            latest=con.execute('SELECT COALESCE(MAX(version),0) FROM catalogs WHERE id=?',(catalog['id'],)).fetchone()[0]
            if catalog['version']!=latest+1:raise Conflict(f'词典需派生 v{latest+1}')
            con.execute('INSERT INTO catalogs VALUES(?,?,?)',(catalog['id'],catalog['version'],canonical(catalog)))

    def add_rule(self,rule):
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            latest=con.execute('SELECT COALESCE(MAX(version),0) FROM naming_rules WHERE id=?',(rule.id,)).fetchone()[0]
            if rule.version!=latest+1:raise Conflict(f'规则需派生 v{latest+1}；不可覆盖')
            con.execute('INSERT INTO naming_rules VALUES(?,?,?)',(rule.id,rule.version,canonical(rule.model_dump())))

    def context(self,key):
        with self.connect() as con:
            row=con.execute("SELECT document FROM contexts WHERE id=?",(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def modules(self):
        with self.connect() as con:
            return [json.loads(row[0]) for row in con.execute("SELECT document FROM modules ORDER BY id,version")]

    def save_task(self,task):
        with self.connect() as con:
            con.execute("INSERT INTO change_tasks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document WHERE json_extract(change_tasks.document,'$.status')!='applied'",(task['id'],canonical(task),task['created_at']))

    def task(self,key):
        with self.connect() as con:
            row=con.execute('SELECT document FROM change_tasks WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def tasks(self):
        with self.connect() as con:
            return [json.loads(r[0]) for r in con.execute('SELECT document FROM change_tasks ORDER BY created_at DESC LIMIT 20')]

    def commit_change(self,module,workflow,inputs,task,run,case_runs=None):
        """Publish module, graph, successful run and task in one SQLite transaction."""
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.assert_workflow_active(con,workflow.id)
            epoch=con.execute('SELECT lifecycle_revision FROM workflow_meta WHERE id=?',(workflow.id,)).fetchone()
            if (epoch[0] if epoch else 0)!=task.get('workspace_epoch',0):raise Conflict('工作流归档/恢复状态已变化；请重新准备修改任务')
            row=con.execute('SELECT document FROM change_tasks WHERE id=?',(task['id'],)).fetchone()
            if not row or json.loads(row[0])['status']=='applied':raise Conflict('修改任务已应用或不存在')
            current=con.execute('SELECT COALESCE(MAX(revision),0) FROM workflows WHERE id=?',(workflow.id,)).fetchone()[0]
            if current!=task['expected_revision']:raise Conflict('工作流已更新，请重新创建修改任务')
            registry=[json.loads(r[0]) for r in con.execute('SELECT document FROM modules ORDER BY id,version')]
            definitions=[json.loads(r[0]) for r in con.execute('SELECT document FROM definitions ORDER BY id,version')]
            if digest(registry)!=task['registry_fingerprint'] or digest(definitions)!=task['definitions_fingerprint']:raise Conflict('模块或参数目录已改变，请重新准备修改任务')
            for table,key in (('catalogs','catalogs_fingerprint'),('naming_rules','rules_fingerprint')):
                if key in task and digest([json.loads(r[0]) for r in con.execute(f'SELECT document FROM {table} ORDER BY id,version')])!=task[key]:raise Conflict('词典或命名规则已改变，请重新准备任务')
            revision=current+1
            self.persist_module_rules(con,module)
            try:con.execute('INSERT INTO modules VALUES(?,?,?)',(module.id,module.version,canonical(module.model_dump())))
            except sqlite3.IntegrityError as exc:raise Conflict('候选模块版本已存在') from exc
            context_row=con.execute('SELECT document FROM contexts WHERE id=?',(task['context_id'],)).fetchone()
            context=json.loads(context_row[0]) if context_row else {}
            draft=con.execute('SELECT document FROM workflow_drafts WHERE id=?',(workflow.id,)).fetchone()
            if draft:
                content=json.loads(draft[0])
                expected={'workflow':context.get('workflow'),'inputs':context.get('inputs',{})}
                if digest({'workflow':content['workflow'],'inputs':content['inputs']})!=digest(expected):
                    raise Conflict('草稿已改变；不能用晚到的候选覆盖新编辑，请重新准备任务')
            base=context.get('base_module',{})
            if base.get('id')==module.id:
                relation={'parent_version':base['version'],'summary':task['instruction'],'acceptance_label':task.get('label',''),'task_id':task['id'],'created_at':now()}
                con.execute('INSERT INTO module_relations VALUES(?,?,?)',(module.id,module.version,canonical(relation)))
            con.execute('INSERT INTO workflows VALUES(?,?,?,?)',(workflow.id,revision,canonical({'workflow':workflow.model_dump(),'inputs':inputs}),now()))
            self.ensure_workflow_meta(con,workflow.id,workflow.name)
            con.execute('DELETE FROM workflow_drafts WHERE id=?',(workflow.id,))
            task.update(status='applied',revision=revision,run_id=run['id'],finished_at=now())
            con.execute('INSERT INTO runs VALUES(?,?,?)',(run['id'],canonical(run),run['created_at']))
            for case_run in case_runs or []:
                con.execute('INSERT INTO runs VALUES(?,?,?)',(case_run['id'],canonical(case_run),case_run['created_at']))
            con.execute('UPDATE change_tasks SET document=? WHERE id=?',(canonical(task),task['id']))
        return revision

    def add_module(self, module,parent_version=None,summary=''):
        with self.connect() as con:
            self.persist_module_rules(con,module)
            try:
                con.execute("INSERT INTO modules VALUES(?,?,?)", (module.id, module.version, canonical(module.model_dump())))
                if parent_version is not None:
                    if not con.execute('SELECT 1 FROM modules WHERE id=? AND version=?',(module.id,parent_version)).fetchone():raise Conflict('派生基准不存在')
                    con.execute('INSERT INTO module_relations VALUES(?,?,?)',(module.id,module.version,canonical({'parent_version':parent_version,'summary':summary,'created_at':now(),'acceptance_label':'模块锚点验证通过'})))
            except sqlite3.IntegrityError as exc:
                raise Conflict("模块版本已存在；不可覆盖，请派生新版本") from exc

    def persist_module_rules(self,con,module):
        for rule in module.naming_rules:
            row=con.execute('SELECT document FROM naming_rules WHERE id=? AND version=?',(rule.id,rule.version)).fetchone()
            document=canonical(rule.model_dump())
            if row:
                if json.loads(row[0])!=rule.model_dump():raise Conflict('命名规则版本含义冲突，不可覆盖')
                continue
            if rule.scope=='shared':raise Conflict('共享规则必须先经过单独确认登记')
            latest=con.execute('SELECT COALESCE(MAX(version),0) FROM naming_rules WHERE id=?',(rule.id,)).fetchone()[0]
            if rule.version!=latest+1:raise Conflict(f'命名规则需派生 v{latest+1}')
            con.execute('INSERT INTO naming_rules VALUES(?,?,?)',(rule.id,rule.version,document))

    def save(self, workflow, inputs, expected_revision, expected_draft_revision=None):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            self.assert_workflow_active(con,workflow.id)
            if expected_draft_revision is not None:
                draft=con.execute('SELECT revision FROM workflow_drafts WHERE id=?',(workflow.id,)).fetchone()
                if (draft[0] if draft else 0)!=expected_draft_revision:raise Conflict('草稿已改变，请核对最新编辑后再保存版本')
            current = con.execute("SELECT COALESCE(MAX(revision),0) FROM workflows WHERE id=?", (workflow.id,)).fetchone()[0]
            if current != expected_revision:
                raise Conflict(f"工作流已更新：当前 r{current}，提交基于 r{expected_revision}；请重新加载")
            rev = current + 1
            con.execute("INSERT INTO workflows VALUES(?,?,?,?)", (workflow.id, rev, canonical({"workflow": workflow.model_dump(), "inputs": inputs or {}}), now()))
            self.ensure_workflow_meta(con,workflow.id,workflow.name)
            con.execute('DELETE FROM workflow_drafts WHERE id=?',(workflow.id,))
            return rev

    def workflows(self):
        with self.connect() as con:
            rows = con.execute("SELECT w.id,w.revision,COALESCE(m.name,json_extract(w.document,'$.workflow.name')),w.created_at FROM workflows w JOIN (SELECT id,MAX(revision) r FROM workflows GROUP BY id) h ON w.id=h.id AND w.revision=h.r LEFT JOIN workflow_meta m ON m.id=w.id WHERE COALESCE(m.archived,0)=0 ORDER BY w.created_at DESC").fetchall()
            return [{"id": a, "revision": b, "name": c, "created_at": d} for a,b,c,d in rows]

    def workflow_epoch(self,key):
        with self.connect() as con:
            row=con.execute('SELECT lifecycle_revision FROM workflow_meta WHERE id=?',(key,)).fetchone()
        return row[0] if row else 0

    @staticmethod
    def assert_workflow_active(con,key):
        row=con.execute('SELECT archived FROM workflow_meta WHERE id=?',(key,)).fetchone()
        if row and row[0]:raise Conflict('工作流在回收站中；请先恢复，不能保存或应用晚到的候选')

    @staticmethod
    def ensure_workflow_meta(con,key,name):
        con.execute('INSERT OR IGNORE INTO workflow_meta(id,name,updated_at) VALUES(?,?,?)',(key,name,now()))

    def workflow(self, key, revision=None):
        with self.connect() as con:
            row = con.execute("SELECT revision,document FROM workflows WHERE id=? AND (? IS NULL OR revision=?) ORDER BY revision DESC LIMIT 1", (key, revision, revision)).fetchone()
            return {"revision": row[0], **json.loads(row[1])} if row else None

    def history(self, key):
        with self.connect() as con:
            return [{"revision": r, "created_at": t} for r,t in con.execute("SELECT revision,created_at FROM workflows WHERE id=? ORDER BY revision DESC", (key,))]

    def save_run(self, run):
        with self.connect() as con:
            con.execute("INSERT INTO runs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document", (run["id"], canonical(run), run["created_at"]))

    def runs(self):
        with self.connect() as con:
            # Project summaries inside SQLite so Python never expands every
            # historical multi-megabyte trace while opening the workbench.
            return [json.loads(row[0]) for row in con.execute("SELECT json_remove(document,'$.snapshot','$.trace') FROM runs ORDER BY created_at DESC LIMIT 30")]

    def run(self, key):
        with self.connect() as con:
            row = con.execute("SELECT document FROM runs WHERE id=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
