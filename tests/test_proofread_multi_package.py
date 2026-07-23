"""Multi-package switching (per-request pkg carried via the kzocr_pkg cookie).

Covers the package registry, cookie-based current-package resolution, and the
/packages, /packages/switch, /packages/open routes added in commit 02802ca.
Reuses the _make_custom_db helper from test_proofread to build valid packages.

NOTE: kept ASCII-only on purpose (no CJK string literals) to avoid the
encoding gotcha described in feedback_cjk_write_encoding.md.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from test_proofread import _make_custom_db


def _client(app, cookies=None):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies=cookies or {},
    )


@pytest.mark.asyncio
class TestMultiPackageSwitch:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.dir = Path(tmp_path)
        self.pkg_a = self.dir / "alpha.db"
        self.pkg_b = self.dir / "beta.db"
        _make_custom_db(self.pkg_a, "TCM-PKG-A", lines_per_page=1, pages=1)
        _make_custom_db(self.pkg_b, "TCM-PKG-B", lines_per_page=1, pages=1)
        from kzocr.proofread.app import app_factory, COOKIE_NAME
        self.COOKIE = COOKIE_NAME
        self.app = app_factory(self.pkg_a, self.pkg_b)
        yield
        for p in (self.pkg_a, self.pkg_b):
            p.unlink(missing_ok=True)

    async def test_packages_route_lists_both_and_default(self):
        async with _client(self.app) as c:
            resp = await c.get("/packages")
            assert resp.status_code == 200
            data = resp.json()
            ids = [p["id"] for p in data["packages"]]
            assert "alpha" in ids
            assert "beta" in ids
            # first registered package is the default / current one
            assert data["current"] == "alpha"

    async def test_switch_package_sets_cookie_and_redirects(self):
        async with _client(self.app) as c:
            resp = await c.post("/packages/switch", data={"pkg_id": "beta"})
            assert resp.status_code == 303
            assert self.COOKIE in resp.cookies
            assert resp.cookies[self.COOKIE] == "beta"

    async def test_cookie_selects_current_package(self):
        async with _client(self.app) as c_default:
            r_default = await c_default.get("/")
            assert "TCM-PKG-A" in r_default.text
        async with _client(self.app, cookies={self.COOKIE: "beta"}) as c_beta:
            r_beta = await c_beta.get("/")
            assert "TCM-PKG-B" in r_beta.text
            # the other package must NOT leak into the selected view
            assert "TCM-PKG-A" not in r_beta.text

    async def test_open_package_registers_at_runtime(self):
        new_pkg = self.dir / "gamma.db"
        _make_custom_db(new_pkg, "TCM-PKG-C", lines_per_page=1, pages=1)
        async with _client(self.app) as c:
            resp = await c.post("/packages/open", data={"path": str(new_pkg)})
            assert resp.status_code == 303
            assert resp.cookies[self.COOKIE] == "gamma"
            r = await c.get("/packages")
            ids = [p["id"] for p in r.json()["packages"]]
            assert "gamma" in ids

    async def test_open_invalid_package_redirects_without_crash(self):
        async with _client(self.app) as c:
            resp = await c.post(
                "/packages/open", data={"path": "/nonexistent/x.db"},
                follow_redirects=False,
            )
            assert resp.status_code == 303
            r = await c.get("/packages")
            ids = [p["id"] for p in r.json()["packages"]]
            assert "x" not in ids

    async def test_stale_cookie_falls_back_to_default(self):
        # cookie points to a pkg that no longer exists (e.g. server restarted)
        async with _client(self.app, cookies={self.COOKIE: "ghost"}) as c:
            r = await c.get("/")
            assert r.status_code == 200
            assert "TCM-PKG-A" in r.text

    async def test_list_packages_handles_corrupt_db(self):
        # corrupt pkg_b after registration -> book_count must degrade to 0
        self.pkg_b.write_bytes(b"this is not a sqlite database")
        async with _client(self.app) as c:
            r = await c.get("/packages")
            data = r.json()
            for p in data["packages"]:
                if p["id"] == "beta":
                    assert p["book_count"] == 0

    async def test_concurrent_switch_is_request_scoped(self):
        # Two simultaneous requests with different cookies see different pkgs.
        async with _client(self.app, cookies={self.COOKIE: "alpha"}) as c_a:
            async with _client(self.app, cookies={self.COOKIE: "beta"}) as c_b:
                r_a, r_b = await asyncio.gather(c_a.get("/"), c_b.get("/"))
        assert "TCM-PKG-A" in r_a.text
        assert "TCM-PKG-B" in r_b.text


def test_register_collision_gets_hash_suffix(tmp_path):
    from kzocr.proofread.app import app_factory

    a = Path(tmp_path) / "sub1" / "dup.db"
    b = Path(tmp_path) / "sub2" / "dup.db"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    _make_custom_db(a, "TCM-DUP-A")
    _make_custom_db(b, "TCM-DUP-B")
    app = app_factory(a, b)

    async def _run():
        async with _client(app) as c:
            r = await c.get("/packages")
            return [p["id"] for p in r.json()["packages"]]

    ids = asyncio.run(_run())
    assert "dup" in ids
    assert any(i.startswith("dup_") for i in ids)


def test_main_no_db_returns_1():
    from kzocr.proofread.app import main

    assert main([]) == 1


def test_main_empty_books_dir_returns_1(tmp_path):
    from kzocr.proofread.app import main

    assert main(["--books-dir", str(tmp_path)]) == 1
