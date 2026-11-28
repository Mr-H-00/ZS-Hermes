import sys


def test_import_zs_does_not_eagerly_import_knowledge(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delitem(sys.modules, "zs", raising=False)
    monkeypatch.delitem(sys.modules, "zs.modules.knowledge", raising=False)

    import zs

    assert zs.get_version() == zs.__version__
    assert "zs.modules.knowledge" not in sys.modules
