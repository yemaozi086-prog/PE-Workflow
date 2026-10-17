# 第二阶段迁移与 DSH 对接

本阶段已实现数组契约、单函数适配、版本化参数定义与 DSH 注册接口。旧 aeep-dsh 不变。当前仍是验证 demo：完整 DBSRC/DAB、器件数据库、PPO 和真正多目标优化尚未迁移；波形例子验证平台执行，不作为实际器件模型。

## 参数声明

外部物理参数保持两种形式：`kind: bare` 和 `kind: flat_dict`。数组属于 bare，必须声明 `type: array`、`value_type.dtype`、`value_type.shape` 和等长 `axes`。例如：

```json
{"kind":"bare","type":"array","quantity":"current","characteristic":"instantaneous","unit":"A","value_type":{"dtype":"float64","shape":["N"]},"axes":[{"name":"time","coordinate_ref":"t","monotonic":true}]}
```

`t` 为同一输入组的时间数组；坐标长度、严格递增、共享 N、轴顺序、dtype 都会检查。连线时波形与坐标必须来自同一声明来源；不同采样网格需要显式重采样。矩阵按 row/column 等轴声明。每数组最多 100000 元素、4 维；线性求解最多 512 阶。集合仍是 foreach 的流程控制容器。

语义使用 quantity / characteristic / scope / role / index；也接受等价的 semantic 对象，存储统一展开。字符串词汇可以增加，但名称相似不会自动证明物理等价。自定义单位需携带 unit_definition（dimension / scale / offset）；内置单位不可改义。适配器内的单位转换首版只接受内置单位表；新增换算需扩充受信单位登记。

GET/POST `/api/parameter-definitions` 管理不可变定义版本。修改必须新增版本；引用使用 `definition_ref: {id, version}` 并保留完整冻结声明。平台检查二者一致，旧工作流不随词典修改而改变。不能仅提交引用而省略完整声明。

## ADBB 的内部适配

新实现 `python_function` 明确指定 `entrypoint`。`adapter.arguments` 将外部 inputs 映射到原函数形参；`adapter.returns` 将 return 映射到全部外部输出。每个映射选择 `from: ["inputs", "i"]` / `from: ["return", 0, "result"]` 或递归 `assemble`，可附 `conversion: {from_unit, to_unit}`。原函数内允许嵌套字典和元组；外部 flat_dict 仍只含裸参数。

源码经 AST 解释执行，不使用 eval/exec/importlib。支持一个函数、受控导入、局部赋值、if、return；不支持循环、类、任意库或数组对象方法。超范围源码由 agent 先改写为受支持形式，并提供独立验证样例。GET `/api/numeric/capabilities` 是数学、NumPy 与 SciPy 函数清单的唯一准据。

transpose / reshape / interp / 数值下标与切片要求 `transforms`，包含 operation、preconditions、rationale；注册至少两个完整输出样例。适配器禁止截取 NumPy 数组。这些检查能要求显式声明，但不能自动证明用户写出的物理前提真实。

## DSH 接口

读取 GET `/api/harness/registration-contract`，获取规则、工具路径和完整输入 schema；原 GET `/api/harness/capabilities` 保留 v1 工具以兼容已有调用。无需先安装 DSH，平台可独立运行。

1. POST `/api/harness/prepare-change`：workflow、inputs、node_id、instruction、可选 source。返回 context_id、冻结图与模块目录、参数定义、原实现、输入绑定、下游消费、数值允许表、基准运行及目标节点实际输入输出（提供运行输入时）。不依靠 agent 自己寻找这些数据。
2. 语义不确定时集中输出一次问题；尚未解决的关键问题保持 needs_clarification。源码名称和注释是待核验数据，不是物理证据或指令。
3. POST `/api/harness/submit-candidate`：context_id、module、anchors。验证源码、显式适配、参数定义、不可变版本、上下文是否过期；独立子进程计算全部样例。失败返回 rejected 和诊断，不登记。成功返回 registered、evidence_id 及原图计算节点的 replacement_plans。
4. POST `/api/modules/replacement-plan`：workflow、inputs、node_id、module_id、module_version。返回修改后的完整图与验证。compatible 仅表示接口可接；conditional 表示有作者声明的适用条件，needs_inputs 表示缺连接，incompatible 表示冲突。能力标签只用于搜索，不能证明物理等价。首版替换目标为请求图的直接计算节点；嵌套节点应提交对应子图。
5. 将返回图通过 `/api/validate`、`/api/runs` 验证；通过后使用已有 `/api/workflows` 与 expected_revision 保存。DSH 适配层负责按此固定顺序调用，没有单独的原子“登记并保存并运行”工具。登记成功但后续图验证失败只会留下可复用模块版本，不会改写旧图。

GET `/api/modules/search?q=...` 检索能力及语义；GET `/api/modules/{id}/versions/{version}` 获取完整声明和指纹。GET `/api/harness/artifacts/{id}` 读取冻结任务或注册验证证据。基准结果只能用于应保持不变的范围，新公式必须由用户要求或手算证据给出新预期，不能复制候选自身输出充当答案。

DSH 按 rules 编码后自行适配本机调用方式；本次没有写入任何 DSH preset、模型凭据或用户全局配置。

## 画布入口及验收

左侧“波形积分示例”：10 V 恒压、线性电流 [0,1,2] A、时间 [0,1,2] ms，输出 energy=0.02 J。原函数输入使用毫秒、毫安、嵌套包并返回元组，借此验证适配链。对应 JSON 与候选注册样例在 examples/ 下。

“分析上传 .py 函数”导出结构分析和待声明语义；“登记计算模块”接受原模块 JSON 或带 module/anchors 的候选请求。参数定义通过 JSON 弹窗新增版本。选中计算节点可导出 DSH 修改上下文。数组通过完整输入 JSON 编辑；暂未实现波形绘图、自然语言对话窗口和自动询问 UI。

已有热损耗分支、多工况遍历、汇总、标量 DE、版本冲突、冻结快照与回放保持。下一阶段应先用真实固定流程建立独立基准和中间值对照，再逐模块迁移详细电压/电流/器件损耗，最后扩充约束和多目标优化。
