import httpx
import io
import base64
from httpx import Cookies
from utils.ocr import img2txt
from utils.redis import get_redis_client
from redis.asyncio import Redis
from typing import Optional
from utils.log import logger
from urllib.parse import urlparse
from datetime import datetime


# 修改Redis客户端初始化，添加异常处理
try:
    redis_client: Optional[Redis] = get_redis_client()
except Exception as e:
    redis_client = None
    logger.warning(f"Redis连接失败，将跳过缓存功能: {e}")


async def save_cookies_to_redis(school_name: str, username: str, cookies: Cookies) -> None:
    """
    将cookies保存到Redis中

    Args:
        school_name (str): 学校名称
        username (str): 用户名
        cookies (Cookies): HTTP cookies对象
    """
    if redis_client is None:
        return

    key: str = f"cookies:{school_name}:{username}"
    try:
        cookie_items = []
        # 使用cookies.jar来避免CookieConflict异常
        for cookie in cookies.jar:
            cookie_items.append(f"{cookie.name}={cookie.value}")
        cookie_str: str = "; ".join(cookie_items)

        # 将cookie字符串存入redis
        await redis_client.setex(key, 1800, cookie_str)
        logger.debug(f"[{username}] 成功将Cookie信息保存到Redis: {key}")
    except Exception as e:
        logger.warning(f"[{username}] 存储Cookie到Redis失败: {e}")
        raise ValueError(f"存储Cookie到Redis失败: {e}")


async def get_cookies_from_jwxt(
    base_url: str,
    timeout: int,
    headers: dict,
    username: str,
    password: str,
) -> Cookies:
    """
    从教务系统获取cookies

    Args:
        base_url (str): 基础URL
        timeout (int): 超时时间
        headers (dict): 请求头
        username (str): 用户名
        password (str): 密码

    Returns:
        Cookies: 获取到的cookies对象

    Raises:
        ValueError: 登录失败或验证码识别失败
        httpx.RequestError: 网络请求失败
    """
    # 创建异步会话维持cookie
    async with httpx.AsyncClient(
        base_url=base_url, follow_redirects=True, timeout=timeout, headers=headers
    ) as client:
        try:
            logger.debug(f"[{username}] 开始获取教务系统Cookie")
            # 1. 获取初始Cookie
            await client.get("/Logon.do?method=logon")
            logger.debug(f"[{username}] 成功获取初始Cookie")

            # 2. 获取验证码图片转换为base64字符串并识别（最多重试5次）
            max_retry = 5
            randomcode: str = ""
            for attempt in range(max_retry):
                try:
                    logger.debug(
                        f"[{username}] 第 {attempt + 1} 次尝试获取并识别验证码"
                    )
                    code_response = await client.get("/verifycode.servlet")
                    img = io.BytesIO(code_response.content)
                    img = base64.b64encode(img.read()).decode()
                    # 使用asyncio.to_thread执行同步的OCR操作
                    randomcode = await img2txt(img)
                    logger.debug(f"[{username}] 验证码识别成功: {randomcode}")
                    break  # 成功则跳出循环
                except ValueError as e:
                    logger.warning(
                        f"[{username}] 第 {attempt + 1} 次验证码识别失败: {e}"
                    )
                    if attempt == max_retry - 1:  # 最后一次尝试仍然失败
                        logger.error(
                            f"[{username}] 验证码识别失败，已重试{max_retry}次: {e}"
                        )
                        raise ValueError(f"验证码识别失败，已重试{max_retry}次: {e}")

            # 3. 获取加密参数
            sess_response = await client.get("/Logon.do?method=logon&flag=sess")
            scode, sxh = sess_response.text.split("#")
            logger.debug(
                f"[{username}] 加密参数获取成功，scode长度: {len(scode)}, sxh长度: {len(sxh)}"
            )

            # 4. 构造加密凭证
            original = f"{username}%%%{password}"
            encoded = ""
            for i in range(len(original)):
                if i < 20:
                    encoded += original[i] + scode[: int(sxh[i])]
                    scode = scode[int(sxh[i]) :]
                else:
                    encoded += original[i:]
                    break
            logger.debug(f"[{username}] 加密凭证构造完成")

            # 5. 提交登录请求
            login_data = {
                "userAccount": username,
                "userPassword": "",  # 实际密码通过加密字段传输
                "RANDOMCODE": randomcode,
                "encoded": encoded,
            }
            logger.debug(f"[{username}] 提交登录请求")
            response = await client.post("/Logon.do?method=logon", data=login_data)

            # 显式处理302
            if response.status_code == 302:
                logger.debug(f"[{username}] 处理302重定向")
                # 跟随重定向到目标页面
                response = await client.get("/")

            # 6. 验证登录结果
            # 检查响应状态码和内容来判断是否登录成功
            logger.debug(f"[{username}] 验证登录结果，状态码: {response.status_code}")
            if response.status_code == 200 and "培养管理" in response.text:
                logger.info(f"[{username}] 登录成功！")
                # 获取会话cookies用于缓存到redis
                cookies: Cookies = client.cookies
                logger.debug(f"[{username}] 获取到Cookies")
                return cookies
            else:
                logger.warning(
                    f"[{username}] 登录失败，状态码: {response.status_code}，响应内容包含'培养管理': {'培养管理' in response.text}"
                )
                raise ValueError("登录失败，请检查用户名、密码")
        except httpx.RequestError as e:
            logger.error(f"[{username}] 网络请求失败: {e}")
            raise ValueError(f"网络请求失败: {e}")
        except ValueError as e:
            logger.error(f"[{username}] 登录过程出错: {e}")
            raise e
        except Exception as e:
            logger.error(f"[{username}] 登录过程中发生未知错误: {e}")
            raise ValueError(f"登录过程中发生未知错误: {e}")


async def get_cookies_from_redis(
    school_name: str, username: str, domain: str = "jwxt.hnucm.edu.cn"
) -> Cookies:
    """
    从Redis中获取cookies

    Args:
        school_name (str): 学校名称
        username (str): 用户名
        domain (str): 域名，默认为jwxt.hnucm.edu.cn

    Returns:
        Cookies: 获取到的cookies对象
    """
    if redis_client is None:
        logger.debug("Redis客户端不可用，无法从缓存获取Cookie")
        return Cookies()

    try:
        key: str = f"cookies:{school_name}:{username}"
        logger.debug(f"[{username}] 尝试从Redis获取Cookie: {key}")
        stored_data: Optional[str] = await redis_client.get(key)
        if stored_data is None:
            logger.debug(f"[{username}] Redis中未找到Cookie: {key}")
            return Cookies()

        # 解析分号分隔的cookie字符串
        cookies = Cookies()
        cookie_pairs = stored_data.split("; ")
        for cookie_pair in cookie_pairs:
            if "=" in cookie_pair:
                name, value = cookie_pair.split("=", 1)
                cookies.set(
                    name=name,
                    value=value,
                    domain=domain,
                    path="/",
                )
        logger.debug(f"[{username}] 成功从Redis还原完整Cookie")
        return cookies
    except Exception as e:
        logger.warning(f"[{username}] 从Redis获取Cookie失败: {e}")
        raise ValueError(f"从Redis获取Cookie失败: {e}")


def is_in_maintenance_window() -> bool:
    """
    判断当前时间是否在00:55-07:05的维护窗口内
    """
    now = datetime.now().time()
    start = now.replace(hour=0, minute=55, second=0, microsecond=0)
    end = now.replace(hour=7, minute=5, second=0, microsecond=0)
    return start <= now <= end


async def get_valid_cookies(
    base_url: str,
    school_name: str,
    timeout: int,
    headers: dict,
    username: str,
    password: str,
) -> Cookies:
    """
    获取有效的cookies，优先从缓存获取，如果缓存无效则重新登录获取

    Args:
        base_url (str): 基础URL
        school_name (str): 学校名称
        timeout (int): 超时时间
        headers (dict): 请求头
        username (str): 用户名
        password (str): 密码

    Returns:
        Cookies: 有效的cookies对象
    """

    if is_in_maintenance_window():
        logger.info("当前时间在教务系统维护窗口内，无法获取有效Cookie")
        raise ValueError("当前时间在教务系统维护窗口内，无法获取有效Cookie")

    # 从base_url提取域名
    parsed_url = urlparse(base_url)
    domain = parsed_url.hostname or "jwxt.hnucm.edu.cn"

    cookies: Cookies

    try:
        logger.debug(f"[{username}] 尝试从缓存获取有效的Cookie")
        # 从缓存获取Cookie
        cookies = await get_cookies_from_redis(school_name, username, domain)

        # 验证缓存的Cookie是否可用
        if cookies:
            logger.debug(f"[{username}] 发现缓存Cookie，验证有效性")
            async with httpx.AsyncClient(
                base_url=base_url,
                follow_redirects=True,
                timeout=timeout,
                headers=headers,
                cookies=cookies,
            ) as client:
                response = await client.get("/jsxsd/framework/xsMain.htmlx#")
                if response.status_code == 200 and "培养管理" in response.text:
                    logger.info(f"[{username}] 使用缓存Cookie登录成功！")
                    # 如果cookie有效，则续约cookie过期时间
                    try:
                        await save_cookies_to_redis(school_name, username, cookies)
                        logger.debug(f"[{username}] Cookie已续约")
                    except Exception as e:
                        logger.warning(f"[{username}] Cookie续约失败: {e}")
                    return cookies
                else:
                    logger.debug(f"[{username}] 缓存Cookie已过期")
    except ValueError as e:
        logger.warning(f"[{username}] 使用缓存Cookie时出错: {e}")
        pass

    try:
        logger.debug(f"[{username}] 从教务系统获取新的Cookie")
        # 从教务系统获取Cookie
        cookies = await get_cookies_from_jwxt(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            username=username,
            password=password,
        )
        logger.debug(f"[{username}] 成功从教务系统获取Cookie")
    except ValueError as e:
        logger.warning(f"[{username}] 获取Cookie失败: {e}")
        raise ValueError(f"获取Cookie失败: {e}")
    except Exception as e:
        logger.error(f"[{username}] 获取Cookie时发生未知错误: {e}")
        raise ValueError(f"获取Cookie时发生未知错误: {e}")

    # 将cookie存储到Redis中供后续复用
    try:
        await save_cookies_to_redis(
            school_name=school_name, username=username, cookies=cookies
        )
    except ValueError as e:
        logger.warning(f"[{username}] 保存Cookie时发生错误: {e}")
    except Exception as e:
        logger.warning(f"[{username}] 保存Cookie时发生未知错误: {e}")

    return cookies
