# 自然语言装配：第一阶段基础接口

状态：场景声明、计划编译、独立 assembly worker 调度和自然语言任务 UI 已实现。真实 DSH 验收结果单独记录，不能把人工提供计划的编译测试算作智能体建图成功。

首次真实任务原批次未通过；用户批准后，新配置下基准与变化工况均各一次生成通过，独立参考和回放通过。详见 assembly-dsh-status.md。结论仅限该小场景。

## 已确定范围

DBSRC / EPS，给定设计、器件和一个工况，生成全部调制候选并逐候选计算谐振电容介质损耗。仅使用冻结的 `dbsrc_modulation@1`、`dbsrc_loss_capacitor@1`。不自动选型、不筛选最优候选、不训练 PPO、不做收敛求解，也不编写 DAB 工作流。

现有电流模块要求器件字典，其 valid 字段参与候选生成门控；它不证明实际器件设计正确。损耗的 `previous.valid=1` 是明确的兼容约定：本小链不执行上游器件约束，绝不代表上游约束已通过。无候选/无效候选的填充值不代表有效损耗。

## 接口

- `GET /api/scenes`：对象形式返回 items/count；场景审核状态为 extracted_from_code，不伪造专家审核。
- `POST /api/assembly/context`：提交 instruction、facts、inputs；返回规则结果、固定模块声明、真实来源文件及 SHA256、计划 schema、权限、上下文哈希。没有目标 JSON 或参考数值答案。这个预览接口不会触发 DSH。
- `POST /api/assembly/preview`：提交同一任务资料及 plan；平台检查职责、固定版本、证据标识与数据来源，然后复制真实契约，创建有限 batch 子图，验证图与输入。不会运行、保存工作流、登记模块或修改旧版本。
- 得到的 workflow/inputs 可继续交给现有 `/api/runs`；保存、回放沿用现有接口。预览返回的 execution/reference 必须仍为 not_run/not_checked，不能因契约通过而改为成功。

facts 首版要求 modulation=EPS、scope=single_workpoint、assessment=loss_only、upstream_constraints=not_evaluated、control=finite_batch。缺失形成澄清项，明确不兼容形成 blocked。用户意图与这些结构化条件之间的解释仍需后续 DSH 协议实现，平台现在不会自动从一句话推断它们。

plan 包含 control=finite_batch、summary 和两个 steps。每个 step 包含 id、role、module_id、module_version、inputs、reason、evidence。它只提交引用与解释，不能复制或修改端口契约。平台创建 batch、子图接口、输出契约与布局。当前编译器有意仅支持本小场景的两职责；不是任意拓扑通用规划器。

## 验证和经验

报告分别展示接口检查、领域规则、执行、独立参考及工程适用性。经验卡默认 candidate/review_required，并列出范围和限制；还没有经验确认/项目检索持久化服务。智能体说明与平台检查结果分开，说明不能替代真实验证。

## 已实现任务流程

UI 顶部“AI 搭建工作流”打开任务面板。用户可以加载明确标注的旧程序输入示例，然后检查/修改目标、条件与数据，再提交。首版仍需要结构化输入与条件，不能只说一个拓扑名称就自动完成设计。

`POST /api/assembly-tasks` 冻结输入、条件、模块版本、场景、证据哈希、实际项目提示词与工具哈希；模型真实标识未取得时显示未知。安装版 preset 必须与项目文件一致，否则不调用。凭证仅存内部记录，不进入普通任务展示/事件日志。

独立 preset 为 `flux-assembly`，只含 `assembly_context` 与 `assembly_result`。其桥接只接受任务凭证并访问该任务的上下文/结果端点，不能发布或运行。原 `flux-worker` 保留原用途。

任务状态包括 dispatch_ready、dispatching、awaiting_worker、validating、needs_clarification、blocked、paused、delivery_unknown、stopped、completed。平台对缺失条件集中提问，DSH 可提交一批关键问题；集中追问后仍不明确则停止。回答必须覆盖当前全部问题；上下文修订保留旧快照。

首次最多一次生成加三次修正，同类错误连续两次提前停止。用户批准追加时，单批最多三次调用。十分钟无回传或服务重启后的未完成调用标记 delivery_unknown，不自动重发；原调用仍能回传，主动重试会轮换凭证。停止任务立即使晚到回调失效，正在运行的远端会话/本地计算可能在原超时内结束，但不能再发布。

编译通过后，平台在隔离计算子进程执行，再在独立子进程调用冻结旧电流例程与独立电容公式参考。参考答案不传给 DSH。参考不可用时停止并要求核对输入，不靠修改接线掩盖无解。全部通过才在同一 SQLite 事务写入新的工作流 r1、冻结运行和 completed 任务事件，原工作流与模块不变。运行可沿现有 replay 接口回放。

用户能恢复历史任务、查看配置/方案/诊断/时间线、停止或批准追加修正；修改目标与输入创建新任务。首版没有运行中局部改图与增量重算，也没有经过核对经验的自动推广。

`backend/tests/test_scenarios.py` 的两个不同工况对照直接调用冻结 WDY 电流数值例程，并独立计算 I_rms²×0.001/(2πfC)，逐行核对全部候选及损耗。该对照证明迁移一致性，不证明实际器件准确性。测试也验证错误版本、跨节点候选、门控篡改和虚构依据被拒绝，以及注册表不被修改。

## 下一实施层

后续扩展能力检索、更多场景规则与经验核对/检索服务。当前只有两种固定职责、两个固定版本，不能以此宣称任意拓扑的工具选择或规划泛化能力。DAB 留待后续测试。
