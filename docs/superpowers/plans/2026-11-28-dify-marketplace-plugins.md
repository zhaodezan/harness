# Dify Marketplace Plugins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Browse Dify's official market in Harness and install and invoke compatible Python tool packages without a Dify service.

**Architecture:** The self-contained `backend/app/dify_plugins/` feature owns upstream HTTP, immutable package previews, an on-disk install catalog, a Linux Docker worker, REST routes and React components. Harness only wires the feature, existing secrets and ToolRegistry; selection reuses `selected_local_tools` and remains subject to the existing approval policy.

**Tech Stack:** Python 3.11+, FastAPI, httpx, PyYAML, Docker CLI and Python 3.12 SDK container, React/TypeScript, pytest.

---

### Task 1: Constrained Marketplace and package metadata

**Files:** `backend/app/dify_plugins/{__init__,marketplace,package}.py`, `backend/app/dify_plugins/tests/{test_marketplace,test_package}.py`, `pyproject.toml`.

- [ ] Add a pytest `httpx.MockTransport` search/resolve/download test and ZIP fixtures for a Python 3.12 Tool plus mismatched identity/version, unsafe/nested paths, duplicate/nonnormalized files, and unsupported features. Red: `python -m pytest backend/app/dify_plugins/tests/test_marketplace.py backend/app/dify_plugins/tests/test_package.py -q` (missing modules).
- [ ] Add client `Marketplace.search(query, page)`, `resolve(link_or_id)` and `download(identifier)`: official base URL; search `POST /api/v1/plugins/search/advanced`, exact lookup `POST /api/v1/plugins/batch`, bounded download `GET /api/v1/plugins/download?unique_identifier=…`; verify category, identifier and redirect origin. Use `with ZipFile(io.BytesIO(data)) as archive` and validate every regular member, declared and actual size, normalized path and duplicates before writing; `yaml.safe_load` manifest/provider/tool YAML. Map static tool parameters to Harness JSON Schema; extract only declared string credentials; reject unsupported capabilities. Keep dependencies a constrained list of registry package requirements only. Test success and failures in the same command.
- [ ] Add `PyYAML` to project dependencies, add feature tests to pytest's discovery, and package Docker worker assets through setuptools.

### Task 2: Standalone Docker runner

**Files:** `backend/app/dify_plugins/docker.py`, `backend/app/dify_plugins/runner/{Dockerfile,worker.py}`, `backend/app/dify_plugins/tests/{test_docker,test_worker}.py`.

- [ ] Define fake-engine/worker tests for clean Docker environment, no mounts, nonroot, CPU/memory/PID limits, deadline, no-network default and malformed/oversized output; test independent worker message handling for text/JSON and offline SDK session. Red: `python -m pytest backend/app/dify_plugins/tests/test_docker.py backend/app/dify_plugins/tests/test_worker.py -q`.
- [ ] Use `python:3.12-slim` present locally, a fixed Dockerfile and file-only build context. Run wheel-only pip against the public registry without a host config or user-supplied URLs; copy the validated source into the image after dependency installation. Worker uses `PluginRegistration(DifyPluginEnv())`, `get_tool_cls(provider, name)`, `Tool.from_credentials(credentials)` and `.invoke(arguments)` without Dify. Normalize results to text/JSON; turn missing session and non-supported results into bounded errors. Start with `docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges --user=65532:65532 --network=none`, plus bounded scratch and resource limits. Allow default Docker networking only after plugin-specific consent. Provide JSON via stdin, not flags or environment. Clean up timed-out containers and ignore raw stderr. Run targeted tests.

### Task 3: Durable catalog and existing Agent authorization

**Files:** `backend/app/dify_plugins/{manager,routes}.py`, `backend/app/dify_plugins/tests/test_routes.py`, `backend/app/main.py`.

- [ ] Add API tests using fake Docker and injected temporary data/credential stores: preview then confirm, install failure with no registration, duplicate/restart/uninstall, credential handling, per-Agent authorization and approval. Red: `python -m pytest backend/app/dify_plugins/tests/test_routes.py -q`.
- [ ] Add router `/api/dify-plugins`: `GET /market`, `POST /preview/market`, `POST /preview/file`, `POST /install`, `GET /installed`, `GET /tools`, `PUT /installed/{id}/settings`, `DELETE /installed/{id}`. Use bounded, expiring opaque preview tokens tied to exact package bytes; publish atomically only after image build/probe. Use the digest to identify image and plugin version. `ToolDefinition(name=f'dify_{…}', plugin_id=f'dify:{…}', risk='side_effect', …)` integrates with the existing filter through explicit Agent selection; other Agents cannot call it. Store credentials via injected `SecretStore`; route validation excludes input values, logs and public output do not include credentials. Restore the catalog and warn per plugin if its image is unavailable; uninstall unregisters first. Wire feature startup/router once in `main.py` and run API tests.

### Task 4: Embedded official market and local installation experience

**Files:** `backend/app/dify_plugins/ui/{DifyPlugins,DifyToolChoices}.tsx`, `backend/app/dify_plugins/ui/dify.css`, `frontend/src/pages/{PluginLibraryPage,AgentConfigPage}.tsx`, `frontend/vite.config.ts`.

- [ ] Wire `<DifyPlugins />` only into the existing Dify tab; keep DSH unchanged. Because the official Marketplace currently sends `frame-ancestors 'none'` and `X-Frame-Options: DENY`, show a fixed official link with `target="_blank" rel="noopener noreferrer"` instead of an iframe. Do not proxy or bypass framing restrictions.
- [ ] Implement market search and official-link/file preview separately from the iframe; show fixed version, tools, dependencies, credential and network requirements, consent and sanitized errors. Add six UI states: browsing, empty/no-results, preview, installing, installed, and failure/retry. Installed list edits credentials/network and uninstalls. Use `<DifyToolChoices selected={selectedLocalTools} onChange={setSelectedLocalTools} />` in the existing Agent form and keep source files in the feature directory; add Vite dev-server read permission for only the feature directory beyond the frontend root.
- [ ] Run `npm --prefix frontend run build`, `python -m pytest -q`, and `git diff --check`. Do not commit unless requested. Document observed market response and distinguish automated fixture verification from a real Linux Docker installation and browser test; the latter require connectivity/engine. Smoke test in the existing plugin library with the official frame plus link and local installation paths; use an independent market plugin and a dedicated provider test credential only if needed.
