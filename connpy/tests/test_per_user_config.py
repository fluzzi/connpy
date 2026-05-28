import os
import pytest
from connpy.grpc_layer.server import NodeServicer, _current_user
from connpy.grpc_layer.user_registry import UserRegistry
from connpy.services.provider import ServiceProvider

@pytest.fixture
def test_config_dir(tmp_path):
    """Creates a temporary config directory for testing user registry."""
    config_dir = tmp_path / "conn_config"
    config_dir.mkdir()
    return config_dir

@pytest.fixture
def registry(test_config_dir):
    """Initializes UserRegistry pointing to a temporary directory."""
    return UserRegistry(str(test_config_dir))

def test_dynamic_routing_isolation(test_config_dir, registry):
    """Verifies that NodeServicer routes list_nodes to the correct user configuration based on _current_user ContextVar."""
    # Setup fallback provider
    from connpy.configfile import configfile
    conf_file = os.path.join(registry.user_service.config_dir, "config.yaml")
    config = configfile(conf=conf_file)
    fallback_provider = ServiceProvider(config, mode="local")
    
    # Create servicer with fallback and registry
    servicer = NodeServicer(fallback_provider, registry=registry)
    
    # Register two users
    u1 = "user1"
    u2 = "user2"
    registry.user_service.create_user(u1, "pass1")
    registry.user_service.create_user(u2, "pass2")
    
    p1 = registry.get_provider(u1)
    p2 = registry.get_provider(u2)
    
    # Add nodes to each user's provider
    p1.nodes.add_node("node-for-user-1", {"host": "1.1.1.1"})
    p2.nodes.add_node("node-for-user-2", {"host": "2.2.2.2"})
    
    # Verify fallback is empty
    fallback_res = servicer.list_nodes(type('Request', (), {'filter_str': None, 'format_str': None})(), None)
    from connpy.grpc_layer.utils import from_value
    assert "node-for-user-1" not in from_value(fallback_res.data)
    assert "node-for-user-2" not in from_value(fallback_res.data)
    
    # Set context to User 1
    t1 = _current_user.set(u1)
    try:
        res1 = servicer.list_nodes(type('Request', (), {'filter_str': None, 'format_str': None})(), None)
        nodes1 = from_value(res1.data)
        assert "node-for-user-1" in nodes1
        assert "node-for-user-2" not in nodes1
    finally:
        _current_user.reset(t1)
        
    # Set context to User 2
    t2 = _current_user.set(u2)
    try:
        res2 = servicer.list_nodes(type('Request', (), {'filter_str': None, 'format_str': None})(), None)
        nodes2 = from_value(res2.data)
        assert "node-for-user-2" in nodes2
        assert "node-for-user-1" not in nodes2
    finally:
        _current_user.reset(t2)
