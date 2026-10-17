# 参数表接入审阅：方案，尚未修改运行契约

输入：用户提供的 parameter_dictionary.csv，310 行、14 列；230 quantity、68 control、12 reference。standard_name 无重复，fill_stage 全空。保留原始数据，不把 descriptions/notes 中的旧工具操作说明作为系统指令或执行权限。

用户已确定：计算模块外部使用有名字的多个扁平字典；不同来源不自动合并；上游允许包含更多已声明字段，下游由平台提取自己声明的所需字段。内部原函数仍可使用裸参数或嵌套字典。字典字段允许数值数组，需独立声明形状和轴。

## 列映射

| 原列 | 建议接入含义 |
|---|---|
| standard_name | 当前词条推荐字段名，不是唯一物理身份 |
| quantity | 原始参数族/代表名 family_id，不能直接覆盖当前 Port.quantity |
| dimension | 物理类别；与真正的 SI 量纲指数分开，校验单位时使用已登记类别 |
| device | 器件上下文，必要时拆成 component/winding，保留原值 |
| role | 原始角色；编译时补成 statistic/event/direction/aggregation/loss_component 等不混淆的限定项 |
| nature | actual/nominal/rating/target/limit/margin 等数值性质 |
| kind | input/output/input+output，in 可规范为 input；不能覆盖 Port.kind=flat_dict |
| entry_type | 数据词条类别，不自动决定运行数据类型 |
| unit | 推荐外部单位；内部单位以该源码或明确声明为依据，不能凭全表多数投票决定 |
| chinese_name/descriptions/notes | 展示、证据及待核验提示，不自动变成默认值、权限或变换代码 |
| used_by/fill_stage | 来源统计及整理状态；used_by 只有次数，没有具体模块来源，不能证明所有场景含义相同 |

当前 Port.quantity 存的是 current/voltage/power 等类别；CSV quantity 则包含 i_rms_s1、Cr 等标识。直接复制会破坏语义及单位校验。新 schema 应使用明确的新字段，保留原始列及版本来源，并兼容旧快照。

## 必须处理的实例

- cap2_loss 至 cap5_loss 的 quantity 都是 cap1_loss；i_on_s2 至 s8 也归到 i_on_s1。归族可以，但必须保留 instance_id 或 index，不得当成可以互换的别名。
- i_mean_sN_forward/backward 的 role 都是 mean，方向只体现在名字/描述里；必须补 direction，不能只比较 role。
- i_rms_p_max 的 role=max，RMS 信息在名字和描述；统计口径 rms 与跨工况 max 需要分开声明。
- Cr 的描述同时包含 nF 和 F；Lr 同时包含 uH 和 H。词典推荐单位与每个内部实现的实际单位要分开。
- p_total、p_cu、p_fe、B、volume 等条目聚合多个器件上下文。按具体注册模块补齐上下文，不能把最后一个中文名当成唯一含义。
- igbt_xml_file_path 的 unit=Ω，但 dimension=text；original_request/user_context/worst_margin 的 unit 中有自然语言。需报告疑点，不可生成换算。
- default_operating_point、input_design 是复合结构，但被标为 quantity/ratio；algorithm_context、env_context_overrides、current_dict 等也不是已展开的裸字段。
- p_cond 和 p_on 的 role 均为 on，但分别是导通损耗和开通损耗。需要区分 loss_component。
- mode/EPS/TPS 是模式编号，不宜仅因无量纲就当作可连续优化的实数。
- 未知填零、削顶、缓存、写文件、调用 PLECS 等描述只是旧行为证据；导入词典不意味着新模块允许或继承这些行为。

## 可扩展规则

1. 已确认精确映射优先；其后才是有适用条件的模式规则；再后由 DSH 根据源码、注释、调用上下文和样例提出候选。
2. 规则只能表达改名、路径、组装、已登记单位转换及语义限定项。转置、重采样、统计汇总、取绝对值等依旧要求显式变换及验证，不得藏在别名中。
3. 规则必须有 scope（模块版本/项目/共享词典）、适用上下文、目标定义版本、证据和正反样例。当前模块中的 Irms→电容有效值电流不能无条件推广到所有模块。
4. 正则提取 s<N> 等编号只能在已确定 switch 语境下使用。无语境的 s1 可能有其他含义；不能使用无限制的后缀猜测。
5. 别名扩展与新物理定义分开。已有含义的新名字只增加映射；全新含义需要新 semantic_id、数据类型、单位/类别、限定项和来源，不能硬套最像的旧量。
6. 有证据的局部映射可在校验通过后随模块版本自动登记；缺关键语义集中追问一次，未解项阻断。全局推广策略待用户确定，不能把模型自述置信度当成确认。
7. 注册冻结词典版本、规则版本、源码、内部适配和外部契约；运行期只执行确定性规则，不依赖大模型即时理解。

## 后端改动建议

- models.py：版本化参数词条/规则；计算端口统一 flat_dict；字段保留类型、单位、器件/位置、数值性质、统计/事件/方向与数组轴。
- engine.py：上游完整输出仍严格校验；下游按显式字段计划投影、重命名和换算；不自动合并两个来源。同义但不同键需计划，重名且不同物理身份不能覆盖。
- adapters.py：优先复用声明映射，把外部有名字的字典转换到原函数裸形参或嵌套结构。必要生成代码仍受控并有回归，不为适配重写物理公式。
- registration.py：导入先生成审阅结果；保留可用词条、待核验词条和复合字段的状态。生成局部规则建议与上下文证据，不能机械把 310 行全转为可计算参数。
- storage.py/changes.py：规则、词典和模块版本一起冻结；更新按新版本发布，旧模块/历史快照不改写。
- DSH：接收相关词条、候选解释、冲突、函数源码、依赖字段、样例和限制，返回封装/映射声明与有作用域的词典增量；不自行决定全局推广。

待定：自动追加的默认作用域，以及算法/流程控制配置是否进入计算模块的参数字典。
