import ddddocr
import base64


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
        img_bytes = base64.b64decode(img)

        result = ocr.classification(img_bytes)

        if not isinstance(result, str):
            raise TypeError("识别结果不是字符串类型")

        text = result.strip()

        if len(text) != 4:
            raise ValueError("验证码长度错误")

        return text

    except ValueError as ve:
        raise ValueError(f"验证码验证失败: {str(ve)}") from ve

    except Exception as e:
        raise ValueError(f"验证码识别过程中发生错误: {str(e)}") from e
