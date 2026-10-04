import asyncio
import json

import httpx
import pytest

import main
from services.electricity import (
    AcademicAccountPool,
    ElectricityAccount,
    ElectricityAccountPool,
    ElectricityService,
)
from services.electricity_cache import DailyElectricityCache


@pytest.mark.parametrize("pool_type", [AcademicAccountPool, ElectricityAccountPool])
async def test_account_crud_is_scoped_by_school_and_preserves_legacy(
    pool_type, tmp_path
):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": [{"xh": "same", "pwd": "legacy"}]}))
    hnucm = pool_type(path, school="HNUCM")
    other = pool_type(path, school="OTHER")
    assert await hnucm.list_accounts() == ["same"]
    assert await other.list_accounts() == []
    await other.add_account("same", "other-secret")
    await hnucm.update_account("same", new_username=None, password="updated-legacy")
    assert (await other.rotated_accounts())[0].password == "other-secret"
    records = json.loads(path.read_text())["accounts"]
    assert {(record["school"], record["xh"]) for record in records} == {
        ("HNUCM", "same"),
        ("OTHER", "same"),
    }
    await hnucm.delete_account("same")
    assert await hnucm.list_accounts() == []
    assert await other.list_accounts() == ["same"]
    await asyncio.gather(
        hnucm.add_account("new", "one"), other.add_account("new", "two")
    )
    assert await hnucm.list_accounts() == ["new"]
    assert await other.list_accounts() == ["same", "new"]


async def test_reading_cache_and_cookie_keys_include_school():
    class Redis:
        def __init__(self):
            self.values = {}

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, *, ex):
            self.values[key] = value

    redis = Redis()
    hnucm = DailyElectricityCache(school="HNUCM", redis_client=redis)
    other = DailyElectricityCache(school="OTHER", redis_client=redis)
    await hnucm.set("hanpu", "06417", {"balance": 10})
    assert await other.get("hanpu", "06417") is None
    await other.set("hanpu", "06417", {"balance": 20})
    assert await hnucm.get("hanpu", "06417") == {"balance": 10}
    assert await other.get("hanpu", "06417") == {"balance": 20}
    assert (
        ElectricityAccount("same", "pwd", "HNUCM").cache_key
        != ElectricityAccount("same", "pwd", "OTHER").cache_key
    )


def test_catalog_and_collection_files_are_scoped_by_school(tmp_path, monkeypatch):
    monkeypatch.setenv("ELECTRICITY_ROOM_CATALOG_FILE", str(tmp_path / "rooms.json"))
    monkeypatch.setenv(
        "ELECTRICITY_COLLECTION_STATE_FILE", str(tmp_path / "state.json")
    )
    hnucm = ElectricityService(
        "https://example",
        ElectricityAccountPool(tmp_path / "accounts.json", school="HNUCM"),
    )
    other = ElectricityService(
        "https://example",
        ElectricityAccountPool(tmp_path / "accounts.json", school="OTHER"),
    )
    assert hnucm.catalog_path != other.catalog_path
    assert hnucm.collection_state_path != other.collection_state_path
    hnucm._write_room_catalog({"school": "HNUCM", "rooms": []})
    other.catalog_path = hnucm.catalog_path
    assert other.get_room_catalog() is None
    hnucm._write_collection_state({"date": "2026-10-04", "next_index": 1})
    other.collection_state_path = hnucm.collection_state_path
    assert other._read_collection_state() is None


async def test_academic_pool_crud_rejects_unimplemented_school(monkeypatch):
    monkeypatch.setenv("CRAWLER_INTERNAL_TOKEN", "test-token")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    ) as client:
        for method, path, body in [
            ("GET", "", None),
            ("POST", "", {"xh": "same", "pwd": "secret"}),
            ("PUT", "/same", {"pwd": "changed"}),
            ("DELETE", "/same", None),
        ]:
            response = await client.request(
                method, "/api/v1/academic/accounts" + path + "?school=OTHER", json=body
            )
            assert response.status_code == 422
        response = await client.post(
            "/api/v1/academic/accounts",
            json={"school": "OTHER", "xh": "same", "pwd": "secret"},
        )
        assert response.status_code == 422


def test_academic_pools_are_cached_separately_by_school(tmp_path, monkeypatch):
    monkeypatch.setenv("ACADEMIC_ACCOUNTS_FILE", str(tmp_path / "accounts.json"))
    main._cached_academic_account_pool.cache_clear()
    try:
        hnucm = main._cached_academic_account_pool("HNUCM")
        other = main._cached_academic_account_pool("OTHER")
        assert hnucm is main._cached_academic_account_pool("HNUCM")
        assert other is not hnucm
        assert other.school == "OTHER"
    finally:
        main._cached_academic_account_pool.cache_clear()
