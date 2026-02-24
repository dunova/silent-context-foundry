# Security Policy

## Supported Scope

This repository contains integration scripts and deployment helpers only.

## Reporting a Vulnerability

Please do **not** open a public issue for secrets exposure or active exploitation paths.

Report privately with:
- affected script/path
- reproduction steps
- impact
- suggested mitigation (if any)

## Local Secret Hygiene

Before sharing logs or configs:
- remove API keys / tokens / passwords
- redact hostnames and internal IPs if needed
- avoid uploading `ov.conf` unless fully scrubbed

## Threat Model (Practical)

This repo assumes:
- a trusted local machine
- untrusted input inside terminal histories and prompts
- need to avoid accidental secret propagation into shared memory

Controls included:
- shell-history secret redaction patterns in daemon
- file permission checks (`0600`) in healthcheck
- `trust_env=False` on HTTP clients in MCP code
