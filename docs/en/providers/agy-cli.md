# Agy CLI

AstrBot can use an authenticated `agy` CLI in its runtime environment (normally the Docker container) as a chat model provider. This integration does not call an HTTP model API. AstrBot formats the persona, conversation history, and current message into one prompt, then runs:

```text
agy --sandbox --agent astrbot --model <model> --print-timeout 10m --print <prompt>
```

Agy uses its own local login state, custom agent, skills, and native tools. AstrBot remains responsible for conversation history, so every invocation is stateless and does not use `--continue` or `--conversation`. By default, each UMO (platform, message type, and session ID) uses a separate working directory.

## Docker installation and authentication

1. Add an **Agy CLI** provider source on the Providers page.
2. Select **Download and install** in the Agy CLI management panel. AstrBot reads Google's official release manifest, verifies SHA-512, and atomically installs the binary under `data/agy/bin`.
3. Select **Web authentication** and finish Google OAuth, theme, terms, and workspace trust onboarding in the browser terminal.
4. Fetch the model list, add the required models, and run the model availability test.

The managed executable, CLI HOME, authentication profile, and cache all live under `data/agy`. The standard Compose mount maps `./data` to `/AstrBot/data`, so this state survives image upgrades and container recreation. Installing agy only on the Docker host does not make it available in the container.

Install and update operations run inside the active container without modifying the image or requiring root. A new binary is checksum-verified before it replaces the old one, so a failed download leaves the current version intact.

On Linux, Agy's native terminal sandbox uses user namespaces. When AstrBot runs in Docker, `unshare` must be permitted. `compose-with-shipyard.yml` configures `seccomp=unconfined` for the AstrBot service without adding Linux capabilities or creating another container for Agy. Add the same `security_opt` to custom Compose files; otherwise Agy logs `operation not permitted` and rejects headless commands.

## Configuration

In the WebUI, open **Providers** → **Add Provider** → **Chat Model**, then select **Agy CLI**.

- **Model ID**: defaults to `gemini-3.5-flash`. The provider page calls `agy models` in the authenticated environment to discover the models currently available to the account. Use **Fetch models** to add any Gemini, Claude, GPT, or thinking variant actually returned by the CLI.
- **Agy executable**: the default `agy` value prefers the managed binary in `data/agy/bin`; an absolute executable path is also accepted.
- **Agy working directory**: workspace for agy's native file tools. It defaults to AstrBot's data workspace directory.
- **Isolate Agy session workspaces**: enabled by default. It maps each UMO to a separate child directory so chats do not share project files.
- **Agy sandbox**: enabled by default and passes `--sandbox`. AstrBot also creates an `astrbot` custom agent, enables `proceed-in-sandbox`, and auto-approves only `command(*)`. A command that needs to fall back to unsandboxed execution is still denied in headless mode.
- **Agy thinking level**: maps to agy model variants such as `gemini-3.5-flash-high`.
- **Agy system prompt mode**: `filtered` is the default. It keeps persona instructions while removing AstrBot-only tool and skill instructions that would conflict with agy's native tools. `full` forwards everything; `none` omits the system prompt.
- **Auto-approve Agy tool permissions**: disabled by default. When enabled, it passes `--dangerously-skip-permissions`, allowing headless mode to use file and command tools that require approval. Enable it only when every chat user and the working directory are trusted. Precise `permissions.allow` rules in agy's `settings.json` are safer.
- **Timeout**: maximum number of seconds AstrBot waits for the agy process; defaults to 600.
- **Agy print timeout**: value passed to `agy --print-timeout`; defaults to `10m`.
- **Proxy**: forwarded to the subprocess as uppercase and lowercase HTTP/HTTPS proxy variables.
- **Agy environment variables**: extra variables added only to the agy subprocess.
- **AstrBot host tool allowlist**: only code, file, math rendering, and image delivery tools are exposed by default. Agy must return a strict structured request, which AstrBot validates before using its existing tool executor. Host tools run outside the Agy sandbox, so do not add shell, arbitrary file, browser, or administration tools.

After saving, use the provider availability test in the WebUI. Images are materialized as temporary local files and passed with agy's `@path` syntax. Audio is not currently supported.

## Behavior and limitations

- The terminal sandbox isolates command execution only. Chat privacy is separately enforced by AstrBot's selected context, per-UMO workspaces, never resuming Agy conversations, and explicit denials for Agy conversation and cache paths.
- AstrBot maintains an `astrbot` custom agent and `astrbot-host-rendering` skill in its isolated Agy HOME. Portable workspace skills can be added under `.agents/skills/<skill>/SKILL.md`.
- Agy decides when to use native tools. Only allowlisted rendering-oriented AstrBot tools are converted into Function Calling through the controlled bridge.
- Requests for one Agy CLI provider instance run serially to avoid multiple processes competing for the same login and workspace state.
- AstrBot enforces runtime and output limits and terminates the subprocess when a request is cancelled.
- `agy --print` normally returns output when the task finishes, so AstrBot streaming mode does not provide token-by-token deltas.

## Troubleshooting

- **Command not found**: select **Download and install** on the Providers page, or check the custom absolute path.
- **Sign-in required**: reopen **Web authentication**. The browser terminal is supported on Linux/Docker deployments.
- **Sign-in is lost after recreation**: verify that `./data:/AstrBot/data` is still mounted and that `data/agy/home` was not removed.
- **Region or network error**: configure the provider Proxy and verify connectivity from the AstrBot service environment.
- **Empty output**: under the same user, working directory, and network environment, run `agy --print-timeout 1m --print "Reply exactly: AGY_OK"` directly.
- **Headless tool permission denied**: prefer minimal `permissions.allow` rules in agy settings. If the risk is acceptable, enable **Auto-approve Agy tool permissions**.
- **Sandbox reports `operation not permitted`**: ensure the AstrBot service has `security_opt: [seccomp=unconfined]`. Do not mask sandbox startup failure with `--dangerously-skip-permissions`.
