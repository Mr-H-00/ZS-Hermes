import sys


def test_import_yuxi_does_not_eagerly_import_knowledge(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delitem(sys.modules, "yuxi", raising=False)
    monkeypatch.delitem(sys.modules, "yuxi.modules.knowledge", raising=False)

    import yuxi

    assert yuxi.get_version() == yuxi.__version__
    assert "yuxi.modules.knowledge" not in sys.modules
