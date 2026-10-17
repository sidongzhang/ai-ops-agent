"""多模态：图片 data URL 校验/限额，以及发给模型的多模态输入构造。"""
import base64
import unittest

from app.agent.diagnostics.runner import _build_prompt
from app.services.diagnostics.attachments import (
    AttachmentError,
    describe_images,
    parse_image_data_urls,
)

# 1x1 PNG
_PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63f8cfc0f01f0005000100ff9d2a4b0000000049454e44ae426082"
    )
).decode()
_DATA_URL = f"data:image/png;base64,{_PNG}"


class ParseImageTests(unittest.TestCase):
    def test_parses_data_url(self) -> None:
        parsed = parse_image_data_urls([_DATA_URL])
        self.assertEqual(len(parsed), 1)
        media, data = parsed[0]
        self.assertEqual(media, "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(parse_image_data_urls(None), [])
        self.assertEqual(parse_image_data_urls([]), [])

    def test_rejects_too_many_images(self) -> None:
        with self.assertRaises(AttachmentError):
            parse_image_data_urls([_DATA_URL] * 4, max_images=3)

    def test_rejects_non_data_url(self) -> None:
        with self.assertRaises(AttachmentError):
            parse_image_data_urls(["http://example.com/a.png"])

    def test_rejects_unsupported_media_type(self) -> None:
        with self.assertRaises(AttachmentError):
            parse_image_data_urls([f"data:image/svg+xml;base64,{_PNG}"])

    def test_rejects_oversized_image(self) -> None:
        with self.assertRaises(AttachmentError):
            parse_image_data_urls([_DATA_URL], max_image_bytes=4)

    def test_describe_images_metadata(self) -> None:
        meta = describe_images(parse_image_data_urls([_DATA_URL]))
        self.assertEqual(meta[0]["media_type"], "image/png")
        self.assertGreater(meta[0]["bytes"], 0)


class BuildPromptTests(unittest.TestCase):
    def test_text_only_prompt_is_plain_string(self) -> None:
        self.assertEqual(_build_prompt("系统健康吗？", None), "系统健康吗？")
        self.assertEqual(_build_prompt("系统健康吗？", []), "系统健康吗？")

    def test_images_become_binary_content_parts(self) -> None:
        from pydantic_ai import BinaryContent

        images = parse_image_data_urls([_DATA_URL])
        prompt = _build_prompt("看看这张图", images)
        self.assertIsInstance(prompt, list)
        self.assertEqual(len(prompt), 2)
        self.assertIsInstance(prompt[0], BinaryContent)
        self.assertEqual(prompt[1], "看看这张图")


if __name__ == "__main__":
    unittest.main()
