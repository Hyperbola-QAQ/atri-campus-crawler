import pytest
import base64
from pathlib import Path
from utils.ocr import img2txt

# 脚本当前的绝对路径
SCRIPT_DIR = Path(__file__).parent
# 测试图片路径
TEST_IMAGE_DIR = SCRIPT_DIR / "fixtures"


@pytest.mark.asyncio
async def test_img2txt_success():
    """测试成功识别验证码的情况"""
    # 获取所有jpg图片文件
    image_files = list(TEST_IMAGE_DIR.glob("*.jpg"))
    assert len(image_files) > 0, "未找到测试图片文件"

    # 测试每一张图片
    for image_file in image_files:
        # 从文件名获取期望的验证码（去除扩展名）
        expected_code = image_file.stem

        with open(image_file, "rb") as f:
            encoded_image = base64.b64encode(f.read()).decode("utf-8")

        # 测试OCR识别
        result = await img2txt(encoded_image)
        # 确保返回的是字符串且不为空
        assert isinstance(result, str)
        assert len(result) > 0
        # 验证识别结果与文件名（正确验证码）是否一致
        assert (
            result == expected_code
        ), f"识别结果 {result} 与期望验证码 {expected_code} 不一致"


@pytest.mark.asyncio
async def test_img2txt_invalid_base64():
    """测试无效base64编码的图像"""
    with pytest.raises(ValueError, match="验证码验证失败: Incorrect padding"):
        await img2txt("invalid_base64_string")


@pytest.mark.asyncio
async def test_img2txt_empty_string():
    """测试空字符串输入"""
    with pytest.raises(ValueError, match="验证码识别过程中发生错误"):
        await img2txt("")
