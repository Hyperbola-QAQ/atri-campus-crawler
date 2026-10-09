"""Public credential validation and persisted catalog compatibility boundaries."""

import json

import pytest
from pydantic import ValidationError

from schemas.electricity_schema import (
    AcademicAccountCreate,
    AcademicAccountUpdate,
    ElectricityAccountCreate,
    ElectricityAccountUpdate,
)
from services.electricity import (
    AccountPoolConfigurationError,
    ElectricityAccountPool,
    ElectricityService,
)
from utils.electricity_identity import numeric_room_number


@pytest.mark.parametrize("model", [ElectricityAccountCreate, AcademicAccountCreate])
def test_account_creation_rejects_whitespace_identifier(model):
    with pytest.raises(ValidationError, match="xh 不能为空"):
        model(xh=" \t ", pwd="secret")
    account = model(xh=" student ", pwd=" secret ")
    assert account.xh == "student"
    assert account.pwd == " secret "


@pytest.mark.parametrize("model", [ElectricityAccountUpdate, AcademicAccountUpdate])
def test_account_update_preserves_null_and_rejects_blank_identifier(model):
    assert model(xh=None).xh is None
    assert model(xh=" student ").xh == "student"
    with pytest.raises(ValidationError, match="xh 不能为空"):
        model(xh=" \t ")


@pytest.mark.parametrize("room", ["06417", "6-417", "guojiao-6-417", "国教6417"])
def test_numeric_room_display_removes_category_prefix(room):
    assert numeric_room_number(room) == "06417"


def test_pool_without_accounts_for_its_school(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps({"accounts": [{"school": "OTHER", "xh": "s", "pwd": "p"}]})
    )
    with pytest.raises(AccountPoolConfigurationError, match="没有可用账号"):
        ElectricityAccountPool(path)._read_accounts()


def test_missing_account_pool_can_only_be_read_for_initialization(tmp_path):
    pool = ElectricityAccountPool(tmp_path / "missing.json")
    assert pool._read_records(allow_missing=True) == []
    with pytest.raises(AccountPoolConfigurationError, match="未配置或不存在"):
        pool._read_accounts()


def test_blank_storage_configuration_disables_catalog_and_collection_state(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ELECTRICITY_ROOM_CATALOG_FILE", "  ")
    monkeypatch.setenv("ELECTRICITY_COLLECTION_STATE_FILE", "")
    service = ElectricityService(
        "https://example.test", ElectricityAccountPool(tmp_path / "accounts.json")
    )
    assert service.catalog_path is None
    assert service.collection_state_path is None
    assert service.get_room_catalog() is None


def test_legacy_catalog_keeps_explicit_identity_when_options_are_absent(tmp_path):
    service = ElectricityService(
        "https://example.test", ElectricityAccountPool(tmp_path / "accounts.json")
    )
    service.catalog_path = tmp_path / "rooms.json"
    room = {"campus_name": "含浦宿舍", "room_number": "guojiao-06417"}
    service.catalog_path.write_text(json.dumps({"rooms": [room]}))
    assert service.get_room_catalog()["rooms"] == [room]


def test_catalog_discards_stale_identity_for_unrecognizable_room_options(tmp_path):
    service = ElectricityService(
        "https://example.test", ElectricityAccountPool(tmp_path / "accounts.json")
    )
    service.catalog_path = tmp_path / "rooms.json"
    room = {
        "campus_name": "含浦宿舍",
        "building": "未知楼栋",
        "room": "未知寝室",
        "room_number": "06417",
    }
    service.catalog_path.write_text(json.dumps({"rooms": [room]}))
    rooms = service.get_room_catalog()["rooms"]
    assert len(rooms) == 1
    assert "room_number" not in rooms[0]
    assert rooms[0]["building"] == "未知楼栋"
