# Flux 电力电子工作流平台

Windows 本地单机研究原型。支持声明驱动的计算模块、命名扁平字典接口、语义及单位校验、可视化工作流、不可变版本和冻结回放；通过可选 DSH 提出计算方案、补齐公式或上传函数、接线检查并保存工作流。项目自定义声明须补充依据并批准后才启用。

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

源码环境改用 `.venv/Scripts/python.exe`。安装专用 flux、flux-worker、flux-assembly 和 flux-design-v2 preset，写入当前路径并备份已有专用配置，不改默认 agent。重复安装相同配置不会重复备份；移动目录后需重新安装 preset。启用前可查看 `GET /api/health`，该接口只报告配置，不证明模型服务在线。真实 AI 验收需要用户自己的服务及调用预算。

## 复现与交付

```powershell
.venv/Scripts/python.exe -m pytest backend/tests -q
npm --prefix frontend run build
# 便携包构建输入必须是经过哈希锁安装的新环境
.venv/Scripts/python.exe scripts/build_portable.py --environment .build/clean-venv --output dist/flux-windows-x64
# 在复制或解压后的新目录中运行
runtime/python.exe scripts/verify_portable.py --output verification.json
```

构建产物包含逐文件 SHA256 manifest、压缩包 SHA256、Python 与依赖许可文件。验证从无历史数据库、无宿主 Python/Node 路径及无 DSH 的环境启动，检查页面、双字典损耗、回放和完整 DBSRC 奖励基线。详细步骤与限制见 [可移植与复现说明](docs/portability.md)。

## 能力边界

上传只支持受控单函数及指定数学功能；复杂内置模型经过审计后接入。已迁移 DBSRC EPS 独立单步链并与冻结旧程序对照，保留旧模型已知限制，不代表器件实测准确。AI 建图仅验证候选电流到电容损耗两个职责，尚未实现任意拓扑规划。已有单标量 DE；PPO、完整 Pareto 和通用收敛迭代尚未打通。

历史本机验收结果不作为便携包用户数据发布。第三方组件保留原有许可；科学模型源码的权利归属需由课题组按实际来源管理，本项目没有擅自为其添加开源许可证。

### 需求优先建图

“AI 搭建工作流”现在先生成可确认的计算方案，支持缺工具暂存、公式或上传函数补齐，以及结构检查通过后保存未运行工作流。运行数值在保存后配置。详见 [使用与验证边界](docs/requirement-first-workflow-design.md)。环境包接口已预留，尚无完整领域知识包。

前端任务入口与无指导使用验收见 [易用性决策记录](docs/decisions-ui-usability.md)。

项目自定义表的申请、审批与版本规则见 [项目扩展说明](docs/project-extensions.md)。Windows 自动回归入口为 GitHub Actions 的 Windows reproducibility；便携包的独立验证脚本覆盖新表审批、单位转换和冻结回放，真实 DSH 仍需另行配置。
