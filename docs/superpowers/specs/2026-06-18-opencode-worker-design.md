# opencode Worker Adapter — Design

Date: 2026-06-18
Status: Approved (pending spec review)

## Goal

Add `opencode` (the SST opencode CLI) as a supported worker type in the Cairn
dispatcher, alongside the existing `claudecode`, `codex`, `pi`, and `mock`
workers. A dispatch config can then declare a worker of `type: opencode` and the
dispatcher will drive opencode non-interactively for `bootstrap`, `explore`, and
`reason` tasks.

## Background

Worker types are pluggable behind `WorkerDriver` (`workers/base.py`). Each driver
builds the argv for three operations — `build_healthcheck`, `build_execute`,
`build_conclude` — and parses the worker's output via `extract_session` /
`extract_response_text`. Drivers are registered in `workers/registry.py` and the
allowed types plus required env keys live in `dispatcher/config.py`. Adding a new
worker type is therefore a localized change: config type/env, a new adapter
module, an export, and a registry entry.

The reference adapter is `pi.py`: it routes all model traffic through a custom
provider that points at a Cairn-managed gateway (baseURL + apiKey + model from
the worker's `env`), and it runs the agent CLI in `--mode json` to parse a
structured event stream for the session id and assistant text.

## opencode CLI facts (verified against opencode 1.15.0 installed locally)

- `opencode run [message..]` runs non-interactively.
- `-m, --model provider/model` selects the model.
- `-s, --session <id>` continues an existing session; new runs omit it.
- `--format json` emits a raw JSON event stream (used to extract session id and
  assistant text). `default` format is human-formatted and not machine-parseable.
- `--dangerously-skip-permissions` auto-approves tool permissions.
- `--pure` runs without external plugins (leaner, more deterministic boot).
- Config can be injected inline via the `OPENCODE_CONFIG_CONTENT` env var (a JSON
  string), avoiding any on-disk config file.
- Custom OpenAI-compatible providers are supported via the
  `@ai-sdk/openai-compatible` npm package.
- Boot can be made lean/stable with `OPENCODE_DISABLE_AUTOUPDATE=1` and
  `OPENCODE_DISABLE_MODELS_FETCH=1`.

## Decisions

- **Model routing:** custom provider named `cairn` pointing at the Cairn gateway,
  consistent with `pi`/`codex`/`claudecode`. The model is fully proxied by Cairn.
- **Config injection:** inline via `OPENCODE_CONFIG_CONTENT` (no temp file). This
  is cleaner than `pi`'s `models.json` file-write.
- **Healthcheck:** opencode-level pong (option A), consistent with `pi`. It
  exercises the opencode binary + provider wiring + upstream end-to-end. The
  default healthcheck mode is `startup_only`, so this one-time cost is acceptable
  and surfaces provider-config errors early.

## Components

### 1. `dispatcher/config.py`

- Add `"opencode"` to the `WorkerType` literal.
- Add required env keys:
  ```python
  "opencode": ("OPENCODE_MODEL", "OPENCODE_BASE_URL", "OPENCODE_API_KEY"),
  ```
- Optional env `OPENCODE_PROVIDER_NPM` (defaults to `@ai-sdk/openai-compatible`)
  to allow anthropic-compatible gateways later. Not in the required set; read by
  the adapter with a default. No new validator needed beyond the existing
  required-key check.

### 2. `dispatcher/workers/adapters/opencode.py` — `OpenCodeDriver(WorkerDriver)`

`type_name = "opencode"`.

Helper `_config_json(worker)` builds the inline provider config:

```json
{"provider":{"cairn":{"npm":"@ai-sdk/openai-compatible",
  "options":{"baseURL":"<OPENCODE_BASE_URL>","apiKey":"<OPENCODE_API_KEY>"},
  "models":{"<OPENCODE_MODEL>":{"name":"<OPENCODE_MODEL>"}}}}}
```

Helper `_wrap(worker, opencode_argv)` wraps the call so the inline config and the
lean-boot env are injected with the process, mirroring `pi._wrap_with_models`:

```sh
exec env OPENCODE_CONFIG_CONTENT="$cfg" \
         OPENCODE_DISABLE_AUTOUPDATE=1 \
         OPENCODE_DISABLE_MODELS_FETCH=1 \
     opencode "$@"
```

(Config JSON passed as an argv positional, not interpolated into the script body,
to avoid shell-quoting issues — same pattern as `pi`.)

- `build_execute(worker, prompt, session)`:
  `opencode run --pure --format json --dangerously-skip-permissions -m cairn/<model> -- <prompt>`
  (new session: no `-s`). Returns `DriverResult(argv=_wrap(...), session=session)`.
- `build_conclude(worker, prompt, session)`: same argv plus `-s <session>`.
- `build_healthcheck(worker)`:
  `opencode run --pure -m cairn/<model> -- "Reply with exactly pong."`
- `extract_session(session, stdout, stderr)`: if `session` already set, return it;
  else iterate JSON events and return the session id. Reuse a `_iter_events`
  helper like `pi`'s.
- `extract_response_text(stdout, stderr)`: pull assistant text from the JSON event
  stream; fall back to `stdout` if not found.

**Verify-during-implementation (TDD):** the exact JSON event shape from
`opencode run --format json` — the field names carrying the session id and the
assistant text. Capture a real sample first (`opencode run --format json -- "hi"`),
then write `extract_session` / `extract_response_text` against the observed
schema. Do not guess field names.

### 3. `dispatcher/workers/adapters/__init__.py`

Export `OpenCodeDriver`; add to `__all__`.

### 4. `dispatcher/workers/registry.py`

Register `"opencode": OpenCodeDriver()`.

## Data flow

Unchanged from existing workers: the scheduler picks a worker, the task layer
calls `build_execute` / `build_conclude` to get argv, runs it in the worker
container, then calls `extract_session` and `extract_response_text` on the
captured stdout/stderr. The two-phase explore/bootstrap flow reuses the session
id returned by `extract_session` so `build_conclude` resumes the same opencode
session via `-s`.

## Error handling

- Missing env keys are caught by the existing `WorkerConfig.validate_env`
  required-key check (no new code).
- If the JSON stream can't be parsed, `extract_response_text` falls back to raw
  stdout (same defensive behavior as `pi`); `extract_session` returns `None`,
  which the existing task layer already handles.
- Healthcheck failure (non-zero exit / no pong) is handled by the existing
  healthcheck machinery; no adapter-specific handling.

## Testing

Follow the existing pi/codex test style.

- `test_config_and_adapters.py`:
  - A valid `opencode` worker config parses; required env keys enforced (missing
    `OPENCODE_MODEL` / `OPENCODE_BASE_URL` / `OPENCODE_API_KEY` raises).
  - `OPENCODE_PROVIDER_NPM` is optional and defaults correctly.
- `test_contracts_and_drivers.py`:
  - `build_execute` argv contains `opencode run`, `--format json`,
    `-m cairn/<model>`, the prompt after `--`, and no `-s` for a new session.
  - `build_conclude` argv includes `-s <session>`.
  - `build_healthcheck` argv runs the pong prompt.
  - The inline config JSON is well-formed and contains the provider/baseURL/
    apiKey/model from env.
  - `extract_session` returns the id from a sampled JSON event stream and is a
    no-op when a session is already supplied.
  - `extract_response_text` returns assistant text from a sampled stream and
    falls back to stdout otherwise.

## Documentation

- `dispatch.example.yaml`: add a commented `opencode` worker example with the
  three env keys.
- `docs/specs/dispatcher-design.md`: add `opencode` to the worker-driver list.

## Out of scope

- anthropic-compatible provider wiring beyond leaving the `OPENCODE_PROVIDER_NPM`
  hook (no separate adapter path now).
- Container image changes to install the opencode binary (the binary is assumed
  present in the worker container, same assumption as other CLI workers).
