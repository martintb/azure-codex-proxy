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

You can manage the stored value explicitly:

```bash
codex-azure config show-resource
codex-azure config set-resource
codex-azure config set-resource https://<your-resource>.openai.azure.com
codex-azure config clear-resource
```

Optional settings:

```bash
export AZURE_OPENAI_SCOPE="https://cognitiveservices.azure.com/.default"
export AZURE_OPENAI_PROXY_HOST="127.0.0.1"
export AZURE_OPENAI_PROXY_PORT="4000"
export AZURE_OPENAI_TOKEN_REFRESH_SKEW_SECONDS="300"
```

## Run

```bash
codex-azure
```

`codex-azure` checks whether the local proxy is already healthy, starts it in the background if needed, and then execs `codex` with any arguments you pass through.

The proxy exposes:

```text
http://127.0.0.1:4000/openai/v1/...
http://127.0.0.1:4000/healthz
```

Authentication uses Azure CLI first and falls back to an interactive browser login.

## Run only the proxy

```bash
python -m codex_azure.server
```
