"""Phase 0: tdxdata vendoring / packaging 守卫（离线，不联网）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _pkg_dir() -> Path:
    import tdxdata

    return Path(tdxdata.__file__).resolve().parent


def test_version_is_pinned():
    import tdxdata

    assert tdxdata.__version__ == "0.3.0"


def test_package_data_present():
    pkg = _pkg_dir()
    assert (pkg / "data" / "products.json").is_file()
    assert (pkg / "_engine" / "catalog" / "page_cmd_params.json").is_file()
    assert (pkg / "_engine" / "catalog" / "pb_specs.json").is_file()
    assert (pkg / "_engine" / "catalog" / "raw_args.json").is_file()
    assert (pkg / "py.typed").is_file()


def test_license_and_vendored_note_present():
    pkg = _pkg_dir()
    license_text = (pkg / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "liu1561089752" in license_text
    note = (pkg / "VENDORED.md").read_text(encoding="utf-8")
    assert "a276f57586a8d006f658d2e1d37faa10e681f695" in note


def test_product_catalog_count():
    pkg = _pkg_dir()
    payload = json.loads((pkg / "data" / "products.json").read_text(encoding="utf-8"))
    products = payload.get("products", {})
    assert len(products) == 421
    # 抽查几个已知产品
    for name in ("holiday", "lhb", "north", "xjjj", "fin"):
        assert name in products


def test_client_loads_catalog_without_network():
    import tdxdata

    client = tdxdata.Client()
    try:
        assert len(client.products()) == 421
        assert len(client.domains()) == 17
        # 产品目录是静态的，不触发 TCP
        assert any(p.name == "holiday" for p in client.products())
    finally:
        client.close()


def test_imports_public_api():
    import tdxdata

    assert hasattr(tdxdata, "Client")
    assert hasattr(tdxdata, "AsyncClient")
    assert hasattr(tdxdata, "Adjust")
    assert hasattr(tdxdata, "TdxError")
