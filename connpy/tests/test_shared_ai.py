import os
import time
import pytest
import yaml
from connpy.configfile import configfile
from connpy.grpc_layer.user_registry import UserRegistry
from connpy.services.provider import ServiceProvider

@pytest.fixture
def temp_config_dir(tmp_path):
    """Creates a temporary config directory for testing."""
    config_dir = tmp_path / "conn_shared_test"
    config_dir.mkdir()
    return config_dir

def test_shared_ai_deep_merge(temp_config_dir):
    """Test get_effective_setting deep merge logic for 'ai' settings."""
    shared_dir = os.path.join(temp_config_dir, "shared")
    user_dir = os.path.join(temp_config_dir, "user")
    os.makedirs(shared_dir, exist_ok=True)
    os.makedirs(user_dir, exist_ok=True)
    
    shared_path = os.path.join(shared_dir, "config.yaml")
    user_path = os.path.join(user_dir, "config.yaml")
    
    # Write shared configuration
    shared_data = {
        "config": {
            "theme": "dark",
            "case": False,
            "ai": {
                "engineer_model": "shared-eng-model",
                "architect_model": "shared-arch-model",
                "engineer_api_key": "shared-key",
                "mcp_servers": {
                    "global-server": {
                        "url": "http://global-server/sse",
                        "enabled": True
                    },
                    "override-server": {
                        "url": "http://override-shared/sse",
                        "enabled": True
                    }
                }
            }
        },
        "connections": {},
        "profiles": {}
    }
    with open(shared_path, "w") as f:
        yaml.safe_dump(shared_data, f)
        
    # Write user configuration with overrides
    user_data = {
        "config": {
            "case": True,
            "ai": {
                "engineer_model": "user-custom-eng-model",
                "mcp_servers": {
                    "override-server": {
                        "enabled": False
                    },
                    "user-server": {
                        "url": "http://user-server/sse",
                        "enabled": True
                    }
                }
            }
        },
        "connections": {},
        "profiles": {}
    }
    with open(user_path, "w") as f:
        yaml.safe_dump(user_data, f)

    # Initialize configfile instances
    shared_config = configfile(conf=shared_path)
    user_config = configfile(conf=user_path, shared_config=shared_config)
    
    # Verify non-inheritable settings (theme, case)
    assert user_config.get_effective_setting("case") is True
    assert user_config.get_effective_setting("theme") is None  # Should NOT inherit "theme"

    # Verify AI setting deep merge
    effective_ai = user_config.get_effective_setting("ai")
    
    # Model override
    assert effective_ai.get("engineer_model") == "user-custom-eng-model"
    # Model inheritance
    assert effective_ai.get("architect_model") == "shared-arch-model"
    # API key inheritance
    assert effective_ai.get("engineer_api_key") == "shared-key"
    
    # MCP Servers merge
    mcp = effective_ai.get("mcp_servers", {})
    # Inherited server
    assert "global-server" in mcp
    assert mcp["global-server"]["url"] == "http://global-server/sse"
    assert mcp["global-server"]["enabled"] is True
    
    # Merged & overridden server
    assert "override-server" in mcp
    assert mcp["override-server"]["url"] == "http://override-shared/sse"  # inherited
    assert mcp["override-server"]["enabled"] is False  # overridden
    
    # User-only server
    assert "user-server" in mcp
    assert mcp["user-server"]["url"] == "http://user-server/sse"

def test_registry_injection_and_hot_reload(temp_config_dir):
    """Test that UserRegistry correctly injects shared config and hot-reloads it when it changes on disk."""
    registry = UserRegistry(str(temp_config_dir))
    
    # Define paths
    shared_path = os.path.join(temp_config_dir, "config.yaml")
    
    # 1. Create a global config file
    global_data = {
        "config": {
            "ai": {
                "engineer_api_key": "global-initial-key",
                "engineer_model": "global-model"
            }
        },
        "connections": {},
        "profiles": {}
    }
    with open(shared_path, "w") as f:
        yaml.safe_dump(global_data, f)
        
    # Re-init registry to pick up the newly created shared config file
    registry = UserRegistry(str(temp_config_dir))
    
    # Register user
    username = "testuser"
    registry.user_service.create_user(username, "testpassword")
    
    # Check initial injection
    provider = registry.get_provider(username)
    ai_settings = provider.config.get_effective_setting("ai")
    assert ai_settings.get("engineer_api_key") == "global-initial-key"
    assert ai_settings.get("engineer_model") == "global-model"
    
    # 2. Modify global config on disk
    global_data["config"]["ai"]["engineer_api_key"] = "global-updated-key"
    
    # Sleep briefly to ensure mtime change is detectable
    time.sleep(0.1)
    
    with open(shared_path, "w") as f:
        yaml.safe_dump(global_data, f)
        
    # Set the mtime forward explicitly to avoid filesystem resolution limits
    new_mtime = os.path.getmtime(shared_path) + 10.0
    os.utime(shared_path, (new_mtime, new_mtime))
    
    # Retrieve provider again - should trigger hot-reload of shared config
    provider2 = registry.get_provider(username)
    
    ai_settings_updated = provider2.config.get_effective_setting("ai")
    assert ai_settings_updated.get("engineer_api_key") == "global-updated-key"
    assert ai_settings_updated.get("engineer_model") == "global-model"


def test_shared_ai_credential_isolation(temp_config_dir):
    """Test that setting user engineer/architect credentials discards corresponding shared credentials."""
    shared_dir = os.path.join(temp_config_dir, "shared_isolation")
    user_dir = os.path.join(temp_config_dir, "user_isolation")
    os.makedirs(shared_dir, exist_ok=True)
    os.makedirs(user_dir, exist_ok=True)
    
    shared_path = os.path.join(shared_dir, "config.yaml")
    user_path = os.path.join(user_dir, "config.yaml")
    
    # Shared has both api_key and auth
    shared_data = {
        "config": {
            "ai": {
                "engineer_api_key": "global-initial-key",
                "engineer_auth": {"vertex_project": "shared-project", "api_key": "shared-auth-key"},
                "architect_api_key": "global-arch-key",
                "architect_auth": {"project": "arch-project"}
            }
        },
        "connections": {},
        "profiles": {}
    }
    with open(shared_path, "w") as f:
        yaml.safe_dump(shared_data, f)
        
    # User configures ONLY engineer_api_key (expects engineer_auth to be discarded)
    # and ONLY architect_auth (expects architect_api_key to be discarded)
    user_data = {
        "config": {
            "ai": {
                "engineer_api_key": "user-custom-key",
                "architect_auth": {"project": "user-project", "api_key": "user-auth-key"}
            }
        },
        "connections": {},
        "profiles": {}
    }
    with open(user_path, "w") as f:
        yaml.safe_dump(user_data, f)

    shared_config = configfile(conf=shared_path)
    user_config = configfile(conf=user_path, shared_config=shared_config)
    
    effective_ai = user_config.get_effective_setting("ai")
    
    # 1. Engineer: local api_key is present, so shared engineer_auth must be completely discarded
    assert effective_ai.get("engineer_api_key") == "user-custom-key"
    assert "engineer_auth" not in effective_ai
    
    # 2. Architect: local auth is present, so shared architect_api_key must be completely discarded
    assert effective_ai.get("architect_auth") == {"project": "user-project", "api_key": "user-auth-key"}
    assert "architect_api_key" not in effective_ai
