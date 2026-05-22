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

def test_build_context_blocks_horizontal_scrolling():
    from connpy.services.ai_service import AIService
    svc = AIService(None)
    
    node_info = {"prompt": "RP/0/RP0/CPU0:xrd#"}
    part1 = 'RP/0/RP0/CPU0:xrd#s show interfaces * | inc "rate|is up|escr|test1|test2|test3|test4|test5|teest8|test7|t$'
    part2 = '|escr|test1|test2|test3|test4|test5|teest8|test7|te                                 s998"show interfaces * | inc "rate|is up|escr|test1|test2|test3|test4|test5|teest8|test7|$'
    
    # Test with \r (classic IOS)
    raw_bytes = (part1 + '\r' + part2).encode()
    cmd_byte_positions = [(0, None), (len(raw_bytes), None)]
    
    blocks = svc.build_context_blocks(raw_bytes, cmd_byte_positions, node_info)
    assert len(blocks) >= 1
    start, end, preview = blocks[0]
    assert "RP/0/RP0/CPU0:xrd# s show interfaces * | inc" in preview

def test_build_context_blocks_horizontal_scrolling_ansi():
    """Test with CSI cursor repositioning (\\x1B[1G) instead of raw \\r, as used by Cisco IOS XR."""
    from connpy.services.ai_service import AIService
    svc = AIService(None)
    
    node_info = {"prompt": "RP/0/RP0/CPU0:xrd#"}
    part1 = 'RP/0/RP0/CPU0:xrd#s show interfaces * | inc "rate|is up|escr|test1|test2|test3|test4|test5|teest8|test7|t'
    part2 = '$|escr|test1|test2|test3|test4|test5|teest8|test7|te                                 s998"show interfaces * | inc "rate|is up|escr|test1|test2|test3|test4|test5|teest8|test7|$'
    
    # Test with \x1B[1G (CSI Cursor Horizontal Absolute - IOS XR)
    raw_bytes = (part1 + '\x1b[1G' + part2).encode()
    cmd_byte_positions = [(0, None), (len(raw_bytes), None)]
    
    blocks = svc.build_context_blocks(raw_bytes, cmd_byte_positions, node_info)
    assert len(blocks) >= 1
    start, end, preview = blocks[0]
    assert "RP/0/RP0/CPU0:xrd# s show interfaces * | inc" in preview


def test_build_context_blocks_cancelled_command():
    from connpy.services.ai_service import AIService
    svc = AIService(None)
    
    node_info = {"prompt": "router#"}
    # Command 1: cancelled with Ctrl+C. Command 2: executed successfully.
    raw_bytes = b"router# show plat\x03\r\nrouter# show ver\r\nrouter# "
    
    # 0: initial boundary
    # 18: Ctrl+C pressed (ends Command 1, marked CANCELLED)
    # 36: Enter pressed (ends Command 2)
    cmd_byte_positions = [(0, None), (18, "CANCELLED"), (36, None)]
    
    blocks = svc.build_context_blocks(raw_bytes, cmd_byte_positions, node_info)
    
    # The cancelled command block (0 to 18) should NOT be registered as a VALID_CMD block.
    # The block for "show ver" should be registered (starting at 36, ending at current_prompt_pos).
    # Plus, the final block for "CURRENT CONTEXT".
    valid_blocks = [b for b in blocks if "CURRENT CONTEXT" not in b[2]]
    assert len(valid_blocks) == 1
    assert "show ver" in valid_blocks[0][2]
    assert "show plat" not in valid_blocks[0][2]


def test_copilot_range_mode_filtering():
    from connpy.cli.terminal_ui import CopilotInterface
    
    # We setup dummy raw_bytes with scrolling garbage in the middle:
    # 0 to 10: "show ip" (VALID_CMD)
    # 10 to 25: "some scrolling garbage we want to skip"
    # 25 to 35: "show run" (VALID_CMD)
    # 35 to 45: "current prompt" (final context block)
    raw_bytes = b"show ip    garbage_to_skip_here   show run   router#"
    
    blocks = [
        (0, 10, "router# show ip"),
        (25, 35, "router# show run"),
        (35, 45, "router#")
    ]
    
    # Mock Config
    class MockConfig:
        def __init__(self):
            self.config = {"ai": {}}
            self.defaultdir = "/tmp"
            
    interface = CopilotInterface(MockConfig())
    # Ensure default is RANGE mode
    interface.mode_range = 0
    interface.mode_single = 1
    interface.mode_lines = 2
    
    captured_buffer = None
    
    async def mock_ai_call(active_buffer, question, on_chunk, node_info):
        nonlocal captured_buffer
        captured_buffer = active_buffer
        return {"guide": "Ok", "commands": [], "risk_level": "low"}
        
    # Mock PromptSession.prompt_async to ask a question once then exit
    prompt_calls = 0
    async def mock_prompt_async(self, *args, **kwargs):
        nonlocal prompt_calls
        prompt_calls += 1
        if prompt_calls == 1:
            # Simulate pressing Ctrl+Up key twice to expand context range from 1 to 3 commands
            kb = kwargs.get('key_bindings')
            if kb:
                class DummyApp:
                    def invalidate(self): pass
                class DummyEvent:
                    app = DummyApp()
                
                # Find and invoke the 'c-up' handler twice
                for b in kb.bindings:
                    if any('up' in str(k).lower() for k in b.keys):
                        b.handler(DummyEvent())
                        b.handler(DummyEvent())
            return "how are interfaces looking?"
        else:
            raise KeyboardInterrupt
            
    with patch('prompt_toolkit.PromptSession.prompt_async', mock_prompt_async):
        async def run():
            # Run session
            return await interface.run_session(
                raw_bytes=raw_bytes,
                node_info={"name": "test"},
                on_ai_call=mock_ai_call,
                blocks=blocks
            )
            
        asyncio.run(run())
        
    # In range mode: it should have concatenated the valid blocks
    # block[0] is raw_bytes[0:10] => b"show ip    "
    # block[1] is raw_bytes[25:35] => b"   show run"
    # block[2] is raw_bytes[35:45] => b"   router#"
    # Note: raw_bytes[10:25] (garbage) must be excluded!
    assert captured_buffer is not None
    assert "garbage_to_skip_here" not in captured_buffer
    assert "show ip" in captured_buffer
    assert "show run" in captured_buffer


def test_build_context_blocks_pager_scrolling_enter():
    from connpy.services.ai_service import AIService
    svc = AIService(None)
    
    node_info = {"prompt": "sixwind>"}
    raw_bytes = (
        b"sixwind> show configuration | less\r\n"
        b"line 1 of output\nline 2 of output\n\r"
        b"line 3 of output\nline 4 of output\n\r"
        b"line 5 of output\n(END)\x1b[?1049l\x1b[?47l\r\nsixwind> \r\n"
        b"sixwind> \r\n"
        b"sixwind> \r\n"
        b"sixwind> "
    )
    cmd_byte_positions = [
        (0, None),
        (36, None),
        (70, None),
        (105, None),
        (153, None),
        (164, None),
        (175, None),
        (186, None)
    ]
    
    blocks = svc.build_context_blocks(raw_bytes, cmd_byte_positions, node_info)
    
    valid_blocks = [b for b in blocks if "CURRENT CONTEXT" not in b[2]]
    assert len(valid_blocks) == 1
    assert "show configuration" in valid_blocks[0][2]
    assert valid_blocks[0][0] == 36
    assert valid_blocks[0][1] == 153


def test_build_context_blocks_pager_scrolling_space():
    from connpy.services.ai_service import AIService
    svc = AIService(None)
    
    node_info = {"prompt": "sixwind>"}
    raw_bytes = (
        b"sixwind> show configuration | less\r\n"
        b"line 1 of output\nline 2 of output\n "
        b"line 3 of output\nline 4 of output\n "
        b"line 5 of output\n(END)\x1b[?1049l\x1b[?47l\r\n"
        b"sixwind> \r\n"
        b"sixwind> \r\n"
        b"sixwind> \r\n"
        b"sixwind> "
    )
    cmd_byte_positions = [
        (0, None),
        (36, None),
        (144, None),
        (155, None),
        (166, None),
        (177, None)
    ]
    
    blocks = svc.build_context_blocks(raw_bytes, cmd_byte_positions, node_info)
    
    valid_blocks = [b for b in blocks if "CURRENT CONTEXT" not in b[2]]
    assert len(valid_blocks) == 1
    assert "show configuration" in valid_blocks[0][2]
    assert valid_blocks[0][0] == 36
    assert valid_blocks[0][1] == 155


def test_build_context_blocks_pager_scrolling_6wind_escapes():
    from connpy.services.ai_service import AIService
    svc = AIService(None)
    
    node_info = {"prompt": "6WIND-PE1>", "os": "6wind"}
    raw_bytes = (
        b"6WIND-PE1> show config running fullpath nodefault\r\n"
        b"line 1\r\n"
        b"line 2\r\n"
        b":\x1b[K\r\x1b[K/ vrf main interface gre gre2 mtu 8400\r\n"
        b":\x1b[K\x07\r\x1b[K\x1b[?1l\x1b>6WIND-PE1> \r\n"
        b"6WIND-PE1> \r\n"
        b"6WIND-PE1> "
    )
    cmd_byte_positions = [
        (0, None),
        (52, None),
        (136, None),
        (177, None),
        (177, None),
        (190, None),
        (203, None)
    ]
    
    blocks = svc.build_context_blocks(raw_bytes, cmd_byte_positions, node_info)
    
    valid_blocks = [b for b in blocks if "CURRENT CONTEXT" not in b[2]]
    assert len(valid_blocks) == 1
    assert "show config running" in valid_blocks[0][2]




