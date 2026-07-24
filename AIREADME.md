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
- AstrBot 的全局代理原为 `http://host.docker.internal:7897`，但服务器 Windows 没有监听 7897，导致容器内 `APIConnectionError`。改为已验证可从服务器访问的 `http://100.113.70.121:7897` 后，同一 SenseNova chat completion 返回 HTTP 200。
- AstrBot 的 `Sinm` aiocqhttp 平台必须保持 `enable: false`；2026-07-25 恢复时配置已核对并强制保持禁用，仅 `Alex` 启用。
- OpenClaw 使用的 agy 1.1.6 和 scoped proxy wrapper 均正常；直接 `agy --print` 与 OpenClaw 完整 agent 调用都成功。OpenClaw“挂掉”的直接原因是 Windows 计划任务 `OpenClaw WSL Keepalive` 未运行，导致 WSL 与 user systemd gateway 随会话退出；启动该任务后跨 SSH 会话 health 保持 `live`。
- OpenClaw 服务器仍存在核心 `2026.6.10` 与部分要求 plugin API `>=2026.7.1` 的 npm 插件版本警告；bundled QQBot 当前仍可连接。核心升级应作为独立部署任务处理，不应与本次运行时恢复混在一起。

# Task Board

- [x] Fork `AstrBotDevs/AstrBot` 到 `AlexbeatsZ/AstrBot`。
- [x] 分析 OpenClaw fork 的 agy CLI 接入方式。
- [x] 实现 Docker 持久化的 Agy CLI 检测、校验下载、安装和更新。
- [x] 在供应商页面加入 Agy CLI 配置、状态管理和 Web 认证终端。
- [x] 实现无会话聊天、图片、模型映射、超时、取消和输出限制。
- [x] 更新 OpenAPI、生成客户端、中英文文档和自动化测试。
- [x] 通过 Ruff、20 项目标测试、前端类型检查和生产构建。
- [x] 检查暂存内容，以 `88876ada7` 提交并推送到 fork 的 `master`。
- [x] 诊断并恢复 2026-07-25 服务器故障：修正 AstrBot 代理、确认 `Sinm` 禁用、启动 OpenClaw WSL keepalive，并完成两边真实模型回归。
