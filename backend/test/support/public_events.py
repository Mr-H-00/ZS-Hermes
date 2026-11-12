"""测试按完整 SSE frame 读取公开事件和独立 cursor。"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

VALIDATOR = Draft202012Validator(json.loads((Path(__file__).with_name("openai_agents_events.schema.json")).read_text()))


async def read_events(response):
    """字段顺序不限；只在 frame 边界消费 event/id/data。"""
    cursor, name, data = None, None, []
    async for line in response.aiter_lines():
        if line.startswith("id:"):
            cursor = line[3:].lstrip()
        elif line.startswith("event:"):
            name = line[6:].lstrip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            event = json.loads("\n".join(data))
            assert name == event["type"]
            if event["type"].startswith("agent."):
                VALIDATOR.validate(event)
            yield cursor, event
            cursor, name, data = None, None, []
