"""日志适配、第三方 logging 桥接与进程级日志初始化。"""

import logging
import sys
from typing import Any

from loguru import logger as loguru_logger

from yuxi.infrastructure.runtime_settings import get_runtime_dir
from yuxi.shared.datetime import shanghai_now

DATETIME = shanghai_now().strftime("%Y-%m-%d")
LOG_DIR = get_runtime_dir() / "logs"
LOG_FILE = str(LOG_DIR / f"yuxi-{DATETIME}.log")
_LOGURU_LEVELS = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


def setup_logger(level: str = "DEBUG", console: bool = True) -> Any:
    """使用 loguru 设置日志记录器。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 移除默认的 handler
    loguru_logger.remove()

    # 文件保留完整来源，控制台保持紧凑。
    loguru_logger.add(
        LOG_FILE,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} - {level} - {file}:{line} - {message}",
        encoding="utf-8",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        diagnose=False,
        enqueue=True,
    )

    if console:
        loguru_logger.add(
            sys.stderr,
            level=level,
            format=("<green>{time:MM-DD HH:mm:ss}</green> <level>{level}</level> <cyan>{file}:{line}</cyan>: <level>{message}</level>"),
            colorize=True,
            diagnose=False,
            enqueue=True,
        )

    return loguru_logger


def setup_logging() -> None:
    """统一标准 logging 与 uvicorn 的进程级输出格式。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    uvicorn_logger = logging.getLogger("uvicorn")
    logging.getLogger("uvicorn.access").handlers.clear()

    formatter = logging.Formatter(fmt="%(asctime)s %(levelname)s: %(message)s", datefmt="%m-%d %H:%M:%S")
    for handler in uvicorn_logger.handlers:
        handler.setFormatter(formatter)


class LoguruHandler(logging.Handler):
    """将 Python logging 记录桥接到 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        level = _LOGURU_LEVELS.get(record.levelno, "DEBUG")
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        loguru_logger.opt(depth=1, exception=record.exc_info).log(level, msg)


def _setup_logging_bridge() -> None:
    """把第三方库的 warning 及以上日志转发到 loguru。"""
    loguru_handler = LoguruHandler()
    loguru_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    loguru_handler.setFormatter(formatter)

    for lib in ("httpx", "openai", "neo4j", "urllib3"):
        lib_logger = logging.getLogger(lib)
        lib_logger.addHandler(loguru_handler)
        lib_logger.setLevel(logging.WARNING)
        lib_logger.propagate = False


logger = setup_logger()
_setup_logging_bridge()

__all__ = ["logger"]
