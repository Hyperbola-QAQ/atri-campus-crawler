"""湖南中医药大学缴费门户适配；账户轮换和缓存由服务层管理。"""

import asyncio
import json
import re
import secrets
from typing import Any

import httpx
from lxml import etree  # ty: ignore[unresolved-import]

from services.cookie_cache import PortalSession
from utils.electricity_identity import normalize_room_number


class ElectricityPlatformError(RuntimeError):
    """远端电量服务无法返回电表数据时抛出。"""


def _jsbn_hex_to_base64(value: str) -> str:
    """沿用平台 jsbn 的半字节分组，保留奇数长度输入的转换语义。"""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    output: list[str] = []
    index = 0

    while index + 3 <= len(value):
        chunk = int(value[index : index + 3], 16)
        output.extend((alphabet[chunk >> 6], alphabet[chunk & 0x3F]))
        index += 3

    remainder = len(value) - index
    if remainder == 1:
        output.append(alphabet[int(value[index:], 16) << 2])
    elif remainder == 2:
        chunk = int(value[index:], 16)
        output.extend((alphabet[chunk >> 2], alphabet[(chunk & 0x03) << 4]))

    while len(output) % 4:
        output.append("=")
    return "".join(output)


def encrypt_password(password: str, public_key_hex: str, random_string: str) -> str:
    """按平台协议对 UTF-8 密码与随机串的直接拼接执行 RSA PKCS#1 v1.5。

    指数固定为 65537，填充至少八个非零随机字节；无效模数或超长消息
    抛出 ElectricityPlatformError。结果须补齐奇数十六进制位再经 jsbn 转换。
    """
    try:
        modulus = int(public_key_hex, 16)
    except ValueError as exc:
        raise ElectricityPlatformError("电费平台返回了无效的登录公钥") from exc
    if modulus <= 0 or len(public_key_hex) % 2:
        raise ElectricityPlatformError("电费平台返回了无效的登录公钥")

    key_size = len(public_key_hex) // 2
    message = (password + random_string).encode("utf-8")
    padding_size = key_size - len(message) - 3
    if padding_size < 8:
        raise ElectricityPlatformError("登录密码长度超出平台支持范围")

    padding = bytearray()
    while len(padding) < padding_size:
        padding.extend(byte for byte in secrets.token_bytes(padding_size) if byte)
    encoded_message = b"\x00\x02" + bytes(padding[:padding_size]) + b"\x00" + message
    encrypted_int = pow(int.from_bytes(encoded_message, "big"), 0x10001, modulus)
    encrypted_hex = format(encrypted_int, "x")
    # jsbn 的 RSAKey.encrypt 会在 hex2b64 前为奇数长度的十六进制结果补零。
    if len(encrypted_hex) % 2:
        encrypted_hex = "0" + encrypted_hex
    return _jsbn_hex_to_base64(encrypted_hex)


def _hidden_value(document: str, field: str) -> str:
    """从登录页读取必要隐藏参数，缺失或空值抛出平台异常。

    field 仅来自内部固定调用点，不接受用户 XPath。
    """
    tree = etree.HTML(document)
    if tree is None:
        raise ElectricityPlatformError("电费平台登录页格式异常")
    values = tree.xpath(f"//input[@id='{field}' or @name='{field}']/@value")
    if not values or not values[0]:
        raise ElectricityPlatformError("电费平台登录页缺少必要参数")
    return str(values[0])


def _response_json(response: httpx.Response) -> Any:
    """仅验证 JSON 可解析，形状由调用方检查；失败异常不包含原始正文。"""
    try:
        return response.json()
    except ValueError as exc:
        raise ElectricityPlatformError("电费平台返回了无法识别的数据") from exc


class HNUCMElectricityClient:
    """每次操作独立创建客户端，以 PortalSession 复用 Cookie 和 CSRF。

    trust_env=False 防止学校请求被宿主代理配置转发；不在实例保存账户会话。
    """

    DEFAULT_BASE_URL = "http://cw-zfpt.hnucm.edu.cn"
    csrf_path = "/xysf/api/Token/Csrf"
    login_page_path = "/xysf/login.aspx?local=zh-cn&lx="
    login_api_path = "/xysf/api/User/App/Login"
    room_option_api_path = "/xysf/api/user/ElecRoomYun/GetOption"
    electricity_api_path = "/xysf/aAppPage/index.aspx/GetRechargeInfo"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20,
        client_factory: Any = httpx.AsyncClient,
    ):
        """仅保存连接参数，构造时不发起登录。"""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client_factory = client_factory

    async def query(
        self,
        room_number: str,
        campus: str,
        username: str,
        password: str,
        session: PortalSession | None = None,
    ) -> tuple[dict[str, Any], PortalSession]:
        """返回寝室公开读数及从最终 Cookie jar 构造的新会话。

        传入会话时直接复用；会话失效后的重新登录由服务层决定。
        无效寝室或未配置地址在网络请求前拒绝，协议失败抛出平台异常。
        """
        try:
            room_number = normalize_room_number(room_number)
        except ValueError as exc:
            raise ElectricityPlatformError("寝室标识格式不正确") from exc
        if not self.base_url:
            raise ElectricityPlatformError("未配置电费平台地址")

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0"
            ),
        }
        async with self.client_factory(
            base_url=self.base_url,
            follow_redirects=True,
            timeout=self.timeout,
            headers=headers,
            cookies=session.cookies if session is not None else None,
            trust_env=False,
        ) as client:
            if session is None:
                csrf_token = await self._get_csrf_token(client)
                public_key = await self._get_login_parameters(client)
                await self._login(client, csrf_token, public_key, username, password)
            else:
                csrf_token = session.csrf_token
            result = await self._get_electricity(
                client, csrf_token, room_number, campus
            )
            cookies = {cookie.name: cookie.value for cookie in client.cookies.jar}
            return result, PortalSession(cookies=cookies, csrf_token=csrf_token)

    async def discover_rooms(
        self,
        username: str,
        password: str,
        session: PortalSession | None = None,
    ) -> tuple[list[dict[str, str]], PortalSession]:
        """逐层枚举宿舍目录，返回公开标签及更新后的会话。

        校区以平台真实区域 ID 标识；不向调用方暴露私有房间选择 ID。
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0"
            ),
        }
        async with self.client_factory(
            base_url=self.base_url,
            follow_redirects=True,
            timeout=self.timeout,
            headers=headers,
            cookies=session.cookies if session is not None else None,
            trust_env=False,
        ) as client:
            if session is None:
                csrf_token = await self._get_csrf_token(client)
                public_key = await self._get_login_parameters(client)
                await self._login(client, csrf_token, public_key, username, password)
            else:
                csrf_token = session.csrf_token

            rooms = await self._discover_room_options(client, csrf_token)
            cookies = {cookie.name: cookie.value for cookie in client.cookies.jar}
            return rooms, PortalSession(cookies=cookies, csrf_token=csrf_token)

    async def _discover_room_options(
        self, client: httpx.AsyncClient, csrf_token: str
    ) -> list[dict[str, str]]:
        """跳过不完整或请求失败的宿舍分支，继续发现其他区域。

        无法识别的房号只保留公开标签，不猜测可能指向其他电表的寝室号。
        """
        base_selection = {
            "areaid": "-1",
            "buildid": "-1",
            "roomid": "-1",
            "levelid": "-1",
            "IsLxr": False,
            "IsDefault": False,
            "IsFirst": False,
            "Cxid": "",
        }
        discovered: list[dict[str, str]] = []
        for area in await self._get_room_options(
            client, csrf_token, "area", base_selection
        ):
            area_id, area_name = area.get("value"), area.get("label")
            if not isinstance(area_id, str) or not isinstance(area_name, str):
                continue
            if "宿舍" not in area_name or "商户" in area_name:
                # 缴费账户也能访问商户区域；其寝室选择器虽有效，但商户房间不能进入宿舍电量采集队列。
                continue
            area_selection = {**base_selection, "areaid": area_id}
            try:
                buildings = await self._get_room_options(
                    client, csrf_token, "build", area_selection
                )
            except ElectricityPlatformError:
                # 单个停用区域可能返回非选择器响应，不应影响其余宿舍目录采集。
                continue
            for building in buildings:
                building_id, building_name = (
                    building.get("value"),
                    building.get("label"),
                )
                if not isinstance(building_id, str) or not isinstance(
                    building_name, str
                ):
                    continue
                building_selection = {**area_selection, "buildid": building_id}
                try:
                    levels = await self._get_room_options(
                        client, csrf_token, "level", building_selection
                    )
                except ElectricityPlatformError:
                    continue
                for level in levels:
                    level_id, level_name = level.get("value"), level.get("label")
                    if not isinstance(level_id, str) or not isinstance(level_name, str):
                        continue
                    level_selection = {**building_selection, "levelid": level_id}
                    try:
                        rooms = await self._get_room_options(
                            client, csrf_token, "room", level_selection
                        )
                    except ElectricityPlatformError:
                        continue
                    for room in rooms:
                        room_id, room_name = room.get("value"), room.get("label")
                        if not isinstance(room_id, str) or not isinstance(
                            room_name, str
                        ):
                            continue
                        record = {
                            "campus": area_id,
                            "campus_name": area_name,
                            "building": building_name,
                            "level": level_name,
                            "room": room_name,
                        }
                        room_number = self._room_number_from_options(
                            building_name, room_name
                        )
                        if room_number is not None:
                            record["room_number"] = room_number
                        discovered.append(record)
        return discovered

    @staticmethod
    def _room_number_from_options(building: str, room: str) -> str | None:
        """仅从完整匹配的楼栋、三位房号标签推导寝室号，否则返回 None。

        保留国教楼类，避免与普通楼同号房间混淆。
        """
        building_match = re.fullmatch(
            r"\s*(?:东塘)?(?P<special>国教)?(?P<number>[0-9]+)(?:号公寓|栋|号楼)\s*",
            building,
        )
        room_match = re.fullmatch(r"\s*(\d{3})房\s*", room)
        if building_match is None or room_match is None:
            return None
        building_number = int(building_match.group("number"))
        if not 0 <= building_number <= 99:
            return None
        prefix = "guojiao-" if building_match.group("special") else ""
        return f"{prefix}{building_number:02d}{room_match.group(1)}"

    async def _get_csrf_token(self, client: httpx.AsyncClient) -> str:
        """取得非空 CSRF 字符串；网络、HTTP 和无效令牌均转为平台异常。"""
        try:
            response = await client.post(self.csrf_path, json={})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ElectricityPlatformError("无法连接电费平台") from exc

        token = _response_json(response)
        if not isinstance(token, str) or not token:
            raise ElectricityPlatformError("电费平台未提供登录令牌")
        return token

    async def _get_login_parameters(self, client: httpx.AsyncClient) -> tuple[str, str]:
        """按模数、随机串顺序返回登录参数；HTTP 或隐藏字段异常阻止登录。"""
        try:
            response = await client.get(self.login_page_path)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ElectricityPlatformError("无法读取电费平台登录页") from exc
        return _hidden_value(response.text, "pbk"), _hidden_value(response.text, "ts")

    async def _login(
        self,
        client: httpx.AsyncClient,
        csrf_token: str,
        public_key: tuple[str, str],
        username: str,
        password: str,
    ) -> None:
        """仅业务码 0000 或 FIRST0001 表示成功，HTTP 200 不足以确认登录。"""
        modulus_hex, random_string = public_key
        body = {
            "xh": username,
            "pwd": encrypt_password(password, modulus_hex, random_string),
            "rsaStr": random_string,
            "ltyp": "id",
            "lx": "",
        }
        try:
            response = await client.post(
                self.login_api_path,
                json=body,
                headers={"x-csrfToken": csrf_token},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ElectricityPlatformError("电费平台登录请求失败") from exc

        result = _response_json(response)
        if not isinstance(result, dict) or result.get("code") not in {
            "0000",
            "FIRST0001",
        }:
            raise ElectricityPlatformError("电费平台登录失败")

    async def _get_electricity(
        self,
        client: httpx.AsyncClient,
        csrf_token: str,
        room_number: str,
        campus: str,
    ) -> dict[str, Any]:
        """查询已消歧的寝室；无可用读数时抛出异常，不生成零余额。"""
        for room_selection in await self._resolve_room_selections(
            client, csrf_token, room_number, campus
        ):
            result = await self._get_electricity_for_selection(
                client, csrf_token, room_number, campus, room_selection
            )
            if result is not None:
                return result

        raise ElectricityPlatformError("电费平台暂未返回该寝室的电表信息")

    async def _resolve_room_selections(
        self,
        client: httpx.AsyncClient,
        csrf_token: str,
        room_number: str,
        campus: str,
    ) -> list[dict[str, Any]]:
        """将寝室号与校区解析为唯一的四级平台 ID 组合，否则拒绝查询。

        保留国教楼类，精确匹配楼层和房间，防止同名或子串匹配选错电表。
        """
        try:
            room_number = normalize_room_number(room_number)
        except ValueError as exc:
            raise ElectricityPlatformError("寝室标识格式不正确") from exc
        room_label = f"{room_number[-3:]}房"
        level_number = room_number[-3]
        base_selection = {
            "areaid": "-1",
            "buildid": "-1",
            "roomid": "-1",
            "levelid": "-1",
            "IsLxr": False,
            "IsDefault": False,
            "IsFirst": False,
            "Cxid": "",
        }

        selections: list[dict[str, Any]] = []
        for area in await self._get_room_options(
            client, csrf_token, "area", base_selection
        ):
            area_id = area.get("value")
            if (
                not isinstance(area_id, str)
                or area_id == "-1"
                or not self._area_matches_campus(area, campus)
            ):
                continue
            area_selection = {**base_selection, "areaid": area_id}
            for building in await self._get_room_options(
                client, csrf_token, "build", area_selection
            ):
                building_id = building.get("value")
                if (
                    not isinstance(building_id, str)
                    or self._room_number_from_options(
                        str(building.get("label", "")), room_label
                    )
                    != room_number
                ):
                    continue
                building_selection = {**area_selection, "buildid": building_id}
                for level in await self._get_room_options(
                    client, csrf_token, "level", building_selection
                ):
                    level_id = level.get("value")
                    if not isinstance(level_id, str) or not self._level_option_matches(
                        level, level_number
                    ):
                        continue
                    level_selection = {**building_selection, "levelid": level_id}
                    for room in await self._get_room_options(
                        client, csrf_token, "room", level_selection
                    ):
                        room_id = room.get("value")
                        if isinstance(room_id, str) and room.get("label") == room_label:
                            selections.append({**level_selection, "roomid": room_id})
        if not selections:
            raise ElectricityPlatformError(
                f"{campus}校区未找到该寝室对应的电费平台房间"
            )
        unique = {
            tuple(item[key] for key in ("areaid", "buildid", "levelid", "roomid")): item
            for item in selections
        }
        if len(unique) != 1:
            raise ElectricityPlatformError("目录存在多个同名寝室, 无法安全确定电表")
        return list(unique.values())

    @staticmethod
    def _area_matches_campus(option: dict[str, Any], campus: str) -> bool:
        """兼容真实 ID、精确名称；hanpu/dongtang 别名仅匹配非商户宿舍区域。"""
        label = option.get("label")
        return (
            option.get("value") == campus
            or label == campus
            or (
                isinstance(label, str)
                and campus in {"hanpu", "dongtang"}
                and {"hanpu": "含浦", "dongtang": "东塘"}[campus] in label
                and "宿舍" in label
                and "商户" not in label
            )
        )

    @staticmethod
    def _level_option_matches(option: dict[str, Any], level_number: str) -> bool:
        """允许楼栋前缀，但楼层前不得为数字，防止 4 层匹配 14 层。"""
        label = option.get("label")
        return (
            isinstance(label, str)
            and re.search(rf"(?:^|\D){re.escape(level_number)}[层楼]$", label.strip())
            is not None
        )

    async def _get_room_options(
        self,
        client: httpx.AsyncClient,
        csrf_token: str,
        key: str,
        selection: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """验证目录业务状态和 Content 列表，value/label 由上层检查。

        平台提供字符串 RetCode/RetMsg 时保留其说明，否则使用固定错误。
        """
        try:
            response = await client.post(
                self.room_option_api_path,
                json={"key": key, "option": selection},
                headers={"x-csrfToken": csrf_token},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ElectricityPlatformError("无法读取电费平台房间信息") from exc
        result = _response_json(response)
        if not isinstance(result, dict):
            raise ElectricityPlatformError("电费平台房间信息格式异常")
        content = result.get("Content")
        if result.get("IsSuccess") is not True:
            code = result.get("RetCode")
            message = result.get("RetMsg")
            if isinstance(code, str) and isinstance(message, str) and message:
                raise ElectricityPlatformError(
                    f"电费平台房间信息请求失败（{code}: {message}）"
                )
            raise ElectricityPlatformError("电费平台房间信息请求失败")
        if not isinstance(content, list):
            raise ElectricityPlatformError("电费平台房间信息格式异常")
        return [item for item in content if isinstance(item, dict)]

    async def _get_electricity_for_selection(
        self,
        client: httpx.AsyncClient,
        csrf_token: str,
        room_number: str,
        campus: str,
        room_selection: dict[str, Any],
    ) -> dict[str, Any] | None:
        """将选择 ID 编为 rybh 内嵌 JSON，兼容顶层或 d 包装响应。

        RetCode 仅接受缺失或 T；缺少电表信息最多尝试三次、间隔 0.25 秒。
        有电表且 Succ=False 时返回 None；明确协议失败立即抛出异常。
        """
        body = {
            "rybh": json.dumps(
                room_selection, ensure_ascii=False, separators=(",", ":")
            ),
            "category": "ElecRoomYun",
        }

        result: Any = None
        for attempt in range(3):
            try:
                response = await client.post(
                    self.electricity_api_path,
                    json=body,
                    headers={"x-csrfToken": csrf_token},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ElectricityPlatformError("电费查询请求失败") from exc
            result = _response_json(response)
            if isinstance(result, dict) and isinstance(result.get("d"), dict):
                result = result["d"]
            if not isinstance(result, dict):
                raise ElectricityPlatformError("电费平台返回了异常数据")
            if result.get("RetCode") not in (None, "T"):
                raise ElectricityPlatformError("电费平台未能查询该寝室")
            content = result.get("Content")
            if isinstance(content, dict) and content.get("CzThirdInfo"):
                if content.get("Succ") is False:
                    return None
                return self._serialize_result(
                    room_number, campus, content["CzThirdInfo"]
                )
            if attempt < 2:
                await asyncio.sleep(0.25)
        return None

    @staticmethod
    def _serialize_result(room_number: str, campus: str, meter: Any) -> dict[str, Any]:
        """仅返回公开电表字段；余额与电量均缺失时拒绝成功结果。

        保留平台数字/文本原值，单位解释和采集时间由下游负责。
        """
        if not isinstance(meter, dict):
            raise ElectricityPlatformError("电费平台返回了异常电表信息")
        if not any(
            meter.get(key) not in (None, "") for key in ("PackageName", "Balance")
        ):
            raise ElectricityPlatformError("电费平台暂未返回该寝室的电量信息")

        return {
            "campus": campus,
            "room_number": room_number,
            "name": meter.get("Czxm"),
            "meter_number": meter.get("Czzjh"),
            "remaining_electricity": meter.get("PackageName"),
            "balance": meter.get("Balance"),
            "state": meter.get("State"),
            "category": meter.get("Category", "ElecRoomYun"),
        }
