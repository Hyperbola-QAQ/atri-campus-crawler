"""本地验证码文件名作为实际 OCR 预期，不访问学校生成服务。"""

import pytest
import base64
from pathlib import Path
from utils.ocr import img2txt

SCRIPT_DIR = Path(__file__).parent
TEST_IMAGE_DIR = SCRIPT_DIR / "fixtures"


@pytest.mark.asyncio
async def test_img2txt_success():
    """验证本地 JPEG 夹具经 Base64 编码后能取得预期四位验证码。

    测试要求至少存在一个夹具，防止空目录使循环悄悄通过。
    返回文本与文件名标签逐一比较，不只检查类型或非空。
    """
    image_files = list(TEST_IMAGE_DIR.glob("*.jpg"))
    assert len(image_files) > 0, "未找到测试图片文件"

    for image_file in image_files:
        # 从文件名获取期望的验证码（去除扩展名）
        expected_code = image_file.stem

        with open(image_file, "rb") as f:
            encoded_image = base64.b64encode(f.read()).decode("utf-8")

        result = await img2txt(encoded_image)
        assert isinstance(result, str)
        assert len(result) > 0
        # 验证识别结果与文件名（正确验证码）是否一致
        assert result == expected_code, (
            f"识别结果 {result} 与期望验证码 {expected_code} 不一致"
        )


@pytest.mark.asyncio
async def test_img2txt_invalid_base64():
    """验证残缺 Base64 在图像识别前被转换为可识别的输入错误。

    检查固定的填充错误包装，避免底层解码异常绕过业务边界。
    """
    with pytest.raises(ValueError, match="验证码验证失败: Incorrect padding"):
        await img2txt("invalid_base64_string")


@pytest.mark.asyncio
async def test_img2txt_empty_string():
    """验证空文本不能被当成成功的空验证码。

    解码为空字节后，底层识别失败仍应由 img2txt 包装为 ValueError。
    """
    with pytest.raises(ValueError, match="验证码识别过程中发生错误"):
        await img2txt("")
