# azure-codex-proxy

Proxy service for `codex` that acquires and refreshes Azure access tokens automatically, then forwards requests to Azure OpenAI through a local OpenAI-compatible endpoint.

Supported platforms: Linux, macOS, and Windows.

## Quick start

Install Codex first by following the official [Codex CLI setup guide](https://developers.openai.com/codex/cli).

The install this package using pip:

```bash
pip install git+https://github.com/tbm/azure-codex-proxy.git
```

Once installed, you should be able to start the codex session via:

Run:

```bash
codex-azure
```

On first run, if these are not already configured, `codex-azure` will prompt for them and store them in its per-user platform config directory:

- Azure OpenAI resource URL (e.g., "https://<your-resource>.cognitiveservices.azure.com")
- Azure OpenAI deployment name (e.g., "gpt-5.4")

You can also set them explicitly with environment variables:

```bash
export AZURE_OPENAI_RESOURCE="https://<your-resource>.cognitiveservices.azure.com"
export AZURE_OPENAI_DEPLOYMENT="gpt-5.4"
```

Or store them once with config commands:

```bash
codex-azure config set-resource https://<your-resource>.openai.azure.com
codex-azure config set-deployment gpt-5.4
```

After the proxy is ready, `codex-azure` starts `codex` and passes through any extra arguments.

## Requirements

- Python 3.13+
- `codex` installed and available on your `PATH`
- Access to an Azure OpenAI resource and deployment
- Azure CLI installed with `az login`, or the ability to complete an interactive browser login

Package dependencies are installed automatically by `pip install git+https://github.com/tbm/azure-codex-proxy.git`:

- `azure-identity`
- `fastapi`
- `httpx`
- `tomlkit`
- `uvicorn`


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

## Versioning

Package versions are derived from Git tags using Hatch VCS versioning.

- Release tags should use a `v` prefix, for example `v0.1.0`.
- Built package metadata resolves that tag to the semver version `0.1.0`.
- Untagged commits build as development versions on the `0.1.0` line until a matching release tag exists.
- If VCS metadata is unavailable entirely, builds fall back to `0.1.0`.

## Basic usage

Start the proxy if needed, then launch `codex`:

```bash
codex-azure
```

Pass arguments through to `codex`:

```bash
codex-azure --help
codex-azure chat
codex-azure <any other codex args>
```

Stop the background proxy:

```bash
codex-azure stop-proxy
```

Restart the background proxy:

```bash
codex-azure restart-proxy
```

Run only the proxy server without launching `codex`:

```bash
python -m codex_azure.server
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

The proxy uses Azure Identity with this credential chain:

1. `AzureCliCredential`
2. `InteractiveBrowserCredential`

The CLI also checks whether Azure CLI is installed and already logged in. If `az` is available but not logged in, it runs:

```bash
az login
```

If Azure CLI authentication is not available, the proxy can still fall back to an interactive browser login through Azure Identity.

## Local endpoints

By default the proxy listens on `127.0.0.1` and asks the OS for any free port. `codex-azure` rewrites its provider `base_url` to the active endpoint each time it starts or restarts the proxy.

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

Install in editable mode while working on the project:

```bash
pip install -e git+https://github.com/tbm/azure-codex-proxy.git
```

Run the proxy module directly during development:

```bash
python -m codex_azure.server
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

`codex-azure` checks whether the local proxy is already healthy, starts it in the background if needed, and then execs `codex` with any arguments you pass through.

The proxy exposes a dynamically chosen local endpoint by default:

```text
http://127.0.0.1:<dynamic-port>/openai/v1/...
http://127.0.0.1:<dynamic-port>/healthz
```

Authentication uses Azure CLI first and falls back to an interactive browser login.

## Run only the proxy

```bash
python -m codex_azure.server
```
