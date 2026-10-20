# 可移植性与可复现性

## 交付方式

源码仓库与便携包分离。源码包含计算模型、静态器件数据、词典、前端源代码、DSH preset、锁文件和工具脚本。便携包额外包含精确 Python 运行时、从干净环境安装的依赖、前端 dist 和逐文件哈希；不包含旧 SQLite、用户任务、凭证、node_modules 或开发机器绝对路径。

本次基线为 Windows x64 / Python 3.14.3 / Node 24.14.0 / npm 11.9.0。便携包日常运行无需 Node；源码构建需要 Node。环境自包含指核心平台与计算，外部 DSH 仍是独立可选集成。

## 锁定与构建

1. backend/requirements-win-py314.lock 为 Windows cp314 精确轮子及 SHA256。用 --require-hashes --only-binary=:all: 安装，禁止未经审查的隐式升级和现场编译。
2. package-lock.json 通过 npm ci 锁定间接依赖和完整性。toolchain.json 固定解释器及构建工具版本。
3. 构建从新虚拟环境读取 site-packages，复制其 Python 基础运行时及标准库。python314._pth 固定隔离搜索路径，排除宿主用户包与 PYTHONPATH。
4. 发布包保留 Python LICENSE.txt 及各 wheel 的 dist-info 许可证。release-manifest.json 保存文件 SHA256，压缩包旁的 .sha256 用于传输校验。哈希证明一致性，不是代码签名，也不保证 ZIP 每次字节完全相同。
5. .gitattributes 禁止换行转换，因为科学模型按源码原始字节核验。公式及可信实现不因打包而改写。

## 配置

config.example.toml 为模板，config.local.toml 为本机设置。相对数据库路径始终按项目根解析。配置只允许 127.0.0.1 或 localhost；DSH 同样只接受本机 HTTP origin。通过 launch.py 启动统一设置子进程源码目录和数据库位置。

DSH 默认关闭，创建任务会返回 DSH_DISABLED 而非永久排队。开启服务与安装 preset 是独立步骤；安装不改变默认 agent，也不携带密钥。回调桥读取同一项目端口配置，运行目录迁移后必须更新 preset 中的 root。临时 --port 只适用于普通计算；启用 DSH 时应修改配置文件，以使回调端口一致。

## 干净环境验证的判据

新建虚拟环境，用离线 wheelhouse 按哈希安装，并运行 pip check；从新环境进行后端回归。把发布 ZIP 解压到另一个含空格和中文的目录，用包内解释器运行 verify_portable.py。验证子进程使用无关 CWD、全新数据库、只含 Windows System32 的 PATH，去除宿主 Python 和虚拟环境变量，关闭 DSH。

检查发布文件哈希、解释器 prefix、健康状态、前端资源、初始空工作空间、双字典损耗约 1.136821022 W、回放输出相同，以及 DBSRC 默认奖励约 0.704378925。具体执行结果写入 docs/portability-verification.json（仅非敏感摘要）。

这属于同一 Windows 主机上的隔离干净运行验证，不等同全新操作系统或企业真实电脑验收。如果没有可用 VM，就应在交付记录中明确这个限制，后续请另一台 Windows x64 机器解压执行同一脚本。

## GitHub 内容边界

GitHub 源码仓库不包含 data、backups、.venv、.build、dist、用户本机配置或 DSH 根路径文件。运行包应作为 Release 附件，不把运行时二进制提交源码历史。上传前检查暂存文件和疑似密钥。当前仓库按用户要求公开；运行历史、凭证和本机配置仍保持排除，不自动给课题组模型添加开源许可。

## 当前版本补充

- 新增使用说明随便携包交付；本机 evidence、任务数据和交接报告不打包。
- 自定义声明的空字段不改变旧标准词典；审批表在新数据库中自动创建。
- DSH 新建图使用 flux-design-v3，重复安装相同配置幂等，目录迁移时另存原配置后更新路径。
- 离线源码配置可执行 `scripts/setup.ps1 -Python <固定版本解释器> -Wheelhouse <轮子目录> -NpmCache <预填缓存> -Offline`。轮子与 npm 包均继续进行锁文件完整性校验；空依赖环境不复用旧虚拟环境或 node_modules。
- GitHub Actions 在 Windows runner 上安装固定工具链、运行回归、构建并解压验证便携包。远程执行结果以对应提交的 Actions 状态为准，本机验证不能替代该结果。
