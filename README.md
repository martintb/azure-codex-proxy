# azure-codex-proxy

Proxy service for codex-cli that acquires and refreshes Azure access tokens automatically.

## Install

```bash
pip install .
```

This installs the `codex-azure` shell command.

## Resource configuration

You can still provide the resource directly with an environment variable:

```bash
export AZURE_OPENAI_RESOURCE="https://<your-resource>.openai.azure.com"
```

If `AZURE_OPENAI_RESOURCE` is not set, `codex-azure` will prompt for the resource URL the first time you run it and store that value in `~/.config/codex-azure/config.json`.

Azure OpenAI still requires a real deployment name. Set it with:

```bash
export AZURE_OPENAI_DEPLOYMENT="gpt-5.4"
```

or store it once with:

```bash
codex-azure config set-deployment gpt-5.4
```

The local alias `azure-openai-proxy` is only used inside Codex config; the proxy rewrites that alias to your configured Azure deployment before forwarding requests upstream.

When you run `codex-azure config set-resource`, it also updates `~/.codex/config.toml` without deleting unrelated TOML. It ensures these settings exist:

```toml
model = "azure-openai-proxy"
model_provider = "azure-openai-proxy"

[model_providers.azure-openai-proxy]
name = "azure-openai-proxy"
env_key = "CODEX_AZURE_OPENAI_DUMMY_API_KEY"
base_url = "http://127.0.0.1:43123/openai/v1"
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
export AZURE_OPENAI_PROXY_PORT="43123"
export AZURE_OPENAI_TOKEN_REFRESH_SKEW_SECONDS="300"
```

## Run

```bash
codex-azure
```

`codex-azure` checks whether the local proxy is already healthy, starts it in the background if needed, and then execs `codex` with any arguments you pass through.

The proxy exposes:

```text
http://127.0.0.1:43123/openai/v1/...
http://127.0.0.1:43123/healthz
```

Authentication uses Azure CLI first and falls back to an interactive browser login.

## Run only the proxy

```bash
python -m codex_azure.server
```
