# 安装、配置与模型选择

本文面向 **codex-101 v0.2.0 preview 候选的 `--lab` 预设**。先按 [README 的安装命令](../README.md#安装并开始使用) 操作；本文解释前置条件、默认行为、模型选择和常见故障。

## 1. 安装前准备

- 在实际使用 Codex 的电脑上运行，不需要登录维护机。
- 准备个人 CPR Key，只在终端隐藏提示中输入。不要使用别人的 Key，也不要分享到聊天、命令参数或 Git。
- macOS / Linux 需要 `curl` 与 Python 3.8+。Windows 需要 64 位系统，安装器会在用户目录准备并校验 Python，不要求管理员权限。
- 首次安装需要访问 GitHub 发布附件、npm 官方包，以及 `https://cpr.molaisonz.dpdns.org/v1`。Windows 准备 Python 时还需要访问 python.org。
- 先保存当前工作。安装器不会强杀活跃 Codex 任务；需要刷新配置时，只在空闲条件满足后重启本地 server，否则返回待处理状态。

输入 Key 后，安装器先核对模型访问权限，再用最终官方客户端读取一个临时文件并匹配返回内容。实验室自检前台最多等待 20 分钟；超时可能只是仍在运行，不能当成失败后重新投递。

**完成标准是 `[READY]` 加成功退出。** 打开新终端、进入项目目录后运行 `codex`。若命中了已有定制版，使用 README 列出的实验室独立入口。

## 2. 安装器会改什么

| 项目 | v0.2.0 行为 |
| --- | --- |
| 官方客户端 | 首次选择 npm `@openai/codex/latest` 的稳定版本，校验平台包 SHA512，再校验可执行版本；不修改二进制，不覆盖原有系统/npm 客户端。 |
| 后续运行 | 复用完好的官方副本和同 tag 安装组件；重跑入口仍会检查本仓库 Release。主动升级使用维护命令，不靠日常启动。 |
| 配置 | 通过官方配置接口更新实验室所需字段，保留其他配置。全权限、默认模型、服务地址及 SQLite 目录等实验室字段会改变。 |
| 启动入口 | 新建实验室独立入口，更新用户 PATH / shell 启动配置；不会删除原客户端。 |
| 凭据 | 保存于当前用户的本地配置状态目录，原生配置通过 `auth.command` 读取凭据。不要分享整个 `CODEX_HOME`、`connection.json` 或 Key 文件。 |
| 模型目录 | 使用发行包内的 V1 目录，当前含 9 个可见模型和 2 个隐藏条目；不把 V2 元数据改写成 V1。 |
| 网络 | Responses 使用 SSE，禁用 WebSocket；HTTP 最多重试 8 次、流最多重试 12 次，SSE 无事件超时为 15 分钟。 |
| 数据库 | 尝试使用本机私有临时目录；存在冲突或写入风险时继续使用原库，不合并、不删除另一份。 |
| 已有数据 | 保留 `CODEX_HOME`、本地 sessions 和旧官方客户端副本。软件回滚不等于数据库或会话备份。 |

**全权限风险：** `sandbox_mode="danger-full-access"`、`approval_policy="never"` 允许 Codex 操作当前用户能访问的文件，不会逐项请求批准。共享目录和项目以外的文件也在风险范围内；运行前检查工作目录并备份重要数据。

**凭据风险：**隐藏输入和文件访问权限不是加密保险箱。不要把状态目录加入 Git、共享网盘或故障附件；仅分享检查过的脱敏诊断。更换 Key 时重跑安装命令，Bash 增加 `--reconfigure`，PowerShell 增加 `-Reconfigure`。

### SQLite 的默认位置

- Linux：`/tmp/<用户名>/codex-sqlite`
- macOS / Windows：系统临时目录下的 `<用户名>/codex-lab/codex-sqlite`

这些位置可能被系统清理，不能成为重要历史的唯一备份。只有源目录存在 `state_5.sqlite` 时才进入迁移检查；检测到 Codex 进程、非空目标或复制期间文件变化时，打印 `[SQLITE_PRESERVED]` 并沿用源库。显式选库和具体保留边界见 [SQLite 恢复说明](03_upgrade_rollback.md#sqlite-冲突先保留再选择)。

## 3. 选择模型

在 Codex 中输入 `/model`。显示名只是标签，脚本/API 中使用准确的模型 ID。以下描述是维护目录中的用途和路由语义，**不表示当前 Key 已开通，或该路由已通过真实工具测试**。

| 模型 ID | 用途 |
| --- | --- |
| `chatgpt-web/gpt-6-tools-temp` | 默认；GPT-6 工具模式、Web 临时会话，面向文件与编程任务。 |
| `chatgpt-web/gpt-6-tools-persist` | GPT-6 工具模式，保存 Web 账号聊天历史。 |
| `chatgpt-web/gpt-6-chat-temp` | GPT-6 一般问答、Web 临时会话，不作为完整 Codex 工具工作流的替代。 |
| `chatgpt-web/gpt-6-chat-persist` | GPT-6 一般问答，保存 Web 账号聊天历史。 |
| `chatgpt-web/gpt-5.6-tools-temp` | GPT-5.6 工具模式、Web 临时会话。 |
| `chatgpt-web/gpt-5.6-tools-persist` | GPT-5.6 工具模式，保存 Web 账号聊天历史。 |
| `chatgpt-web/gpt-5.6-chat-temp` | GPT-5.6 一般问答、Web 临时会话。 |
| `chatgpt-web/gpt-5.6-chat-persist` | GPT-5.6 一般问答，保存 Web 账号聊天历史。 |
| `gpt-5.6-luna` | 目录中的轻量模型；能力以实际调用为准。 |

### Tools / Chat

`Tools` 面向读写文件、执行命令和编程。`Chat` 面向文字问答与讨论。要完成代码修改，优先使用工具模式；不能仅凭模型出现在菜单中就认定它支持所需工具。

### Temporary / Persist

- `temp`：Web 会话不保存进账号聊天历史，不适合依赖该历史恢复长期任务。
- `persist`：Web 账号保留聊天历史，方便回查，也意味着更多账号侧留痕。

`temp` **不等于服务端零留存或绝对隐私**。两种模式与 Codex 本地 sessions 分开；重要结论应写回项目文件。安装器不会为解决 readiness 问题自动从 `temp` 切到 `persist`，也不会自动开启 Memory。

若明确接受保存 Web 账号聊天历史，可在 README 安装命令中追加：

- Bash：`--model chatgpt-web/gpt-6-tools-persist`
- PowerShell：`-Model 'chatgpt-web/gpt-6-tools-persist'`

这只是主动选择另一条路由，不保证消除故障；切换后仍须使用当前地址和 Key 完成真实工具验收。

### 推理档位与不可见模型

`high`、`xhigh`、`max` 是推理档位，不是独立模型。Web 条目的目录包含这三档；实验室安装配置默认设为 `high`。更深档位的耗时和额度取决于服务端，不在这里承诺性能。

原生 `gpt-6.1-sol`、`gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.5` 等名称不在当前实验室的可见 V1 菜单中。上游目录列出、Key 的 `/models` 返回、以及客户端工具调用成功，是三个不同层次。可选项以当前发行包的 [model_catalog.json](../model_catalog.json) 和实际权限为准。

## 4. 常见故障

先保留终端状态和发生时间，查看 `[NEXT]`，再查 [升级与恢复手册](03_upgrade_rollback.md)。不要因为 `Reconnecting...` 或前台超时就重复提交同一任务，尤其是会写文件或执行外部操作的任务。

| 状态 | 下一步 |
| --- | --- |
| `AUTH` / `ACCESS` | 分别检查 Key / 请管理员核对模型权限。不要发送 Key 本身作为故障证据。 |
| `NETWORK` / `ENDPOINT` | 检查当前网络、代理、DNS 和证书；不要关闭 TLS 校验。 |
| `CHECK_PENDING` / `PENDING` | 原自检可能仍在运行，按提示恢复原事务，不新建相同回合。 |
| `SERVER_BUSY` / `PENDING_RESTART` / `SERVER_RESTART_REQUIRED` | 等已有任务结束，再按提示恢复；不要强杀共享任务。 |
| `UPSTREAM_COMPAT` | 请维护者检查 CPR / Web 桥接兼容，重装 CLI 不会修复服务端协议。 |
| `UPSTREAM_READINESS` | 已报告上游 readiness 故障；同模型会复用保存的失败记录，避免盲目重投。上游修复后再加 `--repair` / `-Repair` 明确重测。 |
| `RECOVERY_REQUIRED` | 无法安全自动判断或恢复。保留事务和自检记录，交给维护者核查，不删除记录或强制重测。 |
| `OUTPUT_MISMATCH` / 其他未 READY | 提供脱敏状态、时间、客户端版本、模型和目标地址；不要上传整个配置目录。 |

SSE 和重试设置只能缓解部分短暂断流，不能保证修复服务端故障；重试增加也可能延长最终报错时间。`/models` 可用只说明权限预检通过。真实验收标准及历史地址范围见 [CPR 验收说明](01_cpr_acceptance.md)。
