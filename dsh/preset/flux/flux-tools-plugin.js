// Flux（PE Workflow）DSH 工具桥接插件：把 127.0.0.1:8020 平台接口暴露为 flux_* 工具。
//
// 与旧 aeep-dsh 的 pe-tools/pe-revise 区别：
//   - 全部走本工程 bridge/flux_client.py（白名单 HTTP 客户端），不提供任意 URL / 任意代码执行；
//   - 平台拥有校验与版本治理，候选/提交的 diagnostics 原样返回给模型（含 http_status）；
//   - 不弹 DSH 审批卡片：澄清走平台 change-task 的 clarify/answer，注册/替换由平台锚点回归把关。
//
// 格式：CJS module.exports 插件对象，与 pe-agent 相同（本机 DSH 实际接口）。
module.exports = {
  name: 'flux-tools',
  inject: ['subprocess', 'tools'],
  apply(ctx) {
    const resolveRoot = () => {
      const path = require('path')
      const fs = require('fs')
      const cands = []
      const clean = (s) => String(s || '').replace(/^\uFEFF/, '').replace(/[\u200B-\u200D\uFEFF]/g, '').trim().replace(/^["']|["']$/g, '')
      if (process.env.FLUX_ROOT) cands.push(clean(process.env.FLUX_ROOT))
      try {
        const cfg = path.join(__dirname, 'flux-root.txt')
        if (fs.existsSync(cfg)) {
          const line = fs.readFileSync(cfg, 'utf8').split(/\r?\n/).map(clean).find(s => s && !s.startsWith('#'))
          if (line) cands.push(line)
        }
      } catch (e) {}
      cands.push(__dirname.replace(/\\/g, '/'))
      for (const c of cands) {
        try {
          if (c && fs.existsSync(path.join(c, 'bridge', 'flux_client.py'))) return c.replace(/\\/g, '/')
        } catch (e) {}
      }
      return __dirname.replace(/\\/g, '/')
    }
    const paths = () => {
      const root = resolveRoot()
      return { root, py: root + '/.venv/Scripts/python.exe', client: root + '/bridge/flux_client.py' }
    }
    const sub = ctx.subprocess
    if (!sub) return

    const call = async (endpoint, payload, graceMs) => {
      const P = paths()
      const b64 = Buffer.from(JSON.stringify(payload ?? {}), 'utf8').toString('base64')
      const handle = sub.spawn({
        argv: [P.py, P.client, endpoint, b64],
        cwd: P.root,
        stdio: { stdin: 'ignore', stdout: { maxBytes: 4000000 }, stderr: { maxBytes: 100000 } },
        graceMs: graceMs || 120000,
      })
      await handle.done
      const out = ((handle.collected.stdout && handle.collected.stdout.readFrom(0).text) || '').trim()
      if (!out) return { status: 'failed', error: 'empty flux bridge response' }
      // DSH tool outputs require an object, while list endpoints return JSON arrays.
      try { const value = JSON.parse(out); return Array.isArray(value) ? { items: value, count: value.length } : value !== null && typeof value === 'object' ? value : { value } } catch (e) { return { status: 'failed', error: 'bad flux bridge response', raw: out.slice(0, 400) } }
    }

    const register = (def) => {
      try {
        const dis = ctx.tools.register(def)
        if (typeof dis === 'function') ctx.effect(() => dis)
      } catch (e) { console.error('flux-tools register fail', def.name, String(e)) }
    }
    const render = (v) => [{ type: 'text', text: typeof v === 'string' ? v : JSON.stringify(v, null, 2) }]
    const obj = (d) => ({ type: 'object', additionalProperties: true, description: d })
    const arrObj = (d) => ({ type: 'array', items: { type: 'object', additionalProperties: true }, description: d })

    // ── 只读：健康 / 协议 ────────────────────────────────────────────────
    register({
      name: 'flux_health',
      description: '平台健康检查（127.0.0.1:8020）。返回 status/version/harness；harness 为 not_connected 表示平台未内置 LLM，只接受工具桥接调用。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/health'),
    })
    register({
      name: 'flux_dictionary_contract',
      description: '读取扁平字典协议 4.0（module_contract_version=3.0）：ports/projection/registration/extension/arrays/configuration 规则、module/catalog/naming-rule/binding schema 与可用接口。**任何新模块注册任务先读它**，再走 flux_prepare_registration 的冻结上下文。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/harness/dictionary-contract'),
    })
    register({
      name: 'flux_capabilities',
      description: '平台自身声明的 Harness 工具定义与请求 Schema（workflow_validate/execute、module_revise、workflow_optimize）。用于了解平台原生接口边界。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/harness/capabilities'),
    })

    // ── 只读：模块 / 词典 / 定义 / 规则 ─────────────────────────────────
    register({
      name: 'flux_list_modules',
      description: '列出已登记计算模块及不可变版本、输入输出与实现。可选 q 按关键词过滤（走 /api/modules/search）。新 v3 模块 contract_version=3.0、输入输出为独立命名的扁平字典；旧 1.0/2.0 模块继续保留。',
      parameters: {
        type: 'object',
        properties: { q: { type: 'string', description: '关键词过滤（可选；空则返回全部模块）' } },
        required: [],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => (args.q ? call('/api/modules/search', { q: args.q }) : call('/api/modules')),
    })
    register({
      name: 'flux_inspect_module',
      description: '读取某个模块的特定版本全文（端口契约 + 实现 + adapter + 词典引用）及其指纹。改模型前先读原实现与契约；不要只凭名字猜语义。',
      parameters: {
        type: 'object',
        properties: {
          module_id: { type: 'string', description: '模块 id，如 cr_loss_flat / switch_loss' },
          version: { type: 'number', description: '模块版本号，如 1' },
        },
        required: ['module_id', 'version'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/modules/' + args.module_id + '/versions/' + args.version),
    })
    register({
      name: 'flux_list_catalogs',
      description: '列出参数词典（parameter catalog）的版本摘要（id/version/row_count/counts/policy/source_sha256，不含行）。当前词典为 team_parameters v1（310 行）。要读具体词条用 flux_read_catalog。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/parameter-catalogs'),
    })
    register({
      name: 'flux_read_catalog',
      description: '读取某词典版本的词条行（含原始行、问题清单、建议声明、状态）。可选 q 按关键词筛选（如 "i_rms"）。词典引用为 catalog_ref{id,version,name}；模块冻结完整字段声明。',
      parameters: {
        type: 'object',
        properties: {
          catalog_id: { type: 'string', description: '词典 id，如 team_parameters' },
          version: { type: 'number', description: '词典版本，如 1' },
          q: { type: 'string', description: '关键词筛选（可选）' },
        },
        required: ['catalog_id', 'version'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/parameter-catalogs/' + args.catalog_id + '/versions/' + args.version, { q: args.q || '' }),
    })
    register({
      name: 'flux_list_definitions',
      description: '列出已登记的参数定义（parameter-definitions，含冻结端口声明）。当前可能为空。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/parameter-definitions'),
    })
    register({
      name: 'flux_list_naming_rules',
      description: '读取命名规则（naming_rules）。可选 scope(module/project/shared) 与 scope_id 过滤。规则是可验证声明（固定名 + {index} 模板 + 正反例），帮助注册适配，运行时不会静默改名。',
      parameters: {
        type: 'object',
        properties: {
          scope: { type: 'string', description: '可选：module/project/shared' },
          scope_id: { type: 'string', description: '可选：规则作用域 id' },
        },
        required: [],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/naming-rules', { scope: args.scope || '', scope_id: args.scope_id || '' }),
    })
    register({
      name: 'flux_numeric_capabilities',
      description: '读取数值能力目录（已登记单位、量纲、换算），用于 adapter 的显式单位换算声明。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/numeric/capabilities'),
    })
    register({
      name: 'flux_analyze_source',
      description: '静态分析一段受控 Python 源码（只读，不落盘）：返回入口、读/写键、实现形态与语法边界。生成候选前可先分析；真正的执行边界由平台受控解释器决定，别把“可解析”当成“物理模型正确”。',
      parameters: {
        type: 'object',
        properties: {
          source: { type: 'string', description: '纯 Python 单函数源码' },
          entrypoint: { type: 'string', description: '入口函数名（可选，默认取第一个函数）' },
        },
        required: ['source'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/modules/analyze-source', { source: args.source, entrypoint: args.entrypoint || null }),
    })

    // ── 只读：工作流 / 示例 / 运行结果 ─────────────────────────────────
    register({
      name: 'flux_list_workflows',
      description: '列出已保存的工作流（id/revision/name/created_at）。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/workflows'),
    })
    register({
      name: 'flux_read_workflow',
      description: '读取某个工作流（默认最新 revision，可指定 revision）及其运行输入。修改任务前用它拿 expected_revision（乐观锁）。',
      parameters: {
        type: 'object',
        properties: {
          workflow_id: { type: 'string', description: '工作流 id' },
          revision: { type: 'number', description: '指定 revision（可选，默认最新）' },
        },
        required: ['workflow_id'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/workflows/' + args.workflow_id, { revision: args.revision ?? null }),
    })
    register({
      name: 'flux_workflow_history',
      description: '列出工作流的 revision 历史（revision/created_at），用于回看与回放。',
      parameters: {
        type: 'object',
        properties: { workflow_id: { type: 'string', description: '工作流 id' } },
        required: ['workflow_id'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/workflows/' + args.workflow_id + '/history'),
    })
    register({
      name: 'flux_example',
      description: '读取平台内置示例（工作流 + 输入 + revision）。name=thermal（结温开关损耗）、waveform（波形积分）、dictionaries（双字典真实电容 v3，schema_version 3.0）。',
      parameters: {
        type: 'object',
        properties: { name: { type: 'string', description: 'thermal / waveform / dictionaries' } },
        required: ['name'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => {
        const n = String(args.name || '')
        if (['thermal', 'waveform', 'dictionaries'].indexOf(n) < 0) return { status: 'error', error: 'name 必须是 thermal / waveform / dictionaries' }
        return call('/api/examples/' + n)
      },
    })
    register({
      name: 'flux_list_runs',
      description: '列出运行记录摘要（id/status/output_ports/created_at/finished_at，不含快照与 trace）。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/runs'),
    })
    register({
      name: 'flux_read_run',
      description: '读取某次完整运行：outputs、trace（逐节点实际输入/输出/field_mappings/状态）、diagnostics、快照指纹与超时。用于查看运行结果、定位节点错误与回放。',
      parameters: {
        type: 'object',
        properties: { run_id: { type: 'string', description: '运行记录 id（来自 flux_list_runs 或 flux_run）' } },
        required: ['run_id'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/runs/' + args.run_id),
    })
    register({
      name: 'flux_read_artifact',
      description: '读取任务上下文或注册验证证据（artifact/context id，来自 prepare/submit 返回的 context_id 或 evidence_id）。',
      parameters: {
        type: 'object',
        properties: { artifact_id: { type: 'string', description: 'artifact/context id' } },
        required: ['artifact_id'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/harness/artifacts/' + args.artifact_id),
    })

    // ── 试读 / 替换计划 ─────────────────────────────────────────────────
    register({
      name: 'flux_validate',
      description: '静态试读（POST /api/validate）：校验格式、模块版本、循环依赖、必需输入、字段、类型、物理语义与单位，返回 valid/order/diagnostics/connection_plans。不运行计算、不落盘。提交候选前后都要用它确认图可读。',
      parameters: {
        type: 'object',
        properties: {
          workflow: obj('完整工作流 JSON（schema_version/id/name/inputs/nodes/outputs/output_ports）'),
          inputs: obj('运行输入（可选；校验必需输入时传入）'),
        },
        required: ['workflow'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/validate', { workflow: args.workflow, inputs: args.inputs ?? null }),
    })
    register({
      name: 'flux_replacement_plan',
      description: '针对指定工作流节点计算候选模块的替换计划（compatible/conditional/needs_inputs/incompatible + 完整 diagnostics）。候选能力标签只是检索提示，是否可替换由平台按具体图判定；不要据此声称物理等价。',
      parameters: {
        type: 'object',
        properties: {
          workflow: obj('完整工作流 JSON'),
          inputs: obj('运行输入（可选）'),
          node_id: { type: 'string', description: '目标计算节点 id' },
          module_id: { type: 'string', description: '候选模块 id' },
          module_version: { type: 'number', description: '候选模块版本' },
        },
        required: ['workflow', 'node_id', 'module_id', 'module_version'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/modules/replacement-plan', {
        workflow: args.workflow, inputs: args.inputs ?? null,
        node_id: args.node_id, module_id: args.module_id, module_version: args.module_version,
      }),
    })

    // ── 注册（v3 扁平字典）───────────────────────────────────────────────
    register({
      name: 'flux_prepare_registration',
      description: '准备新模块注册的冻结上下文（POST /api/harness/prepare-registration）：上传单函数源码 + 模块 id + 指令 + 已知事实 + 可选独立样例，返回 context_id 与完整上下文（registry/词典/规则/数值能力/candidate_schema/rules）。\n\n'
        + '纪律：新模块 contract_version=3.0，每个输入输出为独立命名的扁平字典；原始函数的裸参数/嵌套返回用显式 adapter（arguments/returns 的 from 路径、assemble 组装、conversion 单位换算）适配；缺关键语义先集中追问一次，未答清不提交；不得把路径/算法配置塞进计算字典，不得隐藏数组变换。\n\n'
        + '本工具只准备上下文，不注册模块（注册走 flux_submit_candidate）。',
      parameters: {
        type: 'object',
        properties: {
          source: { type: 'string', description: '原始单函数源码（纯 Python，受控子集）' },
          entrypoint: { type: 'string', description: '入口函数名（可选）' },
          module_id: { type: 'string', description: '模块 id（^[A-Za-z][A-Za-z0-9_-]{0,79}$）' },
          project_id: { type: 'string', description: '工程 id（可选）' },
          instruction: { type: 'string', description: '本次注册指令/目的（必填，1–16000 字符）' },
          declared_facts: obj('已知事实（可选）'),
          anchors: arrObj('可选独立验证样例 [{inputs, expected, tolerance?}]；期望值须来自证据或可手算，不得用自己生成的输出充当答案'),
        },
        required: ['source', 'module_id', 'instruction'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/harness/prepare-registration', {
        source: args.source, entrypoint: args.entrypoint || null, module_id: args.module_id,
        project_id: args.project_id || '', instruction: args.instruction,
        declared_facts: args.declared_facts || {}, anchors: args.anchors || [],
      }),
    })
    register({
      name: 'flux_submit_candidate',
      description: '提交候选模块并登记不可变版本（POST /api/harness/submit-candidate）：module（contract_version 3.0 + 显式 adapter + naming_rules）+ anchors + context_id。平台会用锚点回归验证，失败返回 status=rejected + 完整 regression diagnostics；成功返回 registered + replacement_plans。\n\n'
        + '纪律：必须先 flux_prepare_registration 拿 context_id；上下文过期（STALE_CONTEXT）要重新准备；锚点必须覆盖所有输出；显式变换至少两个验证样例；不要重试同一份未改的候选去碰运气。HTTP 错误与 diagnostics 原样返回给你，按提示修正，不要伪造默认值。',
      parameters: {
        type: 'object',
        properties: {
          module: obj('完整 Module JSON（id/version/name/inputs/outputs/contract_version=3.0/provenance/implementation/source/entrypoint/adapter/naming_rules/...）'),
          anchors: arrObj('验证样例 [{inputs, expected, tolerance?}]，至少 1 个、覆盖全部输出'),
          context_id: { type: 'string', description: '来自 flux_prepare_registration 的 context_id（可选但强烈建议，保证上下文未过期）' },
        },
        required: ['module', 'anchors'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/harness/submit-candidate', {
        module: args.module, anchors: args.anchors || [], context_id: args.context_id || null,
      }, 180000),
    })

    // ── 修改任务（harness prepare-change，v2 上下文）────────────────────
    register({
      name: 'flux_prepare_change',
      description: '准备指定节点修改的冻结上下文（POST /api/harness/prepare-change）：给定工作流 + 输入 + 目标节点 + 指令（+ 可选上传源码），返回 context_id 与上下文（原实现、上下游接线、词典、规则、基线、目标样例、candidate_schema）。\n\n'
        + '这是 harness v2 上下文路径，配合 flux_submit_candidate（带 context_id）与 flux_replacement_plan / flux_validate 使用。若要走带 clarify/answer 的 v3 修改任务，用 flux_prepare_change_task。',
      parameters: {
        type: 'object',
        properties: {
          workflow: obj('完整工作流 JSON'),
          inputs: obj('运行输入（可选）'),
          node_id: { type: 'string', description: '目标计算节点 id' },
          instruction: { type: 'string', description: '修改指令（必填）' },
          source: { type: 'string', description: '上传的候选源码（可选）' },
        },
        required: ['workflow', 'node_id', 'instruction'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/harness/prepare-change', {
        workflow: args.workflow, inputs: args.inputs ?? null, node_id: args.node_id,
        instruction: args.instruction, source: args.source || '',
      }, 180000),
    })

    // ── 修改任务（change-tasks v3，带澄清）──────────────────────────────
    register({
      name: 'flux_prepare_change_task',
      description: '创建“选中节点公式修改”任务（POST /api/change-tasks）：给定工作流 + 输入 + expected_revision + node_path + 指令 + 验收锚点 + 授权新增接线，返回 task_id/context_id/context。平台会先跑基线并把目标节点实际样本放进上下文。\n\n'
        + '流程：本工具 → （缺关键语义）flux_clarify 集中追问一次 → flux_answer → flux_apply_change 提交候选并原子发布（平台校验锚点回归 + 工作流执行）。expected_revision 来自 flux_read_workflow。',
      parameters: {
        type: 'object',
        properties: {
          workflow: obj('完整工作流 JSON'),
          inputs: obj('运行输入（可选）'),
          timeout_seconds: { type: 'number', description: '基线/回归超时（秒，默认 30，1–120）' },
          expected_revision: { type: 'number', description: '当前工作流 revision（乐观锁，来自 flux_read_workflow）' },
          node_path: { type: 'array', items: { type: 'string' }, description: '目标节点路径（如 ["loss"] 或含 body/cases 分支的路径）' },
          instruction: { type: 'string', description: '修改指令（必填）' },
          anchors: arrObj('验收样例 [{inputs, expected, tolerance?}]；至少 2 个完整样例'),
          added_bindings: obj('授权新增接线 {name: Binding}；必须与用户明确声明一致'),
          unchanged_outputs: { type: 'array', items: { type: 'string' }, description: '保持不变的原输出名（可选）' },
        },
        required: ['workflow', 'expected_revision', 'node_path', 'instruction'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/change-tasks', {
        workflow: args.workflow, inputs: args.inputs ?? {}, timeout_seconds: args.timeout_seconds ?? 30,
        expected_revision: args.expected_revision, node_path: args.node_path, instruction: args.instruction,
        anchors: args.anchors || [], added_bindings: args.added_bindings || {},
        unchanged_outputs: args.unchanged_outputs || [],
      }, 180000),
    })
    register({
      name: 'flux_list_change_tasks',
      description: '列出最近的修改任务（status/instruction/node_path/expected_revision/created_at，最多 20 条）。',
      parameters: { type: 'object', properties: {}, required: [] },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async () => call('/api/change-tasks'),
    })
    register({
      name: 'flux_read_change_task',
      description: '读取某个修改任务及其冻结上下文（含原实现、授权新增接线、验收锚点、基线样例、candidate_schema）。',
      parameters: {
        type: 'object',
        properties: { task_id: { type: 'string', description: '任务 id（来自 flux_prepare_change_task / flux_list_change_tasks）' } },
        required: ['task_id'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/change-tasks/' + args.task_id),
    })
    register({
      name: 'flux_clarify',
      description: '对修改任务集中追问一次（POST /api/change-tasks/{task_id}/clarify）：把关键语义缺口一次性以 questions 对象提交（如 {"alpha": "请明确 alpha 的数值（V/℃）"}）。平台限制每个任务只能集中追问一次；未答清的关键项应报告阻断，不要猜测。',
      parameters: {
        type: 'object',
        properties: {
          task_id: { type: 'string', description: '任务 id' },
          questions: obj('关键问题 {问题id: 问题文本}（1–12 项）'),
        },
        required: ['task_id', 'questions'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/change-tasks/' + args.task_id + '/clarify', { questions: args.questions }),
    })
    register({
      name: 'flux_answer',
      description: '回答澄清问题（POST /api/change-tasks/{task_id}/answer）：answers 必须覆盖 questions 的全部键且非空。回答后任务回到 awaiting_candidate，才可 flux_apply_change。',
      parameters: {
        type: 'object',
        properties: {
          task_id: { type: 'string', description: '任务 id' },
          answers: obj('本轮全部答案 {问题id: 答案文本}（键须与 questions 完全一致）'),
        },
        required: ['task_id', 'answers'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/change-tasks/' + args.task_id + '/answer', { answers: args.answers }),
    })
    register({
      name: 'flux_apply_change',
      description: '提交修改候选并原子发布（POST /api/change-tasks/{task_id}/apply）：context_id + 新 module + 授权新增接线。平台会校验候选版本/契约/锚点回归/不变输出，然后跑完整候选工作流并一次性发布模块、接线、图修订与冻结运行。\n\n'
        + '纪律：必须保留原有输入与全部输出；新增输入必须全部显式接线且等于 authorized_added_bindings；context_id 必须最新；候选不能仍是原实现；失败返回 stage + diagnostics/regression，按提示修正，不要填零或放宽契约。',
      parameters: {
        type: 'object',
        properties: {
          task_id: { type: 'string', description: '任务 id' },
          context_id: { type: 'string', description: '最新任务上下文 id（来自 flux_read_change_task）' },
          module: obj('新 Module JSON（同模块族指定新版本；保留原输入与全部输出）'),
          added_bindings: obj('新增接线 {name: Binding}（须等于 authorized_added_bindings）'),
        },
        required: ['task_id', 'context_id', 'module'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/change-tasks/' + args.task_id + '/apply', {
        context_id: args.context_id, module: args.module, added_bindings: args.added_bindings || {},
      }, 180000),
    })

    // ── 运行 ──────────────────────────────────────────────────────────────
    register({
      name: 'flux_run',
      description: '运行工作流（POST /api/runs）：冻结输入与模块后拓扑排序执行，返回 outputs/trace/diagnostics。运行始终使用独立快照，不受并发编辑影响。任何计算/比较都应通过工作流执行，不要手算数字。',
      parameters: {
        type: 'object',
        properties: {
          workflow: obj('完整工作流 JSON'),
          inputs: obj('运行输入（可选）'),
          timeout_seconds: { type: 'number', description: '超时（秒，默认 30，1–120）' },
        },
        required: ['workflow'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/runs', {
        workflow: args.workflow, inputs: args.inputs ?? {}, timeout_seconds: args.timeout_seconds ?? 30,
      }, 180000),
    })
    register({
      name: 'flux_replay_run',
      description: '用某次运行的旧快照回放（POST /api/runs/{run_id}/replay）：即使草稿/模块已修改，旧快照仍返回旧结果。',
      parameters: {
        type: 'object',
        properties: { run_id: { type: 'string', description: '要回放的运行记录 id' } },
        required: ['run_id'],
      },
      output: { schema: { type: 'object', additionalProperties: true }, render: (a, v) => render(v) },
      execute: async (args) => call('/api/runs/' + args.run_id + '/replay', {}, 180000),
    })
  },
}
