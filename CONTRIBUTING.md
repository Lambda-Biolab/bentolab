# Contributing

## Commit Format

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: add touchdown PCR support
fix: handle chunked NUS TX responses
docs: update protocol command reference
test: add profile upload round-trip test
chore: bump ruff to 0.5
```

## Pre-Commit Checklist

Before opening a PR, run:

```bash
make validate
```

This runs format check, lint, type check, complexity analysis, and the full
test suite (excluding hardware tests).

## Pull Request Guidelines

- One logical change per PR.
- Branch from `main`, target `main`.
- `main` is protected -- all changes go through PRs. Never push directly,
  even for "trivial" fixes.
- Include test coverage for new protocol commands or client behavior.
- Update `AGENTS.md` if you change architecture or add new modules.

## Quality Gates

The gates defined in `pyproject.toml` (`ruff`, `pyright`, `complexipy`) and
enforced by `make validate` are non-negotiable. When a gate fires:

- **Fix the underlying bug.** If pyright reports an Optional access, add the
  None-guard. If it reports a missing attribute, rename the call. The gates
  exist to catch real defects -- silencing them defeats the purpose.
- **Do not weaken the gate config** (downgrading errors to warnings,
  lowering severity, broadening excludes) as a shortcut. Config changes
  require an explicit justification in the PR description and are reviewed
  with the same scrutiny as code changes.
- **Targeted suppressions are a last resort.** A single `# noqa: <RULE>` or
  `# type: ignore[<code>]` on one line is acceptable when the rule is
  genuinely wrong for that site; it must be accompanied by a comment
  explaining *why*. File-level or project-level suppressions should be
  avoided.
- **Do not introduce per-file excludes in `[tool.pyright]` or
  `[tool.ruff.lint.per-file-ignores]`** without PR-body justification. The
  existing `tools/*` C901 exemption is the only standing carve-out, because
  debug scripts legitimately dispatch on many branches.

## Dependencies

- **Core runtime dependency** is `bleak` only. Anything else belongs in the
  `[tools]` optional extra.
- **Optional dependencies** (e.g. `aiohttp` in `wifi_client.py`) must be
  imported lazily inside the function that needs them, not at module
  top level, so a core install without `[tools]` never fails to import.

## Code Style

- **Formatter/Linter**: ruff (line length 100, target py311)
- **Type checker**: pyright (basic mode)
- **Docstrings**: Google style
- **Imports**: sorted by ruff (`I` rule)

## Testing

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = "auto"`)
- **BLE mocking**: Mock `bleak` at the boundary. Never require a physical
  device for the default test suite.
- **Hardware tests**: Mark with `@pytest.mark.hardware`. These are excluded
  from `make test` and CI by default.
- **Protocol tests**: Encode/decode tests use raw bytes from HCI captures --
  these are the most valuable regression tests.

## Development Setup

```bash
make setup           # Create venv + install deps
make validate        # Verify everything passes
```
