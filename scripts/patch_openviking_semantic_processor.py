#!/usr/bin/env python3
"""Patch OpenViking semantic_processor for quiet degradation on unsupported VLM providers.

This is a local patch helper for installations where OpenViking's VLMFactory only
supports a subset of providers (for example only openai/volcengine), but the
runtime config uses another provider (for example gemini). Without this patch,
background semantic generation can spam logs with repeated errors.
"""

from pathlib import Path
import sys

TARGET = Path.home() / ".openviking_env" / "lib"


def find_target() -> Path | None:
    candidates = sorted(TARGET.glob("python*/site-packages/openviking/storage/queuefs/semantic_processor.py"))
    return candidates[-1] if candidates else None


def patch_text(src: str) -> str:
    if "Semantic VLM disabled:" in src:
        return src
    src = src.replace(
        "logger = get_logger(__name__)\n",
        "logger = get_logger(__name__)\n_UNSUPPORTED_VLM_PROVIDER_LOGGED: set[str] = set()\n",
    )
    marker = "        # Default to other\n        return FILE_TYPE_OTHER\n"
    helper = marker + "\n    def _get_supported_vlm_or_none(self):\n        \"\"\"Return VLM only when provider is supported by this build.\"\"\"\n        vlm = get_openviking_config().vlm\n        provider = getattr(vlm, \"provider\", None) or getattr(vlm, \"backend\", None)\n        if provider not in {\"openai\", \"volcengine\"}:\n            if provider not in _UNSUPPORTED_VLM_PROVIDER_LOGGED:\n                logger.info(\n                    \"Semantic VLM disabled: provider '%s' is unsupported by this OpenViking build; continuing with vectorization-only mode.\",\n                    provider,\n                )\n                _UNSUPPORTED_VLM_PROVIDER_LOGGED.add(provider)\n            return None\n        return vlm\n"
    src = src.replace(marker, helper, 1)
    src = src.replace("        vlm = get_openviking_config().vlm\n", "        vlm = self._get_supported_vlm_or_none()\n", 2)
    src = src.replace("            if not vlm.is_available():\n                logger.warning(\"VLM not available, using empty summary\")\n", "            if not vlm or not vlm.is_available():\n", 1)
    src = src.replace("        if not vlm.is_available():\n            logger.warning(\"VLM not available, using default overview\")\n", "        if not vlm or not vlm.is_available():\n", 1)
    return src


def main() -> int:
    p = find_target()
    if not p:
        print("semantic_processor.py not found under ~/.openviking_env/lib/python*/site-packages", file=sys.stderr)
        return 1
    original = p.read_text(encoding="utf-8")
    patched = patch_text(original)
    if patched == original:
        print(f"Already patched or no changes needed: {p}")
        return 0
    backup = p.with_suffix(p.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")
    p.write_text(patched, encoding="utf-8")
    print(f"Patched: {p}")
    print(f"Backup : {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
