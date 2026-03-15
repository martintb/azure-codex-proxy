# AGENTS.md

## Purpose
This repository contains `azure-codex-proxy`, a small Python package and CLI that starts a local FastAPI proxy, acquires Azure access tokens with Azure Identity, and launches `codex` pointed at that proxy. The codebase is intentionally small, security-sensitive, and should be changed conservatively.

## Working Style
- Make focused, minimal changes that solve the root problem.
- Preserve the current architecture unless the task explicitly calls for a redesign.
- Prefer simple, readable code over clever abstractions.
- Treat this repo as security-sensitive: avoid introducing new trust boundaries, secret leakage, or permissive defaults.
- Do not make unrelated refactors while working on a targeted task.

## Repository Layout
- `codex_azure/app.py`: FastAPI app and proxying logic.
- `codex_azure/cli.py`: CLI entrypoint, background process management, and `codex` launch integration.
- `codex_azure/config.py`: Local config storage, Codex TOML updates, and validation helpers.
- `codex_azure/server.py`: Uvicorn bootstrap.
- `tests/`: focused regression tests.
- `pyproject.toml`: package metadata and dependency definitions.

## Python and Tooling Requirements
- Always use `uv` for Python execution, dependency management, and test commands.
- Do not invoke `python`, `python3`, `pip`, or `pytest` directly unless the user explicitly asks for that or there is a documented exception.
- Preferred command forms:
  - `uv run python -m ...`
  - `uv run pytest ...`
  - `uv sync`
  - `uv add ...` or `uv remove ...` when dependencies need to change
- If you need to run a one-off Python snippet, use `uv run python - <<'PY'` rather than system Python.
- If dependencies appear to be missing, prefer `uv sync` over ad hoc installation commands.

## Testing and Validation
- Run the smallest relevant test set first, then broaden if needed.
- For this repo, prefer targeted commands such as:
  - `uv run pytest tests/test_security.py`
  - `uv run pytest`
- For syntax/import sanity checks, prefer:
  - `uv run python -m compileall codex_azure tests`
- Do not run formatters or broad validation commands unless they are relevant to the task or requested by the user.
- If a command fails because a tool is unavailable, first check whether it should have been run via `uv`.

## Security Expectations
- Assume local proxy, token handling, configuration files, and process management are security-sensitive.
- Never log secrets, bearer tokens, or auth headers.
- Avoid returning raw internal exceptions to HTTP clients unless the task explicitly requires it.
- Preserve or improve validation on network targets, local auth, and file permissions.
- Treat changes to `codex_azure/app.py`, `codex_azure/cli.py`, and `codex_azure/config.py` as security-relevant by default.

## Config and File Handling
- Prefer owner-only permissions for files that contain config, tokens, logs, or process metadata.
- Avoid changing config file structure unless necessary for the task.
- When updating TOML or JSON config, preserve unrelated user settings.

## Dependency Changes
- Keep dependencies minimal.
- When adding a dependency, prefer the smallest well-maintained option that fits the existing stack.
- Update `pyproject.toml` deliberately and avoid incidental dependency churn.

## Editing Guidance
- Match the existing style and keep functions small and direct.
- Avoid inline comments unless they add real value and the surrounding code uses them.
- Do not rename public-facing commands, env vars, or config keys without a strong reason.
- If behavior changes in a user-visible way, update documentation such as `README.md` when appropriate.

## Command Checklist
Before running commands, quickly check:
- Is this a Python command? If yes, use `uv run ...`.
- Is this a test command? If yes, use `uv run pytest ...`.
- Is there a narrower command that validates only the changed behavior?
- Am I about to modify security-sensitive behavior? If yes, verify the trust boundary and add or update tests.

## Good Examples
- `uv run pytest tests/test_security.py`
- `uv run python -m compileall codex_azure tests`
- `uv sync`

## Bad Examples
- `pytest tests/test_security.py`
- `python3 -m compileall codex_azure tests`
- `pip install -r requirements.txt`
