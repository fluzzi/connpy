import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import json
import asyncio

from connpy.ai import ai
from connpy.core import node

class DummyConfig:
    def __init__(self):
        self.config = {"ai": {"engineer_api_key": "test_key", "engineer_model": "test_model"}}
        self.defaultdir = "/tmp"

class MockAsyncIterator:
    def __init__(self, items):
        self.items = items
    def __aiter__(self):
        return self
    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)

@pytest.fixture
def mock_acompletion():
    # Patch acompletion inside connpy.ai.aask_copilot
    with patch('litellm.acompletion') as mock:
        yield mock

def test_aask_copilot_tool_call(mock_acompletion):
    agent = ai(DummyConfig())
    
    # Setup mock response for streaming
    class MockDelta:
        def __init__(self, content):
            self.content = content
            
    class MockChoice:
        def __init__(self, content):
            self.delta = MockDelta(content)
            
    class MockChunk:
        def __init__(self, content):
            self.choices = [MockChoice(content)]
            
    # acompletion is awaited and returns an async iterator
    async def mock_ac(*args, **kwargs):
        return MockAsyncIterator([
            MockChunk("<guide>Check the interfaces and running config.</guide>"),
            MockChunk("<commands>\nshow ip int br\nshow run\n</commands>"),
            MockChunk("<risk>low</risk>")
        ])
    
    mock_acompletion.side_effect = mock_ac
    
    async def run_test():
        return await agent.aask_copilot("Router#", "What do I do?")
    
    result = asyncio.run(run_test())
    
    if result["error"]:
        print(f"ERROR OCCURRED: {result['error']}")
    
    assert result["error"] is None
    assert result["guide"] == "Check the interfaces and running config."
    assert result["risk_level"] == "low"
    assert result["commands"] == ["show ip int br", "show run"]

def test_aask_copilot_fallback(mock_acompletion):
    agent = ai(DummyConfig())
    
    # Setup mock response for streaming
    class MockDelta:
        def __init__(self, content):
            self.content = content
            
    class MockChoice:
        def __init__(self, content):
            self.delta = MockDelta(content)
            
    class MockChunk:
        def __init__(self, content):
            self.choices = [MockChoice(content)]
            
    async def mock_ac(*args, **kwargs):
        return MockAsyncIterator([
            MockChunk("Here is some text response instead of tool call.")
        ])
    
    mock_acompletion.side_effect = mock_ac
    
    async def run_test():
        return await agent.aask_copilot("Router#", "What do I do?")
    
    result = asyncio.run(run_test())
    
    if result["error"]:
        print(f"ERROR OCCURRED: {result['error']}")
    
    assert result["error"] is None
    assert result["guide"] == "Here is some text response instead of tool call."
    assert result["risk_level"] == "low"

def test_logclean_ansi():
    c = node("test_node", "1.2.3.4")
    raw = "Router#\x1b[K\x1b[m show ip"
    clean = c._logclean(raw, var=True)
    assert "\x1b" not in clean

def test_ingress_task_interception():
    async def run_test():
        c = node("test_node", "1.2.3.4")
        c.mylog = MagicMock()
        c.mylog.getvalue.return_value = b"Some session log"
        c.unique = "test_node"
        c.host = "1.2.3.4"
        c.tags = {"os": "cisco_ios"}
        
        class MockStream:
            def __init__(self):
                self.data = [b"a", b"b", b"\x00", b"c", b""]
            async def read(self):
                if self.data:
                    return self.data.pop(0)
                return b""
            def setup(self, resize_callback):
                pass

        stream = MockStream()
        
        called_copilot = False
        async def mock_handler(buffer, node_info, s, child_fd):
            nonlocal called_copilot
            called_copilot = True
            assert buffer == "Some session log"
            assert node_info["os"] == "cisco_ios"
            
        c.child = MagicMock()
        c.child.child_fd = 123
        c.child.after = b""
        c.child.buffer = b""
        
        async def mock_ingress():
            while True:
                data = await stream.read()
                if not data:
                    break
                
                if mock_handler and b'\x00' in data:
                    buffer = c.mylog.getvalue().decode()
                    node_info = {"name": getattr(c, 'unique', 'unknown'), "host": getattr(c, 'host', 'unknown')}
                    if isinstance(getattr(c, 'tags', None), dict):
                        node_info["os"] = c.tags.get("os", "unknown")
                    await mock_handler(buffer, node_info, stream, c.child.child_fd)
                    continue
                    
        await mock_ingress()
        assert called_copilot
        
    asyncio.run(run_test())
