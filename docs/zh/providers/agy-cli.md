# Agy CLI

AstrBot 可以把运行环境（通常是 Docker 容器）内已登录的 `agy` CLI 作为聊天模型提供商使用。此接入不会请求 HTTP 模型 API；AstrBot 将人格、历史上下文和当前消息整理为单次提示词，再调用：

```text
agy --model <model> --print-timeout 10m --print <prompt>
```

agy 使用自己的本地登录状态和原生工具。AstrBot 仍负责保存对话历史，因此每次调用都是无会话调用，不使用 `--continue` 或 `--conversation`。

## Docker 安装与认证

1. 在 WebUI 的「服务提供商」页面新增 **Agy CLI** 提供商源。
2. 在 Agy CLI 管理区点击「下载并安装」。AstrBot 会读取 Google 官方发布清单、校验 SHA-512，并把二进制原子安装到 `data/agy/bin`。
3. 点击「Web 认证」，在浏览器终端中完成 Google OAuth、主题、条款和工作区信任向导。
4. 获取模型列表，添加需要的模型，再执行模型可用性测试。

托管二进制、CLI HOME、认证配置和缓存都放在 `data/agy`。标准 Compose 已将 `./data` 挂载到 `/AstrBot/data`，因此更新镜像或重建容器后仍会保留。只在 Docker 宿主机安装 `agy` 对容器无效。

安装和更新都在运行中的容器内完成，不需要修改镜像或使用 root 权限。更新时会先校验新文件，再替换旧二进制；下载失败不会破坏现有版本。

## 配置

在 WebUI 中进入「服务提供商」→「新增服务提供商」→「聊天模型」，选择 **Agy CLI**。

- **模型 ID**：默认 `gemini-3.5-flash`。供应商页面会调用已登录环境中的 `agy models` 动态获取当前账户可用模型；点击「获取模型」后可以添加 Gemini、Claude、GPT 等 CLI 实际返回的模型及思考变体。
- **Agy 可执行文件**：默认 `agy` 时优先使用 `data/agy/bin` 中的托管版本，也可以填写绝对路径。
- **Agy 工作目录**：agy 原生文件工具的工作区；留空时使用 AstrBot 的数据工作区目录。
- **Agy 思考级别**：会映射到 agy 的模型变体，例如 `gemini-3.5-flash-high`。
- **Agy 系统提示词模式**：默认 `filtered`，会保留人格等指令并移除 AstrBot 专用工具/技能说明，避免与 agy 原生工具冲突。`full` 传递完整提示词，`none` 不传递系统提示词。
- **自动批准 Agy 工具权限**：默认关闭。启用后传递 `--dangerously-skip-permissions`，允许 headless 模式使用需要授权的文件和命令工具。只有在信任所有聊天用户及工作目录内容时才能启用；更安全的方式是在 agy `settings.json` 中配置精确的 `permissions.allow` 规则。
- **超时时间**：AstrBot 等待 agy 进程的秒数，默认 600 秒。
- **Agy 打印模式超时**：传给 `agy --print-timeout`，默认 `10m`。
- **代理地址**：设置后会作为 `HTTP_PROXY`、`HTTPS_PROXY` 及其小写形式传给 agy 子进程。
- **Agy 环境变量**：只添加到 agy 子进程的额外环境变量。

配置保存后，可以使用提供商页面的连通性测试验证。图片会被临时解析为本地文件，并通过 agy 支持的 `@路径` 语法传入；音频目前不支持。

## 工作方式与限制

- agy 原生工具始终由 agy 自己决定是否使用；不会生成 AstrBot Function Calling 指令。
- 同一 Agy CLI 提供商实例会串行执行请求，避免多个 CLI 进程争用同一登录和工作区状态。
- AstrBot 会限制 agy 的运行时间和输出大小，并在请求取消时终止子进程。
- `agy --print` 的输出通常在任务完成后一次返回，因此 AstrBot 的流式模式不会产生逐 token 输出。

## 故障排查

- **找不到命令**：在供应商页面点击「下载并安装」，或检查自定义绝对路径。
- **要求登录**：重新打开「Web 认证」；认证终端仅支持 Linux/Docker 部署。
- **重建容器后要求重新登录**：确认仍挂载 `./data:/AstrBot/data`，并且没有删除 `data/agy/home`。
- **地区或网络错误**：配置提供商的代理地址，并确认运行 AstrBot 的服务环境可以访问 agy 所需的网络。
- **输出为空**：先在相同用户、工作目录和网络环境中直接运行 `agy --print-timeout 1m --print "Reply exactly: AGY_OK"`。
- **headless 模式拒绝工具权限**：优先在 agy 设置中加入最小化的 `permissions.allow` 规则；确认风险可接受后，也可以启用「自动批准 Agy 工具权限」。
