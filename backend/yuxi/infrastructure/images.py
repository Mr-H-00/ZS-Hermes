"""图片格式转换、压缩与缩略图生成。"""

import base64
import io

from PIL import ExifTags, Image

from yuxi.infrastructure.observability.logging import logger

SUPPORTED_FORMATS = {"JPEG", "PNG", "WebP", "GIF", "BMP"}
MAX_FILE_SIZE = 5 * 1024 * 1024

_DEFAULT_IMAGE_QUALITY = 85


def process_uploaded_image(image_data: bytes, filename: str = "") -> dict:
    """处理上传图片，返回图片内容、缩略图与元数据或错误信息。"""
    try:
        img_format = _validate_image_format(image_data)
        if img_format not in SUPPORTED_FORMATS:
            raise ValueError(f"不支持的图片格式: {img_format}")

        with Image.open(io.BytesIO(image_data)) as img:
            img = _fix_image_orientation(img)
            thumbnail_data = _generate_thumbnail(img)
            processed_data, final_format = _compress_image(img, img_format)

            base64_data = base64.b64encode(processed_data).decode("utf-8")
            base64_thumbnail = base64.b64encode(thumbnail_data).decode("utf-8")
            width, height = img.size
            mime_type = f"image/{final_format.lower()}"

            return {
                "success": True,
                "image_content": base64_data,
                "thumbnail_content": base64_thumbnail,
                "width": width,
                "height": height,
                "format": final_format,
                "mime_type": mime_type,
                "size_bytes": len(processed_data),
                "original_filename": filename,
            }

    except Exception as e:
        logger.error(f"图片处理失败: {str(e)}")
        return {"success": False, "error": str(e)}


def _validate_image_format(image_data: bytes) -> str | None:
    """识别图片格式名，无法读取时统一为业务错误。"""
    try:
        with Image.open(io.BytesIO(image_data)) as img:
            return img.format
    except Exception as exc:
        raise ValueError(f"无效的图片格式: {exc}") from exc


def _fix_image_orientation(img: Image.Image) -> Image.Image:
    """根据 EXIF Orientation 修正图片方向。"""
    try:
        orientation = img.getexif().get(ExifTags.Base.Orientation)
        if orientation == 3:
            img = img.rotate(180, expand=True)
        elif orientation == 6:
            img = img.rotate(270, expand=True)
        elif orientation == 8:
            img = img.rotate(90, expand=True)
    except Exception as exc:
        logger.warning(f"修正图片方向失败，使用原始方向: {exc}")

    return img


def _generate_thumbnail(img: Image.Image) -> bytes:
    """生成保持宽高比的 JPEG 缩略图。"""
    try:
        thumbnail = _convert_to_rgb_for_export(img)
        thumbnail.thumbnail((200, 200), Image.Resampling.LANCZOS)
        return _encode_image(thumbnail, "JPEG")
    except Exception as exc:
        logger.error(f"生成缩略图失败: {exc}")
        return _encode_image(Image.new("RGB", (1, 1), color="white"), "JPEG")


def _compress_image(img: Image.Image, original_format: str) -> tuple[bytes, str]:
    """压缩图片，超出大小限制时降低质量或缩小尺寸。"""
    processed_img = _convert_to_rgb_for_export(img)
    target_format = "PNG" if original_format == "PNG" else "JPEG"

    compressed_data = _encode_image(processed_img, target_format)
    quality = _DEFAULT_IMAGE_QUALITY
    while len(compressed_data) > MAX_FILE_SIZE and quality > 10:
        quality -= 10
        compressed_data = _encode_image(processed_img, target_format, quality=quality)

    scale_factor = 0.9
    while len(compressed_data) > MAX_FILE_SIZE and scale_factor > 0.3:
        resized_img = processed_img.resize(
            (int(processed_img.width * scale_factor), int(processed_img.height * scale_factor)),
            Image.Resampling.LANCZOS,
        )
        compressed_data = _encode_image(resized_img, target_format)
        scale_factor -= 0.1

    return compressed_data, target_format


def _encode_image(img: Image.Image, image_format: str, *, quality: int = _DEFAULT_IMAGE_QUALITY) -> bytes:
    """按指定格式编码图片，统一压缩与优化参数。"""
    with io.BytesIO() as output:
        img.save(output, format=image_format, quality=quality, optimize=True)
        return output.getvalue()


def _convert_to_rgb_for_export(img: Image.Image) -> Image.Image:
    """转换为 RGB，同时把透明像素按白底合成，避免隐藏颜色变成可见像素。"""
    if img.mode == "RGB":
        return img.copy()

    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if not has_alpha:
        return img.convert("RGB")

    rgba_img = img.convert("RGBA")
    background = Image.new("RGBA", rgba_img.size, (255, 255, 255, 255))
    background.alpha_composite(rgba_img)
    return background.convert("RGB")
