import ddddocr
import base64


# 初始化 ddddocr 识别器
ocr = ddddocr.DdddOcr()


async def img2txt(img: str) -> str:
    """
    使用 ddddocr 自动识别验证码图片

    Args:
        img: base64编码的图片字符串

    Returns:
        识别出的验证码文本
    """
    try:
        # 解码base64字符串为字节
        img_bytes = base64.b64decode(img)

        # 使用 ddddocr 识别
        result = ocr.classification(img_bytes)

        # 确保结果是字符串类型
        if not isinstance(result, str):
            raise TypeError("识别结果不是字符串类型")

        text = result.strip()

        # 验证码长度检查
        if len(text) != 4:
            raise ValueError("验证码长度错误")

        return text

    except ValueError as ve:
        # 捕获并重新抛出值错误，包含更多上下文信息
        raise ValueError(f"验证码验证失败: {str(ve)}") from ve

    except Exception as e:
        # 捕获所有其他异常
        raise ValueError(f"验证码识别过程中发生错误: {str(e)}") from e
