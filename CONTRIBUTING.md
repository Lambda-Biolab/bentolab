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
- `main` is protected -- all changes go through PRs.
- Include test coverage for new protocol commands or client behavior.
- Update `AGENTS.md` if you change architecture or add new modules.

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
