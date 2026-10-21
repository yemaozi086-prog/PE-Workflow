# Power Electronics Workflow（PE-Workflow）

Windows 本地单机研究原型。支持声明驱动的计算模块、命名扁平字典接口、语义及单位校验、可视化工作流、不可变版本和冻结回放；通过可选 DSH 工作流助手直接对话，生成可测试草稿并根据运行诊断修复。项目自定义声明须补充依据并批准后才启用。

## 直接使用

下载 Windows x64 便携运行包，解压到可写目录，双击 **Start.cmd**。包内带 Python 和依赖、已构建前端、词典和冻结科学模型；核心计算无需安装 Python、Node、旧 WDY 项目或 DSH，也无需联网。关闭服务窗口中的 Ctrl+C 停止。首次初始化新数据库，不包含原机器的工作流历史或凭证。

便携包只验证 Windows x64，不承诺 Linux/macOS 通用或跨平台位级浮点一致。系统 VC 运行环境相关 DLL 随 Python 运行时包含。

## 从源码开发

准确工具链见 `toolchain.json`：Python 3.14.3、Node 24.14.0、npm 11.9.0。其他版本未作为本次交付环境验证。

```powershell
powershell -NoProfile -File scripts/setup.ps1 -Python <Python3.14.3路径>
powershell -NoProfile -File scripts/start.ps1
```

也可以在安装好上述工具后双击 Start.cmd 自动执行首次依赖安装和构建。源码首次配置需要联网；版本不符会明确报错。

Python 使用 `backend/requirements-win-py314.lock` 精确版本与轮子 SHA256 校验；前端通过 `npm ci` 使用 package-lock.json 完整性校验。开发元数据 pyproject.toml 不代替发布锁文件。源码按原字节检出，冻结科学源码的哈希不会被 Git 换行转换破坏。

## 配置与数据

复制 `config.example.toml` 为 `config.local.toml` 后修改。数据库相对路径以项目根目录解析，默认 `data/workbench.sqlite`。配置端口、打开浏览器及可选 DSH 本机地址；只允许 loopback 服务。启动检查端口占用，失败不假装启动成功。数据、配置和日志均不提交 Git。

日常创建、复制、重命名和归档工作流使用列表菜单；归档可恢复，不删除模块、历史版本和运行记录。参数使用声明表单，JSON 是高级入口。试读成功不等于模型经过工程实测验证。

## 可选 DSH

运行包不捆绑 DSH 服务、模型或 API key。先单独安装配置 DSH，再设置 config.local.toml 的 dsh.enabled=true 和本机地址，执行：

```powershell
runtime/python.exe scripts/install_dsh_presets.py
```

源码环境改用 `.venv/Scripts/python.exe`。安装专用 pe-quick 及兼容旧流程的 flux、flux-worker、flux-assembly 和 flux-design-v3 preset，写入当前路径并备份已有专用配置，不改默认 agent。重复安装相同配置不会重复备份；移动目录后需重新安装 preset。启用前可查看 `GET /api/health`，该接口只报告配置，不证明模型服务在线。真实 AI 验收需要用户自己的服务及调用预算。

## 复现与交付

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
npm --prefix frontend run build
# 便携包构建输入必须是经过哈希锁安装的新环境
.venv/Scripts/python.exe scripts/build_portable.py --environment .build/clean-venv --output dist/PE-Workflow-windows-x64
# 在复制或解压后的新目录中运行
runtime/python.exe scripts/verify_portable.py --output verification.json
```

构建产物包含逐文件 SHA256 manifest、压缩包 SHA256、Python 与依赖许可文件。验证从无历史数据库、无宿主 Python/Node 路径及无 DSH 的环境启动，检查页面、双字典损耗、回放和完整 DBSRC 奖励基线。详细步骤与限制见 [可移植与复现说明](docs/portability.md)。

## 能力边界

上传只支持受控单函数及指定数学功能；复杂内置模型经过审计后接入。已迁移 DBSRC EPS 独立单步链并与冻结旧程序对照，保留旧模型已知限制，不代表器件实测准确。AI 建图支持显式来源的多节点计算链、有限笛卡尔网格与单层 batch 子图，可排队补齐工具、查看诊断并局部修正；尚不支持任意嵌套或收敛循环，也不保证任意任务一次生成成功。已有单标量 DE；PPO、完整 Pareto 和通用收敛迭代尚未打通。

历史本机验收结果不作为便携包用户数据发布。第三方组件保留原有许可；科学模型源码的权利归属需由课题组按实际来源管理，本项目没有擅自为其添加开源许可证。

### 与工作流助手对话

首页点击 **用自然语言搭建流程**，在对话窗口粘贴需求，或使用内置标准需求模板。助手可读取平台实际契约、现有工具及有限网格/子图规则，并将生成的草稿直接更新到工作流库。

1. 描述拓扑、计算目标、公式、单位、适用假设与需要的输出；关键资料缺失时在同一对话补充。
2. 草稿生成后点击 **打开草稿，填写输入并测试**，核对数值与单位后运行。
3. 在结果区选择 **交给 DSH 修复／核对结果**，或把诊断粘贴回原对话，继续修改同一工作流。修订保留旧版本，测试输入不会因修复被清空。

窗口显示对话进度和草稿状态，支持附加文件、停止和恢复历史对话。运行期间画布所有连接显示动态虚线，包括因节点选择而淡化的连接。旧分步建图管道已从主要入口停用，已有记录与工作流保留。

草稿保存不等于数值验证或工程认证；非法模块不能安装，接线未通过的草稿不能运行。不自动填写实际工况，也不自动运行。详细操作和限制见 [工作流助手说明](docs/quick-chat.md)。环境包接口已预留，尚无完整领域知识包。

前端任务入口与无指导使用验收见 [易用性决策记录](docs/decisions-ui-usability.md)。

项目自定义表的申请、审批与版本规则见 [项目扩展说明](docs/project-extensions.md)。Windows 自动回归入口为 GitHub Actions 的 Windows reproducibility；便携包的独立验证脚本覆盖新表审批、单位转换和冻结回放，真实 DSH 仍需另行配置。


## 当前交付与环境验证

源码更新日期：2026-10-22。当前源码更新不代表旧 Releases 运行包已同步重建。源码、锁定依赖、自包含 Windows x64 运行包分别交付；运行数据、API 密钥、本机配置和测试数据库不上传。运行包及校验文件见仓库 Releases。

- 计算链中的接口、单位、来源、字段依赖与控制结构分别检查。检查通过不等于工程模型正确，独立样例和实测依据仍需提供。
- 已注册循环成员可省略重复接口声明；平台先读取冻结接口，再生成循环输出和下游声明。
- 候选原始提交、局部修正、剩余额度与恢复记录可追溯；平台错误重验不会消耗模型调用。
- 验证范围与操作说明见 [本次交付记录](docs/release-20261021.md)。

快速对话专项验证：`python -m pytest backend/tests/test_quick_chat.py -q`；提交封装检查：`node scripts/test_quick_transport.cjs`。前端交互逻辑检查：`npm --prefix frontend test`。
