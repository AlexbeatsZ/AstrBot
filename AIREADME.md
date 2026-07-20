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

# Task Board

- [x] Fork `AstrBotDevs/AstrBot` 到 `AlexbeatsZ/AstrBot`。
- [x] 分析 OpenClaw fork 的 agy CLI 接入方式。
- [x] 实现 Docker 持久化的 Agy CLI 检测、校验下载、安装和更新。
- [x] 在供应商页面加入 Agy CLI 配置、状态管理和 Web 认证终端。
- [x] 实现无会话聊天、图片、模型映射、超时、取消和输出限制。
- [x] 更新 OpenAPI、生成客户端、中英文文档和自动化测试。
- [x] 通过 Ruff、20 项目标测试、前端类型检查和生产构建。
- [x] 检查暂存内容，以 `88876ada7` 提交并推送到 fork 的 `master`。
