# azure-codex-proxy

Proxy service for `codex` that acquires and refreshes Azure access tokens automatically, then forwards requests to Azure OpenAI through a local OpenAI-compatible endpoint.

Supported platforms: Linux, macOS, and Windows.

## Requirements

- `uv` installed. See Astral's [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
- `codex` installed and available on your `PATH`. Install it with `npm i -g @openai/codex` and see the official [Codex CLI setup guide](https://developers.openai.com/codex/cli).
- Access to an Azure OpenAI resource and deployment
- If not using uv: Python 3.13+ if you plan to run the project from source

## Quick start

Install this package with `uv`:

```bash
uv tool install git+https://github.com/martintb/azure-codex-proxy.git --prerelease=allow
```

Then start Codex through the local Azure proxy:

```bash
codex-azure
```


> [!WARNING]
> If you get an error `REQUESTS_CA_BUNDLE environment variable is specified with an invalid file path` try the following
> ```bash
> unset REQUESTS_CA_BUNDLE
> codex-azure
> ```

On first run, if these are not already configured, `codex-azure` prompts for them and stores them in its per-user platform config directory:

- Azure OpenAI resource URL (e.g., `"https://<your-resource>.cognitiveservices.azure.com"`)
- Azure OpenAI deployment name (e.g., `"gpt-5.4"`)

You can also set them explicitly with environment variables:

```bash
export AZURE_OPENAI_RESOURCE="https://<your-resource>.cognitiveservices.azure.com"
export AZURE_OPENAI_DEPLOYMENT="gpt-5.4"
```

Or store them once with config commands:

```bash
codex-azure config set-resource https://<your-resource>.cognitiveservices.azure.com
codex-azure config set-deployment gpt-5.4
```

After the proxy is ready, `codex-azure` starts `codex` and passes through extra arguments. Use `codex-azure run ...` when you need to force passthrough for flags or names that would otherwise belong to `codex-azure` itself.

> [!NOTE]
> If `codex-azure` fails during Azure authnetication, you can run the az login command directly after install
> ```bash
> uv tool install azure-cli
> uv tool update-shell   # if needed, once
> az login --use-device-code
> codex-azure
> ```

## What this does

`codex-azure` exists to let `codex` talk to Azure OpenAI without requiring you to manually fetch and refresh Azure access tokens.

It does three things:

1. Resolves your Azure OpenAI resource and deployment from environment variables or stored config.
2. Starts a local proxy if one is not already running.
3. Launches `codex` configured to use that local proxy as its model provider.

The proxy then:

- acquires tokens with Azure Identity
- refreshes tokens automatically before expiry
- retries once on upstream `401` after forcing a token refresh
- rewrites the local model alias to your real Azure deployment name before forwarding requests upstream

## Basic usage

Start the proxy if needed, then launch `codex`:

```bash
codex-azure
```

Pass arguments through to `codex`:

```bash
codex-azure chat
codex-azure --model gpt-5.4
codex-azure run --help
codex-azure run config
codex-azure <any other codex args>
```

Command routing precedence:

- `codex-azure --help` shows `codex-azure` help.
- `codex-azure config ...`, `codex-azure stop-proxy`, and `codex-azure restart-proxy` stay reserved for the proxy CLI.
- `codex-azure run ...` always passes everything after `run` to `codex`.
- Any other first argument is passed through to `codex` directly.

Multiple simultaneous `codex-azure` launches by the same user reuse the same background proxy. They do not start independent per-launch proxy daemons.

Stop the background proxy:

```bash
codex-azure stop-proxy
```

`stop-proxy` stops that shared background proxy for the current user, which also affects any concurrent `codex` sessions using it.

Restart the background proxy:

```bash
codex-azure restart-proxy
```

`restart-proxy` also operates on the shared singleton proxy rather than on an individual `codex-azure` invocation.

Run only the proxy server without launching `codex`:

```bash
uv run python -m codex_azure.server
```

## Configuration

### Configuration sources

Resource and deployment are resolved in this order:

1. Environment variables
2. Stored config in the per-user platform config directory
3. Interactive prompt, if stdin is a TTY

If stdin is not interactive and a required value is missing, the command fails with a clear error instead of prompting.

### Environment variables

Required for non-interactive use unless already stored:

```bash
export AZURE_OPENAI_RESOURCE="https://<your-resource>.openai.azure.com"
export AZURE_OPENAI_DEPLOYMENT="gpt-5.4"
```

Optional settings:

```bash
export AZURE_OPENAI_SCOPE="https://cognitiveservices.azure.com/.default"
export AZURE_OPENAI_PROXY_HOST="127.0.0.1"
export AZURE_OPENAI_PROXY_PORT="0"
export AZURE_OPENAI_TOKEN_REFRESH_SKEW_SECONDS="300"
```

What they mean:

- `AZURE_OPENAI_RESOURCE`: your Azure OpenAI resource base URL
- `AZURE_OPENAI_DEPLOYMENT`: the real Azure deployment name to send upstream
- `AZURE_OPENAI_SCOPE`: Azure token scope, defaulting to Cognitive Services
- `AZURE_OPENAI_PROXY_HOST`: local bind host for the proxy
- `AZURE_OPENAI_PROXY_PORT`: local bind port for the proxy; use `0` or leave unset to auto-select a free port
- `AZURE_OPENAI_TOKEN_REFRESH_SKEW_SECONDS`: how early to refresh a token before expiry

### Stored config commands

Show current stored values:

```bash
codex-azure config show-resource
codex-azure config show-deployment
```

Set values interactively or directly:

```bash
codex-azure config set-resource
codex-azure config set-resource https://<your-resource>.openai.azure.com
codex-azure config set-deployment
codex-azure config set-deployment gpt-5.4
```

Clear stored values:

```bash
codex-azure config clear-resource
codex-azure config clear-deployment
```

## Codex integration

When you run `codex-azure config set-resource` or `codex-azure config set-deployment`, the tool updates `~/.codex/config.toml` without deleting unrelated TOML content.

It ensures Codex points at the local proxy while keeping a real Codex model name:

```toml
model = "gpt-5.4"
model_provider = "azure-openai-proxy"

[model_providers.azure-openai-proxy]
name = "azure-openai-proxy"
env_key = "CODEX_AZURE_OPENAI_DUMMY_API_KEY"
base_url = "http://127.0.0.1:<dynamic-port>/openai/v1"
wire_api = "responses"
query_params = { api-version = "preview" }
stream_idle_timeout_ms = 1800000
stream_max_retries = 20
request_max_retries = 8
```

Important details:

- Codex keeps using a real model name like `gpt-5.4`, so built-in model metadata still applies.
- The proxy rewrites supported local model names to your real Azure deployment before forwarding the request.
- `codex-azure` exports a dummy value for `CODEX_AZURE_OPENAI_DUMMY_API_KEY` before launching `codex`, because authentication is handled by the local proxy rather than a static OpenAI-style API key.
- Existing unrelated keys and tables in `~/.codex/config.toml` are preserved.

## Authentication behavior

The proxy uses `AzureCliCredential`, which relies on an existing Azure CLI sign-in. The CLI checks whether Azure CLI is installed and already logged in. If `az` is available but not logged in, it runs:

```bash
az login --use-device-code
```

This works in regular terminals, bare SSH sessions, and VS Code remote terminals without needing browser forwarding. If stdin is not interactive, `codex-azure` tells you to run that command manually first.

If `codex` is missing from `PATH`, `codex-azure` exits early with an install hint pointing to the Codex CLI guide. Codex CLI currently supports macOS and Linux directly; on Windows, the official docs describe support as experimental and recommend WSL when possible.

## Local endpoints

By default the proxy listens on `127.0.0.1` and asks the OS for any free port. `codex-azure` rewrites its provider `base_url` to the active endpoint each time it starts or restarts the proxy.

For a given user account, that endpoint belongs to one shared background proxy. Starting `codex-azure` again while that proxy is healthy reuses it instead of launching a second proxy instance.

If you need a fixed port, set `AZURE_OPENAI_PROXY_PORT` explicitly. The active endpoints look like:

```text
http://127.0.0.1:<dynamic-port>/openai/v1/...
http://127.0.0.1:<dynamic-port>/healthz
```

The health endpoint returns whether the proxy can currently resolve configuration and obtain a valid token.

## Files and locations

- Stored proxy config:
  - Linux: `~/.config/codex-azure/config.json`
  - macOS: `~/Library/Application Support/codex-azure/config.json`
  - Windows: `%APPDATA%\codex-azure\config.json`
- Generated Codex config: `~/.codex/config.toml`
- Background proxy PID file:
  - Linux: `~/.cache/codex-azure/azure-openai-proxy.pid`
  - macOS: `~/Library/Caches/codex-azure/azure-openai-proxy.pid`
  - Windows: `%LOCALAPPDATA%\codex-azure\Cache\azure-openai-proxy.pid`
- Background proxy log file:
  - Linux: `~/.cache/codex-azure/azure-openai-proxy.log`
  - macOS: `~/Library/Caches/codex-azure/azure-openai-proxy.log`
  - Windows: `%LOCALAPPDATA%\codex-azure\Cache\azure-openai-proxy.log`
- Background proxy runtime state:
  - Linux: `~/.cache/codex-azure/azure-openai-proxy.json`
  - macOS: `~/Library/Caches/codex-azure/azure-openai-proxy.json`
  - Windows: `%LOCALAPPDATA%\codex-azure\Cache\azure-openai-proxy.json`

## How request rewriting works

Codex is configured to use a real model name such as `gpt-5.4` locally.

Before forwarding a JSON request upstream, the proxy checks the request body. If the request model matches a supported local model name, it rewrites the `model` field to your configured Azure deployment name.

That means:

- Codex can keep using built-in model metadata and defaults
- Azure still receives the real deployment name it expects

## Troubleshooting

### The command asks for resource or deployment every time

Check whether you are setting environment variables only for one shell session, or whether the stored config was cleared.

Inspect stored values:

```bash
codex-azure config show-resource
codex-azure config show-deployment
```

### The proxy fails to start

Check the background log:

```bash
tail -f ~/.cache/codex-azure/azure-openai-proxy.log
```

Also verify that the configured host and port are available.

### Authentication fails

Try logging in explicitly:

```bash
az login
```

Then restart the proxy:

```bash
codex-azure restart-proxy
```

If Azure CLI is unavailable or unsuitable, complete the interactive browser login flow when prompted by Azure Identity.

### Requests reach the proxy but Azure rejects them

Check:

- the resource URL is correct
- the deployment name exactly matches your Azure deployment
- your account has access to that Azure OpenAI resource
- the configured API version in Codex provider settings is compatible with your target endpoint

### Codex is not found

`codex-azure` launches `codex` from your `PATH`. On Unix it replaces the current process; on Windows it starts `codex` as a child process and exits with the same status code.

## Development notes

Create the local development environment with `uv`:

```bash
uv sync --extra dev
```

Run the proxy module directly during development:

```bash
uv run python -m codex_azure.server
```

## Resource configuration

You can still provide the resource directly with an environment variable:

```bash
export AZURE_OPENAI_RESOURCE="https://<your-resource>.openai.azure.com"
```

If `AZURE_OPENAI_RESOURCE` is not set, `codex-azure` will prompt for the resource URL the first time you run it and store that value in its per-user platform config directory.

Azure OpenAI still requires a real deployment name. Set it with:

```bash
export AZURE_OPENAI_DEPLOYMENT="gpt-5.4"
```

or store it once with:

```bash
codex-azure config set-deployment gpt-5.4
```

If `AZURE_OPENAI_DEPLOYMENT` is not set, `codex-azure` will also prompt for the deployment name on first run and store it in the same config file.

The proxy provider is only used inside Codex config; the proxy rewrites supported local model names to your configured Azure deployment before forwarding requests upstream.

When you run `codex-azure config set-resource`, it also updates `~/.codex/config.toml` without deleting unrelated TOML. It ensures these settings exist:

```toml
model = "gpt-5.4"
model_provider = "azure-openai-proxy"

[model_providers.azure-openai-proxy]
name = "azure-openai-proxy"
env_key = "CODEX_AZURE_OPENAI_DUMMY_API_KEY"
base_url = "http://127.0.0.1:<dynamic-port>/openai/v1"
wire_api = "responses"
query_params = { api-version = "preview" }
stream_idle_timeout_ms = 1800000
stream_max_retries = 20
request_max_retries = 8
```

Existing keys and tables in `~/.codex/config.toml` are preserved.
`codex-azure` also exports a dummy value for `CODEX_AZURE_OPENAI_DUMMY_API_KEY` before launching `codex`, because authentication is handled by the local proxy rather than a static OpenAI-style API key.

You can manage the stored value explicitly:

```bash
codex-azure config show-resource
codex-azure config set-resource
codex-azure config set-resource https://<your-resource>.openai.azure.com
codex-azure config clear-resource
codex-azure config show-deployment
codex-azure config set-deployment gpt-5.4
codex-azure config clear-deployment
```

Optional settings:

```bash
export AZURE_OPENAI_SCOPE="https://cognitiveservices.azure.com/.default"
export AZURE_OPENAI_DEPLOYMENT="gpt-5.4"
export AZURE_OPENAI_PROXY_HOST="127.0.0.1"
export AZURE_OPENAI_PROXY_PORT="0"
export AZURE_OPENAI_TOKEN_REFRESH_SKEW_SECONDS="300"
```

## Run

```bash
codex-azure
```

`codex-azure` checks whether the local proxy is already healthy, starts it in the background if needed, and then execs `codex` with any passthrough arguments. Use `codex-azure run ...` to forward names or flags that would otherwise be handled by `codex-azure`.

If you run `codex-azure` in multiple terminals as the same user, those sessions share the same background proxy and local proxy auth token.

The proxy exposes a dynamically chosen local endpoint by default:

```text
http://127.0.0.1:<dynamic-port>/openai/v1/...
http://127.0.0.1:<dynamic-port>/healthz
```

Authentication uses Azure CLI first and falls back to an interactive browser login.

## Run only the proxy

```bash
uv run python -m codex_azure.server
```
