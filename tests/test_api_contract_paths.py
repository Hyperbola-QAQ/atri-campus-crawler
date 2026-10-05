"""真实 ASGI 路由的鉴权、输入与并发重复写入。"""

import asyncio

import httpx
import pytest

import main
from services.electricity import AcademicAccountPool


@pytest.fixture
async def api(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAWLER_INTERNAL_TOKEN", "contract-token")
    accounts_file = tmp_path / "accounts.json"
    accounts_file.write_text('{"accounts": []}', encoding="utf-8")
    pool = AcademicAccountPool(accounts_file)

    async def dependency():
        return pool

    main.app.dependency_overrides[main.get_academic_account_pool] = dependency
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        yield client
    main.app.dependency_overrides.pop(main.get_academic_account_pool, None)


@pytest.mark.parametrize(
    "authorization", [None, "Bearer wrong", "Basic contract-token", "Bearer "]
)
async def test_invalid_auth_does_not_write(api, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = await api.post(
        "/api/v1/academic/accounts",
        headers=headers,
        json={"xh": "user", "pwd": "secret"},
    )
    assert response.status_code == 401
    response = await api.get(
        "/api/v1/academic/accounts", headers={"Authorization": "Bearer contract-token"}
    )
    assert response.json()["accounts"] == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"xh": "user"},
        {"pwd": "secret"},
        {"xh": None, "pwd": "secret"},
        {"xh": "", "pwd": "secret"},
    ],
)
async def test_missing_empty_null_parameters(api, body):
    response = await api.post(
        "/api/v1/academic/accounts",
        headers={"Authorization": "Bearer contract-token"},
        json=body,
    )
    assert response.status_code == 422


async def test_parallel_duplicate_creation_has_one_winner(api):
    headers = {"Authorization": "Bearer contract-token"}
    results = await asyncio.gather(
        *(
            api.post(
                "/api/v1/academic/accounts",
                headers=headers,
                json={"xh": "same-user", "pwd": "never-return-secret"},
            )
            for _ in range(30)
        )
    )
    statuses = [response.status_code for response in results]
    assert statuses.count(201) == 1
    assert statuses.count(409) == 29
    response = await api.get("/api/v1/academic/accounts", headers=headers)
    assert len(response.json()["accounts"]) == 1
    assert "never-return-secret" not in response.text
    assert (
        await api.delete("/api/v1/academic/accounts/same-user", headers=headers)
    ).status_code == 204
    assert (
        await api.delete("/api/v1/academic/accounts/same-user", headers=headers)
    ).status_code == 404


async def test_unconfigured_auth_is_unavailable(api, monkeypatch):
    monkeypatch.delenv("CRAWLER_INTERNAL_TOKEN")
    assert (await api.get("/api/v1/academic/accounts")).status_code == 503
