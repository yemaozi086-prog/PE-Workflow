# DeepSeek Harness 适配接口

平台不依赖 Harness 启动，也没有伪造自然语言生成结果。未来 Harness 负责理解需求、准备模块候选和工作流；平台负责确定性检查与执行。

服务默认地址 `http://127.0.0.1:8020`。

| 接口 | 作用 |
|---|---|
| GET /api/harness/capabilities | 工具定义、HTTP 路径与同源请求 Schema |
| GET /api/schema | JSON 工作流 Schema |
| GET /api/modules | 可用模块及不可变版本、输入输出与实现 |
| POST /api/validate | 结构化试读，返回 valid/order/diagnostics |
| POST /api/runs | 冻结输入与模块后运行 |
| POST /api/modules | 登记新模块族 v1 |
| POST /api/modules/{id}/revisions | 候选模块 + 基准版本 + 锚点；通过后派生版本 |
| POST /api/workflows | 以 expected_revision 保存工作流及运行输入 |
| GET /api/workflows/{id} | 当前或指定 revision 的工作流 |
| POST /api/optimize | DE 标量目标优化 |
| GET /api/runs/{id} | 完整运行与源码快照 |
| POST /api/runs/{id}/replay | 用旧快照回放 |

试读/运行请求示例见 `examples/thermal-demo.json`，同样可以作为画布导入文件。运行追加 `timeout_seconds`；保存追加 `expected_revision`。每个端点的详细类型见 `/docs`。

建议接入流程：读取现有定义和模块 → 生成候选模块/连接 → 锚点检查和模块派生 → 用完整候选图试读 → 保存（带预期修订号）→ 运行 → 将结构化诊断送回 Harness。生成者可以自动修复，但不得把失效字段改成假零或放宽契约来掩盖错误。

模块登记与工作流保存是两个独立事务；若图更新失败，已经登记的新模块版本仍可见，但旧工作流保持原版本。运行始终使用独立快照，不受并发编辑影响。

当前只有本机受信调用边界，无账号、鉴权或公网发布能力。对局域网/公网开放前需要另行设计权限与生成任务隔离。
