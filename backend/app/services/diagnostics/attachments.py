"""诊断附件（图片）解析与限额校验。

前端以 data URL（`data:image/png;base64,....`）提交用户粘贴/上传的截图，
这里统一做**白名单 + 限额**校验，避免把超大/非法内容送进模型。

设计原则：只接受常见位图格式、总量与单张都有上限；校验失败抛 AttachmentError，
由 API 层转成 400，而不是静默丢弃（用户需要知道哪张图没被采纳）。
"""
from __future__ import annotations

import base64
import binascii
import re

MAX_IMAGES = 3
MAX_IMAGE_BYTES = 2 * 1024 * 1024        # 单张 2MB
MAX_TOTAL_BYTES = 4 * 1024 * 1024        # 合计 4MB
ALLOWED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

_DATA_URL = re.compile(r"^data:(?P<media>[\w.+-]+/[\w.+-]+);base64,(?P<payload>[A-Za-z0-9+/=\s]+)$")


class AttachmentError(ValueError):
    """附件不合法（格式/大小/数量）。"""


def parse_image_data_urls(
    raw: list[str] | None,
    *,
    max_images: int = MAX_IMAGES,
    max_image_bytes: int = MAX_IMAGE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> list[tuple[str, bytes]]:
    """把 data URL 列表解析成 [(media_type, bytes), ...]。空输入返回 []。"""
    items = [item for item in (raw or []) if item]
    if not items:
        return []
    if len(items) > max_images:
        raise AttachmentError(f"最多支持 {max_images} 张图片")

    parsed: list[tuple[str, bytes]] = []
    total = 0
    for index, item in enumerate(items, start=1):
        match = _DATA_URL.match(str(item).strip())
        if not match:
            raise AttachmentError(f"第 {index} 张图片格式不支持（需要 data:image/...;base64, 形式）")
        media = match.group("media").lower()
        if media not in ALLOWED_MEDIA_TYPES:
            raise AttachmentError(
                f"第 {index} 张图片类型 {media} 不支持，仅支持 {', '.join(ALLOWED_MEDIA_TYPES)}"
            )
        try:
            data = base64.b64decode(match.group("payload"), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentError(f"第 {index} 张图片 base64 解码失败") from exc
        if not data:
            raise AttachmentError(f"第 {index} 张图片内容为空")
        if len(data) > max_image_bytes:
            raise AttachmentError(f"第 {index} 张图片超过 {max_image_bytes // 1024 // 1024}MB")
        total += len(data)
        if total > max_total_bytes:
            raise AttachmentError(f"图片合计超过 {max_total_bytes // 1024 // 1024}MB")
        parsed.append((media, data))
    return parsed


def describe_images(images: list[tuple[str, bytes]]) -> list[dict]:
    """给报告留证的图片元信息（不落图本体）。"""
    return [{"media_type": media, "bytes": len(data)} for media, data in images]
