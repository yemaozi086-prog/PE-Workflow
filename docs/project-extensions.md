# 项目自定义声明表

标准参数词典和内置单位表保持不变。新增定义存放在 SQLite 的 `project_extensions` 表；申请、补充及审批历史存放在 `extension_requests` 表。默认项目是 `local`，新建任务可在“项目范围与自定义声明”中指定其他标识。

## 使用流程

1. DSH 先读取 `design_context.extension_catalog`，它合并标准条目与当前项目已批准的自定义条目，保留来源、版本及词典原有的待审核状态。没有完整环境包，词典不是物理模型正确性认证。
2. 发现缺项时，DSH 可提交 `kind=extension_request`，类型为单位 `unit`、参数 `parameter` 或等义别名 `alias`。缺少依据的字段留空，不允许自行编造。已确认方案中出现未登记单位时，平台在公式生成前主动申请，尚未消耗新的 DSH 调用。
3. 用户在页面填写含义、依据和定义，先“保存补充，暂不启用”，再批准或拒绝。单位采用 `基准值 = 数值 × scale + offset`，量纲标识须与对应字段的物理量标识一致；温差不使用绝对温度的偏移。
4. 依据不全不能批准，待定和拒绝条目均不可出现在统一读取结果中。同名标准条目禁止覆盖；参数与别名共用名称空间，别名只能指向已有参数，禁止别名链。
5. 批准后派生方案，原方案及记录保留。批准本身不会调用 DSH、注册工具、发布工作流或运行。已有候选可重新验证；其计算代码不改变。没有候选时，核对派生方案后再继续正常生成流程。

## 版本和运行

批准条目只追加新版本。自定义单位解析后，其完整量纲、倍率和偏移以及项目、版本、指纹写入端口声明；模块、工作流和运行快照因此保留实际使用的定义。旧版本解析不会自动升级到最新版本。没有扩展引用的旧声明不新增空字段，避免破坏不可变词典的启动校验。

参数和别名提供可检索的声明依据，不自动改写物理含义或接线；适配和绑定仍须声明明确路径。此机制不会从一个新单位推断整套新拓扑模型，也不自动推广到共享标准表。

## 接口

- `GET /api/extension-catalog?project_id=local`：统一读取。
- `POST /api/extension-requests?project_id=local`：提出申请，正文为 ExtensionProposal。
- `GET /api/extension-requests/{id}`：读取申请和历史。
- `POST /api/extension-requests/{id}/edit`：按申请 revision 保存补充。
- `POST /api/workflow-designs/{task_id}/extension-decision`：按申请 revision 审批并派生方案。
- `POST /api/extension-requests/{id}/decision`：独立申请的审批，不推进工作流任务。

DSH 使用新的独立预设 `flux-design-v2`；旧 v1 预设保留。待运行的旧任务遇到上下文配置变化会暂停，需核对后继续。
