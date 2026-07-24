# Project Goal

- 维护 `AlexbeatsZ/AstrBot` fork。
- 参考 `AlexbeatsZ/openclaw` 的现有实现，让 AstrBot 可以使用 agy CLI，并在供应商页面完成检测、下载、更新和 Web 认证。
- 以未来 Docker 部署为目标；程序和认证数据必须随 AstrBot `data` 卷持久化，不在本机部署运行服务。
- 修改直接提交并推送到个人 fork，不创建 Pull Request。

# Lessons Learned

- OpenClaw fork 的 agy 接入采用无会话 `--print` 调用：由宿主整理完整上下文、过滤宿主工具提示、让 agy 使用原生工具，并限制 10 分钟运行时间和 10 MiB 输出。
- 当前 agy 版本接受带思考级别的人类可读模型名称；AstrBot 对外保留稳定的 `gemini-3.5-flash` / `gemini-3.1-pro` ID，并兼容旧版模型参数。
- 官方发布清单提供版本、下载 URL 和 SHA-512。托管安装必须先校验，再原子替换 `data/agy/bin/agy`，失败时保留旧版本。
- agy 的独立 HOME 使用 `data/agy/home`；标准 Compose 已持久化 `/AstrBot/data`，所以容器重建不会丢失程序或登录状态。
- Web 认证通过 Linux PTY 和受 Dashboard JWT 保护的 WebSocket 转发到浏览器；同一时间只允许一个认证会话，最长 15 分钟。
- headless 工具权限可能被 agy 拒绝；危险的自动批准选项必须默认关闭，优先使用最小化权限规则。
- 官方 AstrBot Docker 镜像是 Debian/glibc；官方当前清单键为 `linux_amd64` / `linux_arm64`，不能推测不存在的 musl 清单。
- Windows 下仓库的 `pnpm generate:api` 使用 Unix `rm` 会失败，可直接运行同一 `openapi-ts` 生成器，再执行类型检查和生产构建。
- 2026-07-25 服务器故障并非 agy CLI 回归：运行中的 AstrBot 仍是官方 `soulter/astrbot:latest` v4.26.7，未安装托管 agy，也未配置 agy provider；实际默认模型是 `sensenova/deepseek-v4-flash`。
- `host.docker.internal` 是 Docker Desktop 指向 Windows 宿主的稳定别名，本身没有失效。2026-07-25 故障是服务器的 Clash Verge/Mihomo 进程已退出，Windows 未监听 7897；Clash 配置中的自动启动开关没有形成可靠的 Windows 启动项，所以容器连接该别名时收到 connection refused。最终保留 `http://host.docker.internal:7897`，不依赖会变化的 `100.113.70.121` 地址。
- 服务器现使用常驻 PowerShell watchdog 维护 Clash Verge 和 WSL：计划任务 `Clash Verge Autostart` 与 `OpenClaw WSL Keepalive` 均在登录时启动，循环每 15 秒检测并恢复目标，计划任务本身另配置每分钟重启、最多 999 次。受控故障测试中 Clash/7897 在 14 秒内恢复，WSL keepalive 在 6 秒内恢复。
- 原 `run.vbs` 的注释声称会保持 WSL 存活，但实际只运行一次 `service mysql start` 后退出；原 `OpenClaw WSL Keepalive` 任务也只是启动一次子进程，子进程以结果 9 退出后任务不会自行重启。`run.vbs` 已改为触发两项 watchdog 并继续启动 MySQL。
- AstrBot 的 `Sinm` aiocqhttp 平台必须保持 `enable: false`；2026-07-25 恢复时配置已核对并强制保持禁用，仅 `Alex` 启用。
- OpenClaw 使用的 agy 1.1.6 和 scoped proxy wrapper 均正常；直接 `agy --print` 与 OpenClaw 完整 agent 调用都成功。OpenClaw“挂掉”的直接原因是 Windows 计划任务 `OpenClaw WSL Keepalive` 未运行，导致 WSL 与 user systemd gateway 随会话退出；启动该任务后跨 SSH 会话 health 保持 `live`。
- OpenClaw 服务器仍存在核心 `2026.6.10` 与部分要求 plugin API `>=2026.7.1` 的 npm 插件版本警告；bundled QQBot 当前仍可连接。核心升级应作为独立部署任务处理，不应与本次运行时恢复混在一起。
- 自定义 AstrBot 镜像必须在多阶段 Docker 构建中生成并复制 Dashboard；只复制 Python 源码会让容器回退到官方/旧 WebUI，看不到 Agy 供应商界面。`.dockerignore` 需保留 Dashboard 源码但排除本地 `node_modules` 和 `dist`。
- 代码渲染插件的 Playwright Chromium 还需要 `libnspr4`、`libnss3`、ATK/AT-SPI、CUPS、XComposite 和 XDamage 运行库。缺少时插件会在浏览器下载完成后报共享库错误；这些依赖已固化到 Dockerfile。
- 服务器使用固定的自定义 Agy 镜像，不再使用会漂移的 `soulter/astrbot:latest`。托管 Agy 1.1.6 位于持久化数据卷，AstrBot 使用独立 HOME，仅从 OpenClaw 复制了最小认证/设置状态，没有复制会话历史。
- 最终回归结果：AstrBot WebUI HTTP 200；SenseNova 默认模型经 `host.docker.internal:7897` 真实回复 `PROXY_OK`；Agy provider 真实回复 `ASTRBOT_AGY_OK`；Playwright Chromium 149 可启动；OpenClaw health 为 `live`。容器首次重建会为现有第三方插件重新安装 Python/浏览器依赖，因此首次健康启动可能超过一分钟。
- 服务器关键回滚备份位于 `%LOCALAPPDATA%\Temp\.agents\astrbot-openclaw-startup-20260725-024408`、`astrbot-deploy-20260725-025448`、`astrbot-agy-config-20260725-030008`、`astrbot-final-deploy-bb349f7b5`、`astrbot-auth-fix-backup-19d20b9e4` 和 `astrbot-models-backup-b59474643`。
- Agy 的 `filtered` 提示词模式会删除 AstrBot 固定 Function Calling 提示、skills-like 提示，以及标题为 tools、skills、model aliases、messaging 的二级章节；人格、回复风格和对话历史仍保留，系统提示词超过 24000 字符时保留首尾。Agy provider 不消费 AstrBot `func_tool`，而是提示 Agy 使用自己的原生工具，避免两套工具协议冲突。
- 2026-07-25 Agy Web 认证终端无响应的直接原因是新增代码对标准 Python logger 使用了 `{}` 占位符；日志 handler 抛出 `TypeError` 后，异常日志又重复触发同一错误。已改为 `%s` 并增加 WebSocket 启动路径回归测试，服务器真实 WebSocket 返回 `ready`。
- Agy 模型目录不能硬编码。AstrBot 现沿用 OpenClaw 的 live CLI discovery 思路，在独立 HOME、代理和工作目录下运行 `agy models`；与 OpenClaw 只折叠最新 Gemini 别名不同，AstrBot 保留 CLI 返回的全部模型和思考变体，以便在供应商页面逐项添加和切换。
- 服务器模型 API 已验证返回 11 个模型，包括 Gemini 3.6/3.5 Flash、Gemini 3.1 Pro、Claude Sonnet/Opus 和 GPT-OSS；`gemini-3.6-flash-medium` 真实 provider 调用返回 `AGY_DYNAMIC_OK`。部署镜像固定为 `alexbeatsz/astrbot:agy-b59474643`。
- Agy 1.1.6 自带 `plugin` 管理命令和 `--sandbox`，但服务器当前没有导入任何 Agy plugin，Agy settings 也没有 permissions/sandbox 白名单，AstrBot 的危险自动批准开关保持关闭。若要开放工具，应优先采用专用工作目录、Agy 原生 sandbox、明确插件/工具 allowlist、调用超时/输出上限和审计，而不是全局启用 `--dangerously-skip-permissions`。
- 旧 provider-source 迁移曾直接使用 `<旧 provider ID>_source`，因此旧 Agy provider `agy/gemini-3.5-flash` 会产生不合理的 `agy/gemini-3.5-flash_source`。迁移现在优先从 `source/model` ID 提取 `source`，并让连接配置相同的多个模型复用同一 provider source；服务器现有实例已清理为 `agy/gemini-3.6-flash-high`。
- 2026-07-25 QQ 不响应的根因是部署时仅用 `compose.yml` 重建 AstrBot，使其进入 `astrbot_default`，而 Alex NapCat 位于 `astrbot_network`；虽然 Alex 配置、6201 监听和令牌均正确，跨容器反向 WebSocket 仍持续握手超时。服务器 Compose 现显式连接外部 `astrbot_network`，重建后日志确认 Alex OneBot 连接成功；Sinm 继续保持禁用。运行时回滚备份位于 `%LOCALAPPDATA%\Temp\.agents\astrbot-live-diagnosis\backup-20260725-040350`。
- AstrBot 重建时第三方插件初始化可持续约两分钟；期间宿主 6185 的 Docker 端口已监听，但请求可能暂时返回 502。服务完成启动后，Tailscale 地址 `http://100.106.169.46:6185/`、Dashboard JS/CSS 和本地回环访问均返回 HTTP 200。

# Task Board

- [x] Fork `AstrBotDevs/AstrBot` 到 `AlexbeatsZ/AstrBot`。
- [x] 分析 OpenClaw fork 的 agy CLI 接入方式。
- [x] 实现 Docker 持久化的 Agy CLI 检测、校验下载、安装和更新。
- [x] 在供应商页面加入 Agy CLI 配置、状态管理和 Web 认证终端。
- [x] 实现无会话聊天、图片、模型映射、超时、取消和输出限制。
- [x] 更新 OpenAPI、生成客户端、中英文文档和自动化测试。
- [x] 通过 Ruff、20 项目标测试、前端类型检查和生产构建。
- [x] 检查暂存内容，以 `88876ada7` 提交并推送到 fork 的 `master`。
- [x] 诊断 2026-07-25 服务器故障，确认根因是服务器 Clash 7897 无监听、OpenClaw keepalive 非常驻以及 AstrBot 被部署为官方镜像。
- [x] 建立 Clash Verge 与 OpenClaw WSL 常驻 watchdog，更新 `run.vbs`，完成两项受控退出恢复测试。
- [x] 构建并部署带 Agy CLI、Agy Dashboard 和 Chromium 依赖的固定 AstrBot 镜像 `agy-bb349f7b5`。
- [x] 恢复 Agy 1.1.6 独立认证环境，保留 SenseNova 为默认模型并确保 `Sinm` 禁用、仅 `Alex` 连接。
- [x] 完成 AstrBot WebUI、代理、默认 LLM、Agy LLM、Playwright、计划任务与 OpenClaw health 的端到端回归。
- [x] 修复 Agy Web 认证终端 logger 格式错误，并通过真实 WebSocket `ready` 回归。
- [x] 复刻 OpenClaw 的 Agy live model discovery 思路，动态显示 CLI 返回的全部 11 个模型并完成 Gemini 3.6 真实调用。
- [x] 清理 Agy 遗留 `_source` provider ID，并修正旧 provider-source 迁移的命名和同配置复用行为。
- [x] 修复 AstrBot 与 Alex NapCat 的 Docker 网络分离，固化 `astrbot_network` 并验证 OneBot 连接；Sinm 保持禁用。
- [ ] 若决定开放 Agy 工具，增加显式的原生 sandbox 配置和受控插件/工具网关；在安全模型确定前不启用全局自动批准。
