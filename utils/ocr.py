import ddddocr
import base64


ocr = ddddocr.DdddOcr()


async def img2txt(img: str) -> str:
    """Base64 图片识别后须为去空白的四位字符串，否则包装为 ValueError 并保留异常链。
    异步签名内仍同步调用 OCR，耗时隔离由调用方安排。
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
