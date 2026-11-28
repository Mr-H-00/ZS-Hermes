__all__ = ["select_model"]


def __getattr__(name: str):
    if name == "select_model":
        from zs.modules.models.chat import select_model

        return select_model

    raise AttributeError(f"module 'zs.modules.models' has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
