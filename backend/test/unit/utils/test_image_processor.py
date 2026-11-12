from __future__ import annotations

import base64
import io

from PIL import ExifTags, Image

import yuxi.infrastructure.images as image_module
from yuxi.infrastructure.images import process_uploaded_image


def test_process_uploaded_image_composites_transparent_png_pixels_on_white():
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 0))
    image.putpixel((0, 0), (50, 87, 244, 0))
    image.putpixel((1, 0), (50, 87, 244, 255))

    with io.BytesIO() as buffer:
        image.save(buffer, format="PNG")
        image_data = buffer.getvalue()

    result = process_uploaded_image(image_data, "transparent.png")

    assert result["success"] is True
    assert result["format"] == "PNG"
    assert result["mime_type"] == "image/png"

    processed_data = base64.b64decode(result["image_content"])
    with Image.open(io.BytesIO(processed_data)) as processed_image:
        rgb_image = processed_image.convert("RGB")

    assert rgb_image.getpixel((0, 0)) == (255, 255, 255)
    assert rgb_image.getpixel((1, 0)) == (50, 87, 244)


def test_process_uploaded_image_returns_jpeg_metadata_and_thumbnail():
    image = Image.new("RGB", (400, 200), (50, 87, 244))
    with io.BytesIO() as buffer:
        image.save(buffer, format="JPEG")
        image_data = buffer.getvalue()

    result = process_uploaded_image(image_data, "photo.jpg")

    assert result["success"] is True
    assert result["original_filename"] == "photo.jpg"
    assert (result["width"], result["height"]) == (400, 200)
    assert result["format"] == "JPEG"
    assert result["mime_type"] == "image/jpeg"
    processed_data = base64.b64decode(result["image_content"])
    assert result["size_bytes"] == len(processed_data)
    with Image.open(io.BytesIO(processed_data)) as processed_image:
        assert processed_image.format == "JPEG"
        assert processed_image.size == (400, 200)
    with Image.open(io.BytesIO(base64.b64decode(result["thumbnail_content"]))) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.size == (200, 100)


def test_process_uploaded_image_applies_exif_orientation():
    image = Image.new("RGB", (40, 20), (50, 87, 244))
    exif = image.getexif()
    exif[ExifTags.Base.Orientation] = 6
    with io.BytesIO() as buffer:
        image.save(buffer, format="JPEG", exif=exif)
        image_data = buffer.getvalue()

    result = process_uploaded_image(image_data, "rotated.jpg")

    assert result["success"] is True
    assert (result["width"], result["height"]) == (20, 40)


def test_process_uploaded_image_returns_error_for_invalid_image():
    result = process_uploaded_image(b"not an image", "invalid.png")

    assert result["success"] is False
    assert result["error"].startswith("无效的图片格式:")
    assert "image_content" not in result


def test_process_uploaded_image_bounds_quality_and_resize_attempts(monkeypatch):
    """不可达的大小上限也必须收敛并返回可解码图片。"""
    monkeypatch.setattr(image_module, "MAX_FILE_SIZE", 1)
    image = Image.new("RGB", (400, 200), (50, 87, 244))
    with io.BytesIO() as buffer:
        image.save(buffer, format="JPEG")
        image_data = buffer.getvalue()

    result = process_uploaded_image(image_data, "large.jpg")

    assert result["success"] is True
    with Image.open(io.BytesIO(base64.b64decode(result["image_content"]))) as processed_image:
        assert processed_image.format == "JPEG"
        assert processed_image.width < image.width
