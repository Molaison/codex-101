# 安装、配置与模型选择

本文面向 **codex-101 v0.2.1 preview 的 `--magpie` 预设**。先按 [README 的安装命令](../README.md#安装并开始使用) 操作；本文解释前置条件、默认行为、模型选择和常见故障。

## 1. 安装前准备

- 在实际使用 Codex 的电脑上运行，不需要登录维护机。
- 准备个人 CPR Key，并确认已开通 `gpt-6.1-sol` 和 `DeepSeek-V4.1-Flash`；只在终端隐藏提示中输入。不要使用别人的 Key，也不要分享到聊天、命令参数或 Git。
- macOS / Linux 需要 `curl` 与 Python 3.8+。Windows 需要 64 位系统，安装器会在用户目录准备并校验 Python，不要求管理员权限。
- 首次安装需要访问 GitHub 发布附件、npm 官方包，以及 `https://cpr.molaisonz.dpdns.org/v1`。Windows 准备 Python 时还需要访问 python.org。
- 先保存当前工作。安装器不会强杀活跃 Codex 任务；需要刷新配置时，只在空闲条件满足后重启本地 server，否则返回待处理状态。

输入 Key 后，安装器先核对两个模型的访问权限，默认配置为 `gpt-6.1-sol`（`high`），并通过 Magpie 用最终官方客户端以 `DeepSeek-V4.1-Flash`（`none`）完成真实工具自检。自检不改变保存的默认 Sol。实验室自检前台最多等待 20 分钟；超时可能只是仍在运行，不能当成失败后重新投递。

**完成标准是 `[READY]` 加成功退出。** 打开新终端、进入项目目录后运行 `codex`。若命中了已有定制版，使用 README 列出的实验室独立入口。

### 共享 Lustre HOME

Linux 上安装器自动检测 HOME 是否位于 Lustre。在每个需要使用 Codex 的节点各运行一次 [README 中的安装命令](../README.md#安装并开始使用)；每个节点都需要可用的 systemd 用户服务，并独立运行 `codex101-magpie.service`。共享 HOME 保存安装所需的持久配置种子和 Codex 配置。

各节点的服务启动时，在本节点 `/tmp` 下按用户 UID 创建私有运行配置、缓存、数据、状态和锁。Codex 连接本节点 localhost，所有节点使用同一个 Codex101 专用端口；已有普通 Magpie 的 3425 端口和服务不受影响。共享 HOME 的 f102 和 f100 已各自通过 Sol / `high` 真实文件读取；f100 验收时 f102 测试守护进程已停止。

### 可选：Pi

macOS / Linux 可用独立入口配置 Pi：

```bash
(set -o pipefail; curl --http1.1 -fsSL --retry 2 https://raw.githubusercontent.com/Molaison/codex-101/main/install-pi.sh | bash)
```

脚本复用已有 Pi；找不到时调用 pi.dev 官方安装器。Pi 需要 Node.js 22.19+，缺失时由官方安装器处理；脚本还需要 Python 3.8+。它核验 CPR Key 和 `gpt-6.1-sol` 权限，直接连接 `https://cpr.molaisonz.dpdns.org/v1`，不依赖 Magpie。

运行 `~/.local/bin/pi-cpr`，默认 Sol / `high`。如果 Key 的模型列表包含 `DeepSeek-V4.1-Flash`，可用 `/model` 选择；Pi 的 `off` thinking 对应不使用推理。独立配置位于 `~/.local/share/codex101-pi/agent`，已有 `~/.pi` 不受影响。Pi 1.4.0 的隔离安装配置、Sol 模型列表和 Sol / `high`、Flash / `off` 真实文件读取均已通过；现有环境的 Node.js 版本为 22.20.0。

## 2. 安装器会改什么

| 项目 | v0.2.1 行为 |
| --- | --- |
| 官方客户端 | 首次选择 npm `@openai/codex/latest` 的稳定版本，校验平台包 SHA512，再校验可执行版本；不修改二进制，不覆盖原有系统/npm 客户端；同时安装 Magpie 并连接 canonical CPR。 |
| 后续运行 | 复用完好的官方副本和同 tag 安装组件；重跑入口仍会检查本仓库 Release。主动升级使用维护命令，不靠日常启动。 |
| 配置 | 通过官方配置接口更新实验室所需字段，保留其他配置。全权限、默认模型、服务地址及 SQLite 目录等实验室字段会改变。 |
| 启动入口 | 新建实验室独立入口，更新用户 PATH / shell 启动配置；不会删除原客户端。 |
| 凭据 | 保存于当前用户的本地配置状态目录，原生配置通过 `auth.command` 读取凭据。不要分享整个 `CODEX_HOME`、`connection.json` 或 Key 文件。 |
| 模型目录 | CPR 维护目录先过滤为 V1，再合并当前官方 Codex 的内置目录；同 ID 时 CPR 条目优先，不更改模型协议标记。若无法读取官方目录则停止更新，避免静默隐藏官方模型。 |
| 网络 | Responses 使用 SSE，禁用 WebSocket；HTTP 最多重试 8 次、流最多重试 12 次，SSE 无事件超时为 15 分钟。 |
| 数据库 | 预检失败不迁移；通过后尝试使用本机私有临时目录。重复安装复用已有库；重试时仅在空闲且副本完全一致时采用目标库；不同库不覆盖、不混合，冲突或写入风险时保留原配置目录和原库并可继续使用，不强杀进程。 |
| 已有数据 | 保留 `CODEX_HOME`、本地 sessions 和旧官方客户端副本。软件回滚不等于数据库或会话备份。 |

**全权限风险：** `sandbox_mode="danger-full-access"`、`approval_policy="never"` 允许 Codex 操作当前用户能访问的文件，不会逐项请求批准。共享目录和项目以外的文件也在风险范围内；运行前检查工作目录并备份重要数据。

**凭据风险：**隐藏输入和文件访问权限不是加密保险箱。不要把状态目录加入 Git、共享网盘或故障附件；仅分享检查过的脱敏诊断。更换 Key 时重跑安装命令：Bash 在 `--magpie` 后、末尾的 `)` 前增加 `--reconfigure`；PowerShell 在末尾增加 `-Reconfigure`。共享 HOME 模式下，使用此参数显式刷新共享配置种子；随后在其他节点各重跑一次安装命令，使各节点采用新的 Key 和模型。未加该参数时，已有种子保持不变。

### SQLite 的默认位置

- Linux：`/tmp/<用户名>/codex-sqlite`
- macOS / Windows：系统临时目录下的 `<用户名>/codex-lab/codex-sqlite`

这些位置是节点本地的，跨节点 SQLite 历史不会自动迁移或合并；共享 HOME 中的 sessions 保留。这些临时位置可能被系统清理，不能成为重要历史的唯一备份。只有源目录存在 `state_5.sqlite` 时才进入迁移检查；检测到 Codex 进程、目标存在不同数据或复制期间文件变化时，打印 `[SQLITE_PRESERVED]` 并沿用源库。显式选库和具体保留边界见 [SQLite 恢复说明](03_upgrade_rollback.md#sqlite-冲突先保留再选择)。

## 3. 选择模型

默认使用 `gpt-6.1-sol`（`high`），在 Codex 中输入 `/model` 切换。Magpie 使用 `cpr/<raw>` 限定名。显示名只是标签，脚本/API 中使用准确的模型 ID。以下描述是维护目录中的用途和路由语义，**不表示当前 Key 已开通，或该路由已通过真实工具测试**。

| 模型 ID | 用途 |
| --- | --- |
| `gpt-6.1-sol` | 默认；日常编程和通用任务。 |
| `DeepSeek-V4.1-Flash` | 使用 `none` 档位进行真实工具自检。 |
| `chatgpt-web/gpt-6-tools-temp` | GPT-6 工具模式、Web 临时会话，面向文件与编程任务。 |
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

如需持久 Web 会话，请主动在模型菜单选择相应路由；切换后仍须确认当前地址和 Key 的权限，并以真实调用结果为准。

### 推理档位与不可见模型

`high`、`xhigh`、`max` 是推理档位，不是独立模型。Web 条目的目录包含这三档；实验室安装配置默认设为 `high`。更深档位的耗时和额度取决于服务端，不在这里承诺性能。

V1-only 只过滤 CPR 维护目录中的模型；随后会把当前官方 Codex 的内置目录合并进来，因此 `gpt-6-astra`、`gpt-6-sol`、`gpt-6-luna`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.5` 等原生条目会按官方目录的可见性恢复到 `/model`（具体以当前安装的 Codex 版本为准）。如果无法提取官方目录，安装器会明确报错，不再默默写入一个缺少官方模型的目录。

**菜单可见不等于 CPR 支持调用。** 实验室配置通过 Magpie 将这些模型请求发送到 CPR endpoint；需确认该 Key 的 `/models` 包含对应 ID，并以真实调用结果为准。这里恢复的是官方模型元数据和菜单项，不会偷偷切换到 OpenAI 官方 API 或更改凭据。

## 4. 常见故障

先保留终端状态和发生时间，查看 `[NEXT]`，再查 [升级与恢复手册](03_upgrade_rollback.md)。不要因为 `Reconnecting...` 或前台超时就重复提交同一任务，尤其是会写文件或执行外部操作的任务。

| 状态 | 下一步 |
| --- | --- |
| `AUTH` / `ACCESS` | 分别检查 Key / 请管理员核对模型权限。不要发送 Key 本身作为故障证据。 |
| `NETWORK` / `ENDPOINT` | 先跑下一节的 `preflight` 一条命令定位真实原因；不要关闭 TLS 校验。 |
| `CHECK_PENDING` / `PENDING` | 原自检可能仍在运行，按提示恢复原事务，不新建相同回合。 |
| `SERVER_BUSY` / `PENDING_RESTART` / `SERVER_RESTART_REQUIRED` | 等已有任务结束，再按提示恢复；不要强杀共享任务。 |
| `UPSTREAM_COMPAT` | 请维护者检查 CPR / Web 桥接兼容，重装 CLI 不会修复服务端协议。 |
| `UPSTREAM_READINESS` | 已报告上游 readiness 故障；同模型会复用保存的失败记录，避免盲目重投。上游修复后再加 `--repair` / `-Repair` 明确重测。 |
| `RECOVERY_REQUIRED` | 无法安全自动判断或恢复。保留事务和自检记录，交给维护者核查，不删除记录或强制重测。 |
| `OUTPUT_MISMATCH` / 其他未 READY | 提供脱敏状态、时间、客户端版本、模型和目标地址；不要上传整个配置目录。 |

SSE 和重试设置只能缓解部分短暂断流，不能保证修复服务端故障；重试增加也可能延长最终报错时间。`/models` 可用只说明权限预检通过。真实验收标准及历史地址范围见 [CPR 验收说明](01_cpr_acceptance.md)。

### 4.1 预检失败（`NETWORK` / `ENDPOINT`）先跑这一条

安装器对 `{地址}/models` 做权限预检（每次 20 秒、最多 3 次、禁止重定向）；失败时终端会给出带 `reason` 的 JSON 和一条 `next` 命令。Python urllib 连接失败时可回退到系统 curl，保持相同代理和 TLS 校验，不绕过 TLS。此命令只重测这一项，不写配置、不碰 SQLite、不调用模型：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/provider-connect/codex_provider.py" preflight
```

Windows：`python "%USERPROFILE%\.codex\provider-connect\codex_provider.py" preflight`（设了 `CODEX_HOME` 时用它）。安装已失败、没有保存 key 时，该命令会提示隐藏输入一次 Key；也可用 `CODEX_PROVIDER_API_KEY` 环境变量提供。

| `reason` | 诊断线索 | 处理 |
| --- | --- | --- |
| `timeout` | 连续 3 次都超过 20 秒，可能是端点或代理超时 | 稍后重跑；仍失败把 JSON 与发生时间发给维护者 |
| `connection` / `refused` / `reset` | 连接失败、被拒绝或重置 | 重跑 `preflight`，持续失败时提供脱敏诊断 |
| `dns` | 域名解析失败 | 检查 DNS 或代理的域名解析 |
| `tls` | TLS 握手或证书校验失败 | 检查代理是否替换了证书；不要关闭校验 |
| `http` | 网关返回可重试的 5xx / 429（已重试 3 次） | 等网关恢复后重跑 |
| `body` | 拿到了 HTTP 响应但不是 JSON，可能是代理或网关错误页 | 检查响应并将脱敏的 `detail` 发给维护者 |

预检自动重试 2 次，仍失败时按 `reason` 和 `[NEXT]` 排查。`preflight` 通过后重跑原安装命令即可继续，不需要 `--repair`。
