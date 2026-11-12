try:
    from importlib.metadata import version

    __version__ = version("yuxi")
except Exception:
    __version__ = "unknown"


def get_version():
    """返回后端版本。"""
    return __version__
