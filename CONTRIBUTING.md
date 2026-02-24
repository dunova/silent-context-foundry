# Contributing

## Principles

- Prefer configuration over hard-coded paths.
- Keep background behavior quiet and predictable.
- Optimize for recovery and observability, not only happy-path startup.
- No secrets, tokens, or machine-specific absolute paths in commits.

## Local Validation

```bash
bash -n scripts/*.sh
python3 -m py_compile scripts/*.py
```

If you modify daemon/MCP behavior, also run a smoke test in a real local setup.

## Style

- Shell: POSIX-ish bash, `set -euo pipefail`
- Python: stdlib first, small targeted dependencies
- Comments: only for non-obvious operational logic
