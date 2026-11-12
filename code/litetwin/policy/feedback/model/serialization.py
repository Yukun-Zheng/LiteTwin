"""Save and load LiteTwin sidecars without duplicating DynaMAC arrays."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ...dynamac import DynaMAC
from ..config import LiteTwinPolicyConfig
from .task_model import LiteTwinTaskModel

BUNDLE_SCHEMA = "litetwin.policy_bundle.v1"


def save_policy_bundle(
    directory: str | Path,
    *,
    task_models: Mapping[str, LiteTwinTaskModel],
    config: LiteTwinPolicyConfig,
    summary: Mapping[str, Any],
) -> Path:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    arms = sorted(task_models)
    for arm in arms:
        task_models[arm].save(root / f"{arm}.npz")
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "arms": arms,
        "base_policy_digests": {
            arm: task_models[arm].base_policy.identity_digest() for arm in arms
        },
        "config": config.to_dict(),
        "summary": dict(summary),
    }
    path = root / "policy.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_policy_bundle(
    directory: str | Path,
    *,
    base_policies: Mapping[str, DynaMAC],
) -> tuple[dict[str, LiteTwinTaskModel], LiteTwinPolicyConfig, dict[str, Any]]:
    root = Path(directory)
    manifest = json.loads((root / "policy.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != BUNDLE_SCHEMA:
        raise ValueError("Unsupported LiteTwin policy bundle schema")
    arms = tuple(manifest.get("arms", ()))
    if set(arms) != set(base_policies):
        raise ValueError("LiteTwin policy bundle and base policies cover different arm sets")
    expected = manifest.get("base_policy_digests", {})
    if any(base_policies[arm].identity_digest() != expected.get(arm) for arm in arms):
        raise ValueError("LiteTwin policy bundle is bound to a different DynaMAC checkpoint")
    models = {
        arm: LiteTwinTaskModel.load(root / f"{arm}.npz", base_policies[arm]) for arm in arms
    }
    config = LiteTwinPolicyConfig.from_mapping(manifest["config"])
    summary = manifest.get("summary", {})
    if not isinstance(summary, dict):
        raise TypeError("LiteTwin policy bundle summary must be an object")
    return models, config, summary


__all__ = ["BUNDLE_SCHEMA", "load_policy_bundle", "save_policy_bundle"]
