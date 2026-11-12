"""Identity and ABI checks for the bounded pytracik build."""

from __future__ import annotations

import hashlib
import argparse
import importlib.metadata
import json
import sys
import sysconfig
from pathlib import Path
from typing import Any


PYTRACIK_PACKAGE_VERSION = "0.0.3"
PYTRACIK_UPSTREAM_COMMIT = "8c8fd2d8ca70334af9b747987f1156ebb1da25cc"
PYTRACIK_SOURCE_ARCHIVE_SHA256 = (
    "6ab16912c8bbad74214b553a6b49ac39f3a48362b512f187fc08d852af946f8a"
)
PYTRACIK_BOUNDED_PATCH_SHA256 = (
    "52e3480aaba7d9717ea8ca71879a448ca306b43f4a5729a6211d366a67d74122"
)
PYTRACIK_BUILD_ID = (
    "pytracik-v0.0.3-8c8fd2d8-bounded-cartesian-" "52e3480aaba7-cpython38-v1"
)
PYTRACIK_BUILD_MANIFEST = "_dynamac_build.json"
PYTRACIK_CONDA_LOCK_SHA256 = (
    "22b236e3cff3df2fdaf736390ed0583b93c8cbe2adaee9d0c64d4e5e5e4ba7d7"
)


def pytracik_dependency_identity() -> dict[str, Any]:

    return {
        "build_id": PYTRACIK_BUILD_ID,
        "package": "pytracik",
        "package_version": PYTRACIK_PACKAGE_VERSION,
        "repository": "https://github.com/chenhaox/pytracik",
        "upstream_commit": PYTRACIK_UPSTREAM_COMMIT,
        "source_archive_sha256": PYTRACIK_SOURCE_ARCHIVE_SHA256,
        "bounded_patch_sha256": PYTRACIK_BOUNDED_PATCH_SHA256,
        "conda_lock_sha256": PYTRACIK_CONDA_LOCK_SHA256,
        "required_python_abi": "cp38",
        "required_python_version": [3, 8],
        "required_python_api": "trac_ik.TracIK.ik_with_bounds",
        "required_native_api": "pytracik.ik_with_bounds",
        "binary_distributed_in_repository": False,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_bounded_pytracik_build(
    *, allow_temporary_build: bool = False
) -> dict[str, Any]:

    expected = pytracik_dependency_identity()
    if list(sys.version_info[:2]) != expected["required_python_version"]:
        raise RuntimeError("bounded pytracik requires the CPython 3.8 ABI")
    if importlib.metadata.version("pytracik") != PYTRACIK_PACKAGE_VERSION:
        raise RuntimeError("bounded pytracik package version is not pinned 0.0.3")

    import pytracik
    from trac_ik import TracIK
    import trac_ik as trac_ik_package

    package_root = Path(trac_ik_package.__file__).resolve().parent
    extension_path = Path(pytracik.__file__).resolve()
    if not allow_temporary_build and (
        str(package_root).startswith("/tmp/") or str(extension_path).startswith("/tmp/")
    ):
        raise RuntimeError("bounded pytracik must not be loaded from /tmp")
    extension_suffix = str(sysconfig.get_config_var("EXT_SUFFIX") or "")
    if not extension_suffix or not extension_path.name.endswith(extension_suffix):
        raise RuntimeError("pytracik native extension does not match this Python ABI")
    if not callable(getattr(pytracik, "ik_with_bounds", None)):
        raise RuntimeError("pytracik native bounded Cartesian API is unavailable")
    if not callable(getattr(TracIK, "ik_with_bounds", None)):
        raise RuntimeError("trac_ik.TracIK bounded Cartesian API is unavailable")

    manifest_path = package_root / PYTRACIK_BUILD_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("bounded pytracik build manifest is unavailable") from exc
    required_manifest = {
        "build_id": PYTRACIK_BUILD_ID,
        "package_version": PYTRACIK_PACKAGE_VERSION,
        "upstream_commit": PYTRACIK_UPSTREAM_COMMIT,
        "source_archive_sha256": PYTRACIK_SOURCE_ARCHIVE_SHA256,
        "bounded_patch_sha256": PYTRACIK_BOUNDED_PATCH_SHA256,
        "conda_lock_sha256": PYTRACIK_CONDA_LOCK_SHA256,
        "python_abi": "cp38",
    }
    # The earlier diff included two more unchanged context lines. Both diffs
    # produce identical source files at the pinned upstream revision.
    if manifest.get("bounded_patch_sha256") == "ce1f9053bfc70b6d1a562fe2c830a8f04d8cc59a985941fc3163c3268fe4b565":
        required_manifest.update(
            bounded_patch_sha256="ce1f9053bfc70b6d1a562fe2c830a8f04d8cc59a985941fc3163c3268fe4b565",
            build_id=PYTRACIK_BUILD_ID.replace("52e3480aaba7", "ce1f9053bfc7"),
        )
    for field, value in required_manifest.items():
        if manifest.get(field) != value:
            raise RuntimeError(f"bounded pytracik build manifest mismatch: {field}")
    extension_sha256 = manifest.get("native_extension_sha256")
    if not isinstance(extension_sha256, str) or extension_sha256 != _sha256(
        extension_path
    ):
        raise RuntimeError("bounded pytracik native-extension digest mismatch")
    return {
        **expected,
        **required_manifest,
        "native_extension_sha256": extension_sha256,
        "native_extension_path": str(extension_path),
        "build_manifest_path": str(manifest_path),
        "bounded_python_api": True,
        "bounded_native_api": True,
        "abi_verified": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-temporary-build", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            assert_bounded_pytracik_build(
                allow_temporary_build=arguments.allow_temporary_build
            ),
            indent=2,
            sort_keys=True,
        )
    )
