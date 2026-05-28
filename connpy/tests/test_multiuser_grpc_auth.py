import os
import pytest
import grpc
from concurrent import futures
from google.protobuf.empty_pb2 import Empty

from connpy.grpc_layer import server, connpy_pb2, connpy_pb2_grpc, stubs
from connpy.grpc_layer.user_registry import UserRegistry
from connpy.services.provider import ServiceProvider
from connpy.configfile import configfile

@pytest.fixture
def test_config_dir(tmp_path):
    """Creates a temporary config directory for testing gRPC auth."""
    config_dir = tmp_path / "conn_config"
    config_dir.mkdir()
    
    # Initialize basic config file inside it
    from connpy.configfile import configfile
    conf_file = os.path.join(str(config_dir), "config.yaml")
    configfile(conf=conf_file)
    return config_dir

@pytest.fixture
def registry(test_config_dir):
    """Initializes UserRegistry."""
    return UserRegistry(str(test_config_dir))

@pytest.fixture
def auth_grpc_server(test_config_dir, registry):
    """Starts an authenticated local gRPC server for integration testing."""
    srv = grpc.server(
        futures.ThreadPoolExecutor(max_workers=5),
        interceptors=[server.AuthInterceptor(registry)]
    )
    
    fallback_provider = ServiceProvider(configfile(conf=os.path.join(str(test_config_dir), "config.yaml")), mode="local")
    
    # Register services
    connpy_pb2_grpc.add_NodeServiceServicer_to_server(server.NodeServicer(fallback_provider, registry=registry), srv)
    connpy_pb2_grpc.add_AuthServiceServicer_to_server(server.AuthServicer(registry), srv)
    
    port = srv.add_insecure_port('127.0.0.1:0')
    srv.start()
    yield f"127.0.0.1:{port}"
    srv.stop(0)

@pytest.fixture
def channel(auth_grpc_server):
    with grpc.insecure_channel(auth_grpc_server) as channel:
        yield channel


class TestGRPCAuthentication:
    def test_backward_compatibility_no_users(self, channel, registry):
        """Verifies that if no users are registered, gRPC calls proceed without authentication."""
        assert registry.has_users() is False
        
        # Calling NodeService list_nodes should succeed without any authorization metadata
        stub = connpy_pb2_grpc.NodeServiceStub(channel)
        req = connpy_pb2.FilterRequest()
        res = stub.list_nodes(req)
        assert res is not None

    def test_login_and_authenticated_calls(self, channel, registry):
        """Tests user creation, login to retrieve JWT, and using JWT to access protected endpoints."""
        username = "alice"
        password = "alicepassword"
        
        # 1. Register a user in the registry
        registry.user_service.create_user(username, password)
        assert registry.has_users() is True
        
        # 2. Try unauthenticated call - must fail with UNAUTHENTICATED
        node_stub = connpy_pb2_grpc.NodeServiceStub(channel)
        req = connpy_pb2.FilterRequest()
        with pytest.raises(grpc.RpcError) as exc:
            node_stub.list_nodes(req)
        assert exc.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert "Authorization token is missing" in exc.value.details()

        # 3. Call login endpoint (open method) - must succeed
        auth_stub = connpy_pb2_grpc.AuthServiceStub(channel)
        login_req = connpy_pb2.LoginRequest(username=username, password=password)
        login_res = auth_stub.login(login_req)
        
        assert login_res.username == username
        assert isinstance(login_res.token, str)
        assert login_res.expires_at > 0
        
        # 4. Make authenticated call using Bearer token - must succeed
        metadata = [("authorization", f"Bearer {login_res.token}")]
        res = node_stub.list_nodes(req, metadata=metadata)
        assert res is not None

    def test_login_invalid_credentials(self, channel, registry):
        """Verifies login fails and returns UNAUTHENTICATED for incorrect credentials."""
        registry.user_service.create_user("bob", "bobpass")
        
        auth_stub = connpy_pb2_grpc.AuthServiceStub(channel)
        login_req = connpy_pb2.LoginRequest(username="bob", password="wrongpassword")
        
        with pytest.raises(grpc.RpcError) as exc:
            auth_stub.login(login_req)
        assert exc.value.code() == grpc.StatusCode.UNAUTHENTICATED
        assert "Invalid username or password" in exc.value.details()

    def test_change_password(self, channel, registry):
        """Tests changing password via gRPC and verifying old password no longer works."""
        username = "charlie"
        registry.user_service.create_user(username, "oldpass")
        
        auth_stub = connpy_pb2_grpc.AuthServiceStub(channel)
        
        # 1. Login with old password to get token
        login_res = auth_stub.login(connpy_pb2.LoginRequest(username=username, password="oldpass"))
        token = login_res.token
        
        # 2. Change password via gRPC using the token
        metadata = [("authorization", f"Bearer {token}")]
        change_req = connpy_pb2.ChangePasswordRequest(old_password="oldpass", new_password="newpass")
        auth_stub.change_password(change_req, metadata=metadata)
        
        # 3. Logging in with old password must fail
        with pytest.raises(grpc.RpcError) as exc:
            auth_stub.login(connpy_pb2.LoginRequest(username=username, password="oldpass"))
        assert exc.value.code() == grpc.StatusCode.UNAUTHENTICATED
        
        # 4. Logging in with new password must succeed
        login_res_new = auth_stub.login(connpy_pb2.LoginRequest(username=username, password="newpass"))
        assert login_res_new.token is not None
