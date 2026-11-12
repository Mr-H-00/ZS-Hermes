"""RapidOCR 请求的模型必须由安装的 rapidocr wheel 自带（离线可加载）。"""

from __future__ import annotations

import os
import sysconfig
from pathlib import Path

import pytest
from rapidocr import ModelType, OCRVersion

from yuxi.infrastructure.document_parsing.engines.rapid_ocr import RapidOCRParser


def test_concurrent_first_use_shares_one_model(monkeypatch):
    """并发首次请求共享同一个完成初始化的模型。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    parser = RapidOCRParser()
    start = threading.Barrier(2)
    entered = threading.Event()
    release = threading.Event()
    models = []

    def construct(**kwargs):
        """阻塞初始化，让另一个请求竞争同一未加载实例。"""
        model = object()
        models.append(model)
        entered.set()
        assert release.wait(5)
        return model

    def load():
        """返回请求实际取得的模型身份。"""
        start.wait(5)
        parser._load_model()
        return parser.ocr

    monkeypatch.setattr("yuxi.infrastructure.document_parsing.engines.rapid_ocr.RapidOCR", construct)
    monkeypatch.setattr(parser, "_get_model_params", lambda: {})
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(load) for _ in range(2)]
        assert entered.wait(5)
        release.set()
        actual = [future.result(5) for future in futures]
    assert actual[0] is actual[1] is models[0]
    assert len(models) == 1


@pytest.mark.parametrize("array", [False, True])
def test_memory_image_preserves_color_without_temporary_file(tmp_path, array):
    """用依赖的真实图像加载器确认内存输入与 PNG 的颜色语义一致。"""
    import numpy as np
    from PIL import Image
    from rapidocr.utils.load_image import LoadImage
    from types import SimpleNamespace

    image = Image.new("RGB", (4, 4), (255, 50, 10))
    reference = tmp_path / "reference.png"
    image.save(reference)
    expected = LoadImage()(str(reference))
    received = []

    def recognize(value):
        """从传给依赖的真实输入回读像素。"""
        assert not isinstance(value, str)
        received.append(LoadImage()(value))
        return SimpleNamespace(txts=["recognized"])

    parser = RapidOCRParser()
    parser.ocr = recognize
    assert parser.process_image(np.asarray(image) if array else image) == "recognized"
    np.testing.assert_array_equal(received[0], expected)


def _wheel_models_dir() -> Path:
    return Path(sysconfig.get_paths()["purelib"]) / "rapidocr" / "models"


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value)).lower()


def _wheel_bundled_model_defects(params: dict[str, object], models_dir: Path) -> list[str]:
    """检查请求的 det/rec 模型文件是否存在于 wheel 自带目录。"""
    defective: list[str] = []
    version = _enum_value(params["Det.ocr_version"])
    det_type = _enum_value(params["Det.model_type"])
    rec_type = _enum_value(params["Rec.model_type"])
    expected = {
        f"{version}_det_{det_type}.onnx",
        f"{version}_rec_{rec_type}.onnx",
    }
    available = {path.name.lower() for path in models_dir.glob("*.onnx")}
    for name in sorted(expected):
        if name.lower() not in available:
            defective.append(f"wheel 未包含 {name}（可用: {sorted(available)}）")
    return defective


def test_rapid_ocr_requests_models_bundled_in_wheel() -> None:
    params = RapidOCRParser()._get_model_params()
    assert params["Det.ocr_version"] == OCRVersion.PPOCRV6
    assert params["Rec.ocr_version"] == OCRVersion.PPOCRV6
    assert params["Det.model_type"] == ModelType.SMALL
    assert params["Rec.model_type"] == ModelType.SMALL
    assert _wheel_bundled_model_defects(params, _wheel_models_dir()) == []


def test_wheel_bundled_check_rejects_ppocrv5_mobile() -> None:
    """负向：回退 PP-OCRv5+MOBILE 时 wheel 中不存在对应 ONNX。"""
    params = {
        "Det.ocr_version": OCRVersion.PPOCRV5,
        "Det.model_type": ModelType.MOBILE,
        "Rec.model_type": ModelType.MOBILE,
    }
    defects = _wheel_bundled_model_defects(params, _wheel_models_dir())
    assert defects
    assert "pp-ocrv5_det_mobile.onnx" in defects[0]


@pytest.mark.skipif(not os.environ.get("RAPIDOCR_MODEL_DIR"), reason="未配置预置模型目录")
def test_preinstalled_model_dir_covers_requested_models() -> None:
    """镜像内 RAPIDOCR_MODEL_DIR 已预置请求的 det/rec 模型时，解析路径不需要联网。"""
    params = RapidOCRParser()._get_model_params()
    assert _wheel_bundled_model_defects(params, Path(os.environ["RAPIDOCR_MODEL_DIR"])) == []
