from dataclasses import dataclass

from yuxi.modules.agents.runtime.context import BaseContext


@dataclass(kw_only=True)
class ChatBotContext(BaseContext):
    """统一会话的执行配置。"""
