"""进程入口的环境文件加载。"""

from dotenv import load_dotenv


def load_environment() -> None:
    """在业务配置读取前加载本地环境。"""
    load_dotenv(".env", override=True)
