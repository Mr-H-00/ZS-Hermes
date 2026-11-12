from yuxi.modules.agents.runtime.middlewares.cooperation import CooperationState


class ChatBotState(CooperationState):
    """清理图也保留协作中间件拥有的 checkpoint 状态。"""
