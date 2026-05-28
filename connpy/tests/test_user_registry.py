import os
import pytest
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


class TestUserRegistry:
    def test_has_users_empty(self, registry):
        """Verifies has_users is False when no users exist."""
        assert registry.has_users() is False

    def test_get_provider_returns_service_provider(self, registry):
        """Tests that get_provider lazy-loads a valid ServiceProvider instance."""
        username = "alice"
        registry.user_service.create_user(username, "password")
        
        assert registry.has_users() is True
        
        provider = registry.get_provider(username)
        assert isinstance(provider, ServiceProvider)
        assert provider.mode == "local"

    def test_get_provider_cached(self, registry):
        """Verifies that subsequent calls return the cached singleton instance."""
        username = "bob"
        registry.user_service.create_user(username, "password")
        
        p1 = registry.get_provider(username)
        p2 = registry.get_provider(username)
        
        assert p1 is p2  # must be exact same object reference

    def test_two_users_isolated(self, registry):
        """Ensures different users get completely separate ServiceProviders and configs."""
        u1 = "user1"
        u2 = "user2"
        
        registry.user_service.create_user(u1, "pass1")
        registry.user_service.create_user(u2, "pass2")
        
        p1 = registry.get_provider(u1)
        p2 = registry.get_provider(u2)
        
        assert p1 is not p2
        assert p1.config is not p2.config
        
        # Add a node for user1 and verify user2 is unaffected
        p1.nodes.add_node("node1", {"host": "1.1.1.1"})
        assert "node1" in p1.nodes.list_nodes()
        assert "node1" not in p2.nodes.list_nodes()

    def test_evict_clears_cache(self, registry):
        """Verifies that eviction deletes the cached provider from memory."""
        username = "evictuser"
        registry.user_service.create_user(username, "pass")
        
        p1 = registry.get_provider(username)
        assert username in registry._providers
        
        registry.evict(username)
        assert username not in registry._providers
        
        # Calling get_provider again spawns a new instance
        p2 = registry.get_provider(username)
        assert p1 is not p2

    def test_provider_hot_reload_on_external_change(self, registry):
        """Verifies that UserRegistry hot-reloads the provider if config.yaml is updated externally."""
        username = "charlie"
        registry.user_service.create_user(username, "password")
        
        # Initial load (no nodes)
        p1 = registry.get_provider(username)
        assert len(p1.nodes.list_nodes()) == 0
        
        # Resolve config.yaml file path
        conf_file = os.path.join(registry.server_config_dir, "users", username, "config.yaml")
        
        # Modify the config file physically on disk by appending a node
        from connpy.configfile import configfile
        cfg = configfile(conf=conf_file)
        cfg._connections_add(id="testnode", host="8.8.8.8")
        cfg._saveconfig(cfg.file)
        
        # Artificially increase mtime to force reload
        mtime = os.path.getmtime(conf_file)
        os.utime(conf_file, (mtime + 5.0, mtime + 5.0))
        
        # Fetch provider again
        p2 = registry.get_provider(username)
        
        # Verify it hot-reloaded and the new node is immediately visible
        assert p1 is not p2
        assert "testnode" in p2.nodes.list_nodes()

    def test_provider_hot_reload_fails_on_corrupt_file_keeps_old_provider(self, registry):
        """Verifies that UserRegistry keeps serving the old provider if disk config is corrupt."""
        username = "danny"
        registry.user_service.create_user(username, "password")
        
        # Initial load
        p1 = registry.get_provider(username)
        p1.nodes.add_node("nodeA", {"host": "2.2.2.2"})
        assert "nodeA" in p1.nodes.list_nodes()
        
        # Resolve config.yaml path
        conf_file = os.path.join(registry.server_config_dir, "users", username, "config.yaml")
        
        # Write corrupted content directly to config.yaml
        with open(conf_file, "w") as f:
            f.write("corrupt yaml content ::: invalid syntax :::")
            
        # Artificially increase mtime to force reload attempt
        mtime = os.path.getmtime(conf_file)
        os.utime(conf_file, (mtime + 5.0, mtime + 5.0))
        
        # Fetching provider again should fallback to old_provider instead of failing completely
        p2 = registry.get_provider(username)
        
        # Verify fallback
        assert p1 is p2
        assert "nodeA" in p2.nodes.list_nodes()
