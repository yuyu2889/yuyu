"""
pytest 配置。

关键点说明（面试可以讲）：
1. **为什么测试要用独立的数据库？**
   如果直接跑在开发库上，测试会真的插入/删除数据，
   污染开发数据，而且测试之间会互相影响（前一个用例建的用户
   让后一个用例的"注册成功"断言失败）。
   专业做法是用独立的测试库，每个用例前后清理。

2. **pytest-asyncio 的 asyncio_mode = auto**：
   这样写 async def test_xxx 时不需要每个都加 @pytest.mark.asyncio 装饰器。

3. **测试分层**：
   - 单元测试（tests/unit/）：测纯函数，不需要数据库，跑得飞快
   - 集成测试（tests/integration/）：测接口，需要数据库
   这个划分让你可以"写完一个函数立刻跑单元测试验证"，
   而不是等整个环境起来。
"""
import asyncio
import sys
from pathlib import Path

import pytest

# 把 backend 目录加入 sys.path，让测试能 import app.*
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="session")
def event_loop():
    """
    会话级事件循环。

    pytest-asyncio 默认每个用例创建一个新的事件循环，
    但 SQLAlchemy 的异步引擎绑定在创建它的事件循环上，
    跨循环复用会报 "attached to a different loop"。
    用会话级 loop 可以避免这个问题。
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
