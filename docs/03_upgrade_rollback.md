# codex-101 v0.2.0：更新、离线回滚与恢复

此文档描述 **尚未发布的 v0.2.0 源码候选**。现有公开 v0.1.1 是历史预发布版本，不具备下面的全部维护命令；先通过后续 v0.2.0 Release 首次安装或更新。

## 日常入口

平时运行 `codex`。维护操作使用 `codex101 status`、`codex101 upgrade`、`codex101 upgrade --codex-only`、`codex101 rollback`、`codex101 recover` 和 `codex101 doctor`。Windows 维护脚本位于 `%LOCALAPPDATA%\CodexLab\bin\codex101.cmd`；Linux/macOS 为 `~/.local/share/codex-lab/bin/codex101`，可通过绝对路径调用。

首次安装不固定 Codex：`codex_provider.py` 读取 npm `@openai/codex/latest`，验证稳定版本号，再解析当前 OS/CPU 平台包的 SHA512。运行中的 Codex 不会因重复执行安装器而静默升级；只有明确执行 `upgrade` 才重新选择最新稳定版。旧二进制及其实际版本、SHA256 保留在当前用户 `CODEX_HOME/provider-connect/official/`，用作离线回滚。

## 一键安装遇到 SQLite 冲突时

v0.2.0 的 `--lab` 在默认迁移前检查原目录和本地临时目录。发现两份不一致的数据、Codex 正在使用旧库或源文件在复制中改变时，不对两个数据库写入合并，也不强制退出安装；**保留已经配置/默认使用的原库**，打印 `[SQLITE_PRESERVED]` 和可复制的完整切换命令。只有源库可安全停写且目标为空时才执行原来的整套 SQLite/WAL/SHM 拷贝。若要改用另一份数据，用安装命令的 `--sqlite-home <绝对路径>` 或 PowerShell 的 `-SqliteHome <绝对路径>` 显式选库，选择不执行文件迁移。原版 v0.1.1 不支持这些参数。

任何本地安装错误都打印确切的 `status` 命令；当存在可安全恢复的 pending 事务时，还打印 `recover`。如果事务处于 `RECOVERY_REQUIRED`，指向 journal 而非建议重复提交无法确认终态的模型回合。

## 升级流程

1. `upgrade` 查询有完整附件及 manifest 的 codex-101 Release，并按 stable 优先选择；`--codex-only` 使用已经验证的本地 helper/catalog，不要求重新下载本仓库 Release。
2. 通过 `install.lock` 防止同用户并发修改；下载至临时目录，对照 `release_manifest.json` 验证 helper/catalog/安装引擎 SHA256，再安装到不可变的 `releases/<tag>/` 目录。同 tag 缓存完整时直接复用。
3. 保存升级事务到私有 `transactions/<id>/`，记录此前 `active`、本次参数、安装前配置与客户端记录，以及阶段状态。旧 flat `v0.1.x` helper/catalog 先复制到 `releases/legacy-*`，不动 Key、会话或 SQLite。
4. 调用新版本的官方 Codex 配置和真实工具自检。新发布 tag 强制重新检验，真实自检由既有 `checks/current.json` 维持同一回合所有权，前台中断不会触发重复提交。
5. 自检通过后才记录 `READY`；若 CPR/账号侧失败，记录 `UPSTREAM_UNAVAILABLE` 或 `NOT_VERIFIED`，不自动降级。自检仍在运行则返回 `PENDING`，后续使用 `recover` 查询。安装器内部失败且可安全恢复时，将配置/客户端记录恢复到事务前的版本。

`active.json` 记录 codex-101 `active`、`previous`、`last_ready`、实际 `codex_version`、状态以及可回滚的事务 ID。发布 manifest **不固定 Codex 二进制版本**，只声明 `codex_channel: stable`。

## 离线回滚

执行 `codex101 rollback` 时 **不访问 GitHub、npm 或 CPR**。先验证上一个版本目录 SHA256、旧官方 Codex 可执行性与已记录的 binary SHA256。若有活跃 Codex 或未完成模型回合，返回 `PENDING_RESTART`，不终止进程。

对于升级时被本安装器修改的 `config.toml` 和 `client.json`，只有当前文件的 SHA256 **仍等于升级后记录**时，才恢复私有快照；若用户或其他进程随后修改了文件，返回明确冲突而非覆盖。**显式 `--reconfigure` 同时变更了凭据时，不自动执行这种配置回滚。** Key 文件、`connection.json`、SQLite、WAL/SHM、sessions、Memory 设置从不作为软件回滚文件复制或覆盖。

回滚执行前写入 `rollback-pending.json`。即使进程在恢复配置、客户端记录或切换启动入口的中途退出，`codex101 recover` 仍可依据该记录完成剩余步骤。回滚完成状态为 `ROLLED_BACK_LOCAL`，仅证明本地文件恢复；这**不是**新的远端 `READY`。

如果 `codex101` 不在 PATH，可直接用本机 Python 运行已安装目录中的 `install_lifecycle.py recover` 或 `rollback`，完全不依赖安装网站。Windows 首次安装默认自带 Python；有既存兼容 Python 时，维护者可显式设置 `CODEX_CONNECT_PYTHON`。

若安装进程在真实自检的提交边界崩溃，且无法根据原回合记录确认是否已提交，`recover` 会返回 `RECOVERY_REQUIRED` 并保留日志，**不会再发送第二次模型请求**。本地配置补偿也先写事务记录再逐项恢复；若恢复途中再次中断，可用同一命令继续。Windows bootstrap 会检查缓存 Python 是否可启动，缓存损坏时先验证新下载的 Python，才尝试切换，并保留不可用旧副本。

## 发布门槛和仍需验证的边界

在仓库根目录运行 `python 01_build_release_manifest.py --check`、`python -m unittest -q test_lab_install test_onboarding test_profiles test_install_lifecycle`。CI 还执行 Bash/PowerShell 语法检查、Windows 隔离 PowerShell bootstrap 与失败恢复测试。`release.yml` 是手动触发的 preview 发布流程，要求三平台测试通过，并在 Windows/macOS/Linux 各下载并启动当时最新稳定的原生 Codex；不会自动推送或升级 stable。

正式宣布稳定版前，仍需各目标系统/用户用自己的、绑定正确 HTTPS CPR 的 Key，在最终实际安装的 Codex 中通过一次真实只读工具调用；验收必须含 `turn.completed`、工具退出码 0、完整标记匹配和客户端退出码 0。CI fixtures、`/models`、官方二进制 `--version` 或历史另一版本的成功记录均不能替代该验收。
