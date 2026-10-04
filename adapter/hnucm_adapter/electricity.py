"""Client for the HNUCM campus payment portal's dormitory electricity API."""

import asyncio
import json
import re
import secrets
from typing import Any

import httpx
from lxml import etree  # ty: ignore[unresolved-import]

from services.cookie_cache import PortalSession


class ElectricityPlatformError(RuntimeError):
    """Raised when the remote electricity service cannot return meter data."""


def _jsbn_hex_to_base64(value: str) -> str:
    """Match jsbn's ``hex2b64`` conversion used by the portal login page."""
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
    """Encrypt ``password + random_string`` with RSA PKCS#1 v1.5 padding."""
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
    # jsbn's RSAKey.encrypt pads an odd-length hexadecimal result before hex2b64.
    if len(encrypted_hex) % 2:
        encrypted_hex = "0" + encrypted_hex
    return _jsbn_hex_to_base64(encrypted_hex)


def _hidden_value(document: str, field: str) -> str:
    tree = etree.HTML(document)
    if tree is None:
        raise ElectricityPlatformError("电费平台登录页格式异常")
    values = tree.xpath(f"//input[@id='{field}' or @name='{field}']/@value")
    if not values or not values[0]:
        raise ElectricityPlatformError("电费平台登录页缺少必要参数")
    return str(values[0])


def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ElectricityPlatformError("电费平台返回了无法识别的数据") from exc


class HNUCMElectricityClient:
    """Logs in to the cashier portal and retrieves one dorm meter reading."""

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
        """Return every selectable dorm room from every area visible to an account.

        The payment site exposes its inventory as a four-level selector rather
        than a single room-list endpoint.  Keep the area's opaque ``value`` as
        the campus identifier: it is stable for the lifetime of a portal
        record and, unlike a hand-maintained mapping, also covers new campuses.
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
                # A payment account can also see merchant areas.  Their room
                # selectors are valid portal data, but they are not dormitory
                # rooms and must not enter the electricity collection queue.
                continue
            area_selection = {**base_selection, "areaid": area_id}
            try:
                buildings = await self._get_room_options(
                    client, csrf_token, "build", area_selection
                )
            except ElectricityPlatformError:
                # A single disabled area can return a non-selector response.
                # Keep the rest of the dorm catalog available for collection.
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
        """Convert selector labels such as ``6号公寓`` / ``417房`` to ``06417``."""
        building_match = re.fullmatch(
            r"\s*(?:东塘)?(\d+)(?:号公寓|栋|号楼)\s*", building
        )
        room_match = re.fullmatch(r"\s*(\d{3})房\s*", room)
        if building_match is None or room_match is None:
            return None
        building_number = int(building_match.group(1))
        if not 0 <= building_number <= 99:
            return None
        return f"{building_number:02d}{room_match.group(1)}"

    async def _get_csrf_token(self, client: httpx.AsyncClient) -> str:
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
        """Resolve a displayed ``楼栋+房间`` number to portal option IDs."""
        building_number = int(room_number[:-3])
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
                if not isinstance(building_id, str) or not self._option_matches_number(
                    building, building_number
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
        return selections

    @staticmethod
    def _area_matches_campus(option: dict[str, Any], campus: str) -> bool:
        """Match a discovered campus ID, its exact label, or the legacy hanpu alias."""
        label = option.get("label")
        return (
            option.get("value") == campus
            or label == campus
            or (isinstance(label, str) and campus == "hanpu" and "含浦" in label)
        )

    @staticmethod
    def _option_matches_number(option: dict[str, Any], number: int) -> bool:
        label = option.get("label")
        if not isinstance(label, str):
            return False
        match = re.fullmatch(r"\s*(?:东塘)?(\d+)(?:号公寓|栋|号楼)\s*", label)
        return match is not None and int(match.group(1)) == number

    @staticmethod
    def _level_option_matches(option: dict[str, Any], level_number: str) -> bool:
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
