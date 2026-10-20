"""MockTransport 隔离真实客户端，字节 Redis 替身验证重建后缓存命中。
深拷贝和实际 RSA 解密补足返回值断言。
"""

import json

import httpx
import pytest

from adapter.hnucm_adapter.electricity import HNUCMElectricityClient
from services.cookie_cache import PortalSession
from services.electricity import ElectricityAccountPool, ElectricityService
from services.electricity_cache import DailyElectricityCache


class ByteRedis:
    """存储 JSON 文本但按 Redis 默认行为返回字节的共享替身。

    多个 DailyElectricityCache 实例使用同一 values，模拟进程重建后的共享存储。
    set 要求正 TTL，但测试不在替身中重新实现 Redis 的完整过期算法。
    """

    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex):
        assert ex > 0
        self.values[key] = value.encode("utf-8")


async def test_expired_http_session_reauthenticates_and_cache_survives_restart(
    tmp_path,
):
    accounts_path = tmp_path / "accounts.json"
    accounts_path.write_text(
        json.dumps({"accounts": [{"xh": "test", "pwd": "test-password"}]})
    )
    pool = ElectricityAccountPool(accounts_path)
    redis = ByteRedis()
    requests = []

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == HNUCMElectricityClient.csrf_path:
            return httpx.Response(200, json="fresh-csrf")
        if path == "/xysf/login.aspx":
            return httpx.Response(
                200,
                text=f'<input id="pbk" value="{"f" * 256}"><input id="ts" value="nonce">',
            )
        if path == HNUCMElectricityClient.login_api_path:
            assert request.headers["x-csrftoken"] == "fresh-csrf"
            assert json.loads(request.content)["pwd"] != "test-password"
            return httpx.Response(
                200, json={"code": "0000"}, headers={"set-cookie": "sid=fresh; Path=/"}
            )
        assert (
            path == HNUCMElectricityClient.room_option_api_path
            or path == HNUCMElectricityClient.electricity_api_path
        )
        if request.headers.get("cookie") == "sid=expired":
            # 生产平台会在缓存会话过期时返回 HTML。
            return httpx.Response(200, text="<html>please login</html>")
        assert request.headers["cookie"] == "sid=fresh"
        assert request.headers["x-csrftoken"] == "fresh-csrf"
        body = json.loads(request.content)
        if path == HNUCMElectricityClient.room_option_api_path:
            options = {
                "area": [{"label": "含浦学生宿舍", "value": "area"}],
                "build": [{"label": "6号公寓", "value": "build"}],
                "level": [{"label": "4楼", "value": "level"}],
                "room": [{"label": "417房", "value": "meter-room"}],
            }
            return httpx.Response(
                200, json={"IsSuccess": True, "Content": options[body["key"]]}
            )
        assert json.loads(body["rybh"])["roomid"] == "meter-room"
        return httpx.Response(
            200,
            json={
                "d": {
                    "RetCode": "T",
                    "Content": {
                        "Succ": True,
                        "CzThirdInfo": {"Balance": "32.33", "PackageName": "64.66"},
                    },
                }
            },
        )

    def portal_factory(base_url):
        return HNUCMElectricityClient(
            base_url,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )

    service = ElectricityService(
        "https://payment.example",
        pool,
        client_factory=portal_factory,
        reading_cache=DailyElectricityCache(redis_client=redis),
    )
    account = (await pool.rotated_accounts())[0]
    await service.cookie_cache.set(
        account.cache_key, PortalSession({"sid": "expired"}, "old-csrf")
    )
    reading = await service.query("6-417", "hanpu")
    assert reading["room_number"] == "06417"
    assert reading["balance"] == "32.33"
    assert reading["collected_at"]
    session = await service.cookie_cache.get(account.cache_key)
    assert session == PortalSession({"sid": "fresh"}, "fresh-csrf")
    assert (
        sum(
            request.url.path == HNUCMElectricityClient.login_api_path
            for request in requests
        )
        == 1
    )
    request_count = len(requests)
    restarted = ElectricityService(
        "https://payment.example",
        pool,
        client_factory=portal_factory,
        reading_cache=DailyElectricityCache(redis_client=redis),
    )
    restored = await restarted.query("6417", "hanpu")
    assert restored == reading
    assert len(requests) == request_count
    restored["balance"] = "changed"
    assert (await restarted.query("06417", "hanpu"))["balance"] == "32.33"


@pytest.mark.parametrize("payload_campus", ["dongtang", 1, ""])
async def test_cache_rejects_other_campus_even_for_same_room(payload_campus):
    redis = ByteRedis()
    cache = DailyElectricityCache(redis_client=redis)
    wrong = {"campus": payload_campus, "room_number": "06417", "balance": 100}
    day, _, _ = cache._day_and_expiry()
    redis.values[cache._key("hanpu", "06417", day)] = json.dumps(wrong).encode()
    assert await cache.get("hanpu", "06417") is None
    with pytest.raises(ValueError, match="身份不一致"):
        await cache.set("hanpu", "06417", wrong)


async def test_corrupt_redis_bytes_fall_back_to_independent_memory_snapshot():
    redis = ByteRedis()
    cache = DailyElectricityCache(redis_client=redis)
    original = {
        "campus": "hanpu",
        "room_number": "06417",
        "balance": 32.33,
        "metadata": {"source": "portal"},
    }
    await cache.set("hanpu", "06417", original)
    original["metadata"]["source"] = "caller-mutated"
    key = next(iter(redis.values))
    redis.values[key] = b"\xff"
    restored = await cache.get("hanpu", "06417")
    assert restored["metadata"]["source"] == "portal"
    restored["metadata"]["source"] = "reader-mutated"
    assert (await cache.get("hanpu", "06417"))["metadata"]["source"] == "portal"


def test_portal_rsa_ciphertext_decrypts_to_utf8_pkcs1_password(monkeypatch):
    """通过离线私钥解密验证平台 RSA 密文的真实结构。

    使用已知素数构造测试密钥，不读取任何部署公钥或私钥。
    随机源故意提供零字节，填充必须过滤零值且至少八字节。
    解密后检查 00 02 前缀、分隔零字节和 UTF-8 密码加 nonce。
    这能发现 Base64 长度看似正确但密码编码或填充协议错误的实现。
    """
    import base64

    from adapter.hnucm_adapter import electricity as module

    # 使用已知梅森素数构造离线 RSA 密钥，不涉及生产密钥或凭据。
    p, q = 2**521 - 1, 2**607 - 1
    modulus = p * q
    private_exponent = pow(65537, -1, (p - 1) * (q - 1))
    modulus_hex = format(modulus, "x")
    if len(modulus_hex) % 2:
        modulus_hex = "0" + modulus_hex
    monkeypatch.setattr(
        module.secrets, "token_bytes", lambda count: bytes([0, 17, 23]) * count
    )
    password, nonce = "测试password", "nonce"
    ciphertext = module.encrypt_password(password, modulus_hex, nonce)
    encrypted = int.from_bytes(base64.b64decode(ciphertext), "big")
    decoded = pow(encrypted, private_exponent, modulus).to_bytes(
        len(modulus_hex) // 2, "big"
    )
    assert decoded[:2] == b"\x00\x02"
    padding, message = decoded[2:].split(b"\x00", 1)
    assert len(padding) >= 8
    assert set(padding) == {17, 23}
    assert message == (password + nonce).encode("utf-8")
