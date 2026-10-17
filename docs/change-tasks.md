# 选中节点的公式修改任务

本阶段实现接口候选闭环：选节点、输入指令、准备完整上下文、接收候选、校验、计算、原子发布。**DSH 尚未接入，系统不会根据任意自然语言生成代码。**界面中的 alpha 手算模板是确定性夹具，仅验收平台流程，统一标注“平台自带手算示例，待课题组核对”。

## 页面使用

1. 在热损耗工作流中，双击“遍历工况”，选中“选择损耗模型”，进入 temperature 分支，选中 loss 节点。
2. 属性面板点击“自然语言修改任务”。对话框明确展示完整目标路径及版本。
3. 首个演示默认使用 alpha 模板；指令及 alpha 数值由用户明确提交。alpha 留空会提问，不连接默认值。目前固定手算夹具只覆盖 0.002 V/℃；不同公式或不同系数应关闭模板，提供自己的 anchors 与新增接线，再提交真实 DSH 候选。
4. 点击“准备修改任务”，平台运行冻结的完整原图，整理相关节点全部工况样例、上下游消费、原公式、契约、数值函数清单、下一版本和验收依据。
5. 可导出完整任务给 DSH。模板模式展示带明确标签的固定候选；普通模式只有结构骨架，原实现未变时不能通过任务。用户可以粘贴 DSH 的候选 JSON。
6. 点击“验证、保存并运行”。模块样例、保持不变的输出、完整候选图都通过后，平台在一个 SQLite 事务内写入不可变模块、工作流新修订、成功运行快照和任务完成状态。只替换指定路径节点，其他引用不动。
7. 失败显示阶段及诊断，原图和模块目录均不发布改动。任务可恢复；版本或目录过期需要重新准备上下文。任务成功重复提交同一候选返回原结果，不重复增加版本。

手算公式：`P_cond=(vce+alpha*(Tj-25))*i_rms*0.5`，`P_sw=e_sw*fs`，`P_total=P_cond+P_sw`。输入 `vce=1.7 V, i_rms=8 A, fs=100000 Hz, e_sw=0.001 J, alpha=0.002 V/℃` 时，25 ℃ 对应 6.8/100/106.8 W；125 ℃ 对应 7.6/100/107.6 W。三工况整图均值为 107.2 W、最大值 107.6 W。若原本固定系数已经是 0.002，结果应不变，变化在于 alpha 成为显式模块输入和接线。

## DSH 对接协议 3.0

GET `/api/harness/change-task-contract` 返回输入 schema 和工具表；旧 registration-contract 通过 selected_change_protocol 指向它。DSH 使用本协议时不要先调用旧 submit-candidate 注册模块，否则会使当前任务目录指纹过期。

- POST `/api/change-tasks`：完整 workflow / inputs、expected_revision、node_path、instruction、至少两个 anchors、明确授权的 added_bindings、unchanged_outputs。node_path 示例 `['sweep','body','model','cases','temperature','loss']`。alpha_demo/alpha 仅用于首个确定性验收模板。
- GET `/api/change-tasks/{id}`：读取最新 context_id 及完整冻结上下文。GET 集合接口可列出近期任务。
- POST `/api/change-tasks/{id}/clarify`：`{questions:{id:问题}}`，关键语义一次集中询问。未回答前拒绝应用。
- POST `/api/change-tasks/{id}/answer`：`{answers:{id:回答}}`，全部问题均需有答案；生成新的上下文指纹。回答是供 DSH 阅读的证据，不会自动修改原来授权的新增接线或验收结果；如需改变这些授权，应重新准备任务。
- POST `/api/change-tasks/{id}/apply`：`{context_id,module,added_bindings}`。模块必须派生指定族和版本，保留原输入输出契约；新增接线必须精确等于用户授权；anchors 由冻结任务提供，候选不能自行换答案或放宽容差。此接口完成样例、整图、执行与原子发布。

模板/用户样例只能验证覆盖的数值行为，不能证明任意工况下物理等价。任务保留基准结果、逐节点样例、回归记录、目标路径和成功运行 id；运行按原有快照协议回放。任务接入后仍应在界面明确区分“模板候选”“DSH 候选”和独立核验状态。
