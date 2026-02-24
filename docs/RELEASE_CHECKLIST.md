# Release Checklist

- [ ] `rg` scan confirms no secrets / local absolute paths
- [ ] `bash -n scripts/*.sh`
- [ ] `python3 -m py_compile scripts/*.py`
- [ ] README updated for new env vars / behavior
- [ ] healthcheck output reviewed on a real machine
- [ ] GitHub Actions verification passes
- [ ] tag + release notes created
