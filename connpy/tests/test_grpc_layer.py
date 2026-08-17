import pytest
import grpc
import json
import os
import threading
from unittest.mock import MagicMock, patch
from concurrent import futures
from connpy.grpc_layer import server, connpy_pb2, connpy_pb2_grpc, stubs
from connpy.services.exceptions import ConnpyError

class MockContext:
    def abort(self, code, details):
        raise Exception(f"gRPC Abort: {code} - {details}")

# --- UNIT TESTS (with mocks) ---

class TestNodeServicerNaming:
    @pytest.fixture
    def servicer(self, populated_config):
        return server.NodeServicer(populated_config)

    @patch("connpy.core.node")
    def test_interact_node_uses_passed_name(self, mock_node, servicer):
        # Setup request with custom name
        params = {"name": "custom-node-name@test", "host": "1.2.3.4", "protocol": "ssh"}
        request = connpy_pb2.InteractRequest(
            id="dynamic",
            connection_params_json=json.dumps(params)
        )
        
        # Mock node to allow _connect
        mock_node_instance = MagicMock()
        mock_node_instance._connect.return_value = True
        mock_node.return_value = mock_node_instance

        # We only need the first iteration of the generator to check naming
        gen = servicer.interact_node(iter([request]), MockContext())
        next(gen) # Skip the success response

        # Verify that node() was called with the custom name
        mock_node.assert_called()
        found = False
        for call in mock_node.call_args_list:
            if call.args[0] == "custom-node-name@test":
                found = True
                break
        assert found

    @patch("connpy.core.node")
    def test_interact_node_fallback_naming(self, mock_node, servicer):
        # Setup request without custom name but with host
        params = {"host": "my-instance", "protocol": "ssm"}
        request = connpy_pb2.InteractRequest(
            id="dynamic",
            connection_params_json=json.dumps(params)
        )
        
        mock_node_instance = MagicMock()
        mock_node_instance._connect.return_value = True
        mock_node.return_value = mock_node_instance

        gen = servicer.interact_node(iter([request]), MockContext())
        next(gen)

        # Verify fallback name: dynamic-{host}@remote
        found = False
        for call in mock_node.call_args_list:
            if call.args[0] == "dynamic-my-instance@remote":
                found = True
                break
        assert found

class TestStubsMessageFormatting:
    @patch("termios.tcsetattr")
    @patch("termios.tcgetattr")
    @patch("tty.setraw")
    @patch("os.read")
    @patch("select.select")
    def test_connect_dynamic_msg_formatting_ssm(self, mock_select, mock_read, mock_setraw, mock_getattr, mock_setattr):
        from connpy.grpc_layer.stubs import NodeStub

        mock_getattr.return_value = [0, 0, 0, 0, 0, 0, [0] * 32]
        mock_channel = MagicMock()
        stub = NodeStub(mock_channel, "localhost:8048")

        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.stdout_data = b''
        stub.stub.interact_node.return_value = iter([mock_resp])        
        with patch("connpy.printer.success") as mock_success:
            with patch("sys.stdin.fileno", return_value=0):
                mock_select.return_value = ([], [], [])
                params = {"protocol": "ssm", "host": "i-12345", "name": "my-ssm-node@aws"}
                
                with patch("select.select", side_effect=KeyboardInterrupt):
                    try:
                        stub.connect_dynamic(params)
                    except KeyboardInterrupt:
                        pass
                
                mock_success.assert_called()
                msg = mock_success.call_args[0][0]
                assert "Connected to my-ssm-node@aws" in msg
                assert "at i-12345" in msg
                assert ":22" not in msg
                assert "via: ssm" in msg


# --- INTEGRATION TESTS (Real Server/Stub Communication) ---

class TestGRPCIntegration:
    @pytest.fixture
    def grpc_server(self, populated_config):
        """Starts a local gRPC server for integration testing."""
        srv = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
        
        # Register services
        connpy_pb2_grpc.add_NodeServiceServicer_to_server(server.NodeServicer(populated_config), srv)
        connpy_pb2_grpc.add_ProfileServiceServicer_to_server(server.ProfileServicer(populated_config), srv)
        connpy_pb2_grpc.add_ConfigServiceServicer_to_server(server.ConfigServicer(populated_config), srv)
        connpy_pb2_grpc.add_ExecutionServiceServicer_to_server(server.ExecutionServicer(populated_config), srv)
        connpy_pb2_grpc.add_ImportExportServiceServicer_to_server(server.ImportExportServicer(populated_config), srv)
        connpy_pb2_grpc.add_AIServiceServicer_to_server(server.AIServicer(populated_config), srv)
        
        port = srv.add_insecure_port('127.0.0.1:0')
        srv.start()
        yield f"127.0.0.1:{port}"
        srv.stop(0)

    @pytest.fixture
    def channel(self, grpc_server):
        with grpc.insecure_channel(grpc_server) as channel:
            yield channel

    @pytest.fixture
    def node_stub(self, channel):
        return stubs.NodeStub(channel, "localhost")

    @pytest.fixture
    def profile_stub(self, channel):
        return stubs.ProfileStub(channel, "localhost")

    @pytest.fixture
    def config_stub(self, channel):
        return stubs.ConfigStub(channel, "localhost")

    @pytest.fixture
    def ai_stub(self, channel):
        return stubs.AIStub(channel, "localhost")

    def test_list_nodes_integration(self, node_stub):
        nodes = node_stub.list_nodes()
        assert "router1" in nodes
        assert "server1@office" in nodes

    def test_get_node_details_integration(self, node_stub):
        details = node_stub.get_node_details("router1")
        assert details["host"] == "10.0.0.1"

    def test_node_not_found_integration(self, node_stub):
        with pytest.raises(ConnpyError) as exc:
            node_stub.get_node_details("non-existent")
        assert "Node 'non-existent' not found." in str(exc.value)

    def test_list_profiles_integration(self, profile_stub):
        profiles = profile_stub.list_profiles()
        assert "office-user" in profiles

    def test_get_settings_integration(self, config_stub):
        settings = config_stub.get_settings()
        assert "idletime" in settings

    def test_update_setting_integration(self, config_stub):
        config_stub.update_setting("idletime", 99)
        settings = config_stub.get_settings()
        assert settings["idletime"] == 99

    def test_list_mcp_servers_integration(self, ai_stub):
        ai_stub.configure_mcp("test-mcp", url="http://localhost:8080", enabled=True)
        servers = ai_stub.list_mcp_servers()
        assert "test-mcp" in servers
        assert servers["test-mcp"]["url"] == "http://localhost:8080"

    def test_add_delete_node_integration(self, node_stub):
        node_stub.add_node("integration-test-node", {"host": "9.9.9.9"})
        assert "integration-test-node" in node_stub.list_nodes()
        node_stub.delete_node("integration-test-node")
        assert "integration-test-node" not in node_stub.list_nodes()

    def test_import_yaml_integration(self, channel, node_stub):
        import yaml
        from connpy.grpc_layer import stubs
        stub = stubs.ImportExportStub(channel, "localhost")
        
        # ImportExportService expects a flat dict of nodes, not a full config structure
        inventory = {
            "imported-node": {"host": "8.8.8.8", "protocol": "ssh", "type": "connection"}
        }
        yaml_content = yaml.dump(inventory)
        
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name
            
        try:
            stub.import_from_file(temp_path)
            # Verify the node was imported and is visible via NodeStub
            nodes = node_stub.list_nodes()
            assert "imported-node" in nodes
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @patch("sys.stdin.fileno", return_value=0)
    @patch("termios.tcsetattr")
    @patch("termios.tcgetattr")
    @patch("tty.setraw")
    @patch("os.write")
    def test_handle_remote_copilot_command_execution_loop(self, mock_os_write, mock_setraw, mock_getattr, mock_setattr, mock_fileno):
        import queue
        from connpy.grpc_layer.stubs import NodeStub
        from connpy.cli.terminal_ui import CopilotInterface

        mock_getattr.return_value = [0, 0, 0, 0, 0, 0, [0] * 32]
        mock_channel = MagicMock()
        stub = NodeStub(mock_channel, "localhost:8048")

        initial_node_info = {
            "name": "r1",
            "context_blocks": [[0, 10, "router#"]]
        }
        res = connpy_pb2.InteractResponse(
            copilot_prompt=True,
            copilot_node_info_json=json.dumps(initial_node_info)
        )

        request_queue = queue.Queue()
        response_queue = queue.Queue()
        client_buffer_bytes = bytearray(b"router# ")
        pause_gen = MagicMock()
        resume_gen = MagicMock()
        old_tty = [0] * 7

        call_count = 0
        received_blocks_per_call = []

        async def mock_run_session(raw_bytes, node_info, on_ai_call, blocks):
            nonlocal call_count
            call_count += 1
            received_blocks_per_call.append(list(blocks))
            if call_count == 1:
                # Step 1: User approves commands
                # Simulate server putting stdout data and next copilot prompt
                updated_node_info = {
                    "name": "r1",
                    "context_blocks": [[0, 10, "router#"], [10, 30, "router# show ip route"]]
                }
                response_queue.put(connpy_pb2.InteractResponse(stdout_data=b"show ip route\r\n10.0.0.0/24\r\nrouter# "))
                response_queue.put(connpy_pb2.InteractResponse(
                    copilot_prompt=True,
                    copilot_node_info_json=json.dumps(updated_node_info)
                ))
                return "send_all", ["show ip route"], None
            else:
                # Step 2: Second turn, mission completes or user cancels
                return "cancel", None, None

        with patch.object(CopilotInterface, "run_session", side_effect=mock_run_session):
            stub._handle_remote_copilot(
                res, request_queue, response_queue, client_buffer_bytes,
                pause_gen, resume_gen, old_tty
            )

        # Assert that run_session was called twice
        assert call_count == 2
        # First call should have initial blocks
        assert len(received_blocks_per_call[0]) == 1
        # Second call should have updated blocks received from server
        assert len(received_blocks_per_call[1]) == 2
        assert received_blocks_per_call[1][1][2] == "router# show ip route"
        # Assert client_buffer_bytes was updated with stdout_data
        assert b"10.0.0.0/24" in bytes(client_buffer_bytes)
        # Assert action sent to server
        reqs = []
        while not request_queue.empty():
            reqs.append(request_queue.get_nowait())
        assert reqs[0].copilot_action == "custom:show ip route"
        assert reqs[1].copilot_action == "cancel"

