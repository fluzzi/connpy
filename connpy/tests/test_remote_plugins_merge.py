import os
import shutil
import pytest
from connpy.configfile import configfile
from connpy.services.plugin_service import PluginService
from connpy.services.exceptions import InvalidConfigurationError

@pytest.fixture
def temp_plugins_env(tmp_path):
    """Creates a temporary isolated environment for core, shared, and user plugins."""
    base_dir = tmp_path / "plugins_test_env"
    base_dir.mkdir()
    
    # Paths for shared config and user config folders
    shared_dir = base_dir / "shared"
    user_dir = base_dir / "user"
    
    shared_dir.mkdir()
    user_dir.mkdir()
    
    # Create plugins subdirectories
    (shared_dir / "plugins").mkdir()
    (user_dir / "plugins").mkdir()
    
    # Mock core_plugins path by creating a sibling folder
    core_dir = base_dir / "core_plugins"
    core_dir.mkdir()
    
    # Config file paths
    shared_path = os.path.join(shared_dir, "config.yaml")
    user_path = os.path.join(user_dir, "config.yaml")
    
    # Write empty config templates
    import yaml
    empty_conf = {"config": {}, "connections": {}, "profiles": {}}
    with open(shared_path, "w") as f:
        yaml.safe_dump(empty_conf, f)
    with open(user_path, "w") as f:
        yaml.safe_dump(empty_conf, f)
        
    return {
        "shared_dir": shared_dir,
        "user_dir": user_dir,
        "core_dir": core_dir,
        "shared_path": shared_path,
        "user_path": user_path
    }

def test_plugin_resolution_priority_merge(temp_plugins_env, monkeypatch):
    """Test that list_plugins correctly merges core, shared, and user plugins with overrides."""
    env = temp_plugins_env
    
    # 1. Create a core plugin: 'coreplug'
    core_file = env["core_dir"] / "coreplug.py"
    with open(core_file, "w") as f:
        f.write("# core plugin content")
        
    # 2. Create a shared plugin: 'sharedplug'
    shared_file = env["shared_dir"] / "plugins" / "sharedplug.py"
    with open(shared_file, "w") as f:
        f.write("# shared plugin content")
        
    # 3. Create a user plugin: 'userplug'
    user_file = env["user_dir"] / "plugins" / "userplug.py"
    with open(user_file, "w") as f:
        f.write("# user plugin content")
        
    # 4. Create an override plugin: 'overrideplug' in all three directories
    with open(env["core_dir"] / "overrideplug.py", "w") as f:
        f.write("# core override version")
    with open(env["shared_dir"] / "plugins" / "overrideplug.py", "w") as f:
        f.write("# shared override version")
    with open(env["user_dir"] / "plugins" / "overrideplug.py", "w") as f:
        f.write("# user override version")

    # Initialize configs
    shared_cfg = configfile(conf=env["shared_path"])
    user_cfg = configfile(conf=env["user_path"], shared_config=shared_cfg)
    
    # Initialize service
    plugin_svc = PluginService(user_cfg)
    
    # Monkeypatch the core plugins folder path inside list_plugins
    # in order to use our mock core folder instead of the real one.
    # Note: real path is computed via __file__, so we'll mock the internal core path
    monkeypatch.setattr(
        "os.path.realpath",
        lambda path: os.path.join(str(env["core_dir"]), "dummy")
    )


    plugins_list = plugin_svc.list_plugins()
    
    # Verify all plugins are registered
    assert "coreplug" in plugins_list
    assert "sharedplug" in plugins_list
    assert "userplug" in plugins_list
    assert "overrideplug" in plugins_list
    
    # Verify status is Active (enabled=True)
    assert plugins_list["coreplug"]["enabled"] is True
    assert plugins_list["sharedplug"]["enabled"] is True
    assert plugins_list["userplug"]["enabled"] is True
    assert plugins_list["overrideplug"]["enabled"] is True
    
    # Verify hashes differ matching user overrides
    import hashlib
    user_override_hash = hashlib.md5(b"# user override version").hexdigest()
    assert plugins_list["overrideplug"]["hash"] == user_override_hash

def test_get_plugin_source_override(temp_plugins_env, monkeypatch):
    """Test that get_plugin_source resolves the highest priority plugin version."""
    env = temp_plugins_env
    
    # Create override in shared and user
    with open(env["shared_dir"] / "plugins" / "myplug.py", "w") as f:
        f.write("shared content")
    with open(env["user_dir"] / "plugins" / "myplug.py", "w") as f:
        f.write("user override")
        
    shared_cfg = configfile(conf=env["shared_path"])
    user_cfg = configfile(conf=env["user_path"], shared_config=shared_cfg)
    plugin_svc = PluginService(user_cfg)
    
    # Fetch source
    source = plugin_svc.get_plugin_source("myplug")
    assert source == "user override"

def test_delete_plugin_restrictions(temp_plugins_env):
    """Test that deleting shared plugins is rejected, but deleting user overrides works."""
    env = temp_plugins_env
    
    # Create shared plugin
    with open(env["shared_dir"] / "plugins" / "globalplug.py", "w") as f:
        f.write("global content")
        
    # Create user plugin override
    with open(env["user_dir"] / "plugins" / "globalplug.py", "w") as f:
        f.write("user content")
        
    shared_cfg = configfile(conf=env["shared_path"])
    user_cfg = configfile(conf=env["user_path"], shared_config=shared_cfg)
    plugin_svc = PluginService(user_cfg)
    
    # 1. Delete plugin (should delete the user override first)
    plugin_svc.delete_plugin("globalplug")
    
    # Verify user override is gone, but shared plugin remains
    assert not os.path.exists(env["user_dir"] / "plugins" / "globalplug.py")
    assert os.path.exists(env["shared_dir"] / "plugins" / "globalplug.py")
    
    # 2. Try to delete again (now only exists in shared/global folder)
    with pytest.raises(InvalidConfigurationError) as exc:
        plugin_svc.delete_plugin("globalplug")
    assert "Global and core plugins are read-only" in str(exc.value)
    
    # Verify shared plugin is still present
    assert os.path.exists(env["shared_dir"] / "plugins" / "globalplug.py")

def test_shadow_disable_and_enable_mechanisms(temp_plugins_env):
    """Test that disabling a shared plugin creates a shadow backup file and enabling it removes it."""
    env = temp_plugins_env
    
    # Create a shared plugin
    with open(env["shared_dir"] / "plugins" / "sharedplug.py", "w") as f:
        f.write("shared content")
        
    shared_cfg = configfile(conf=env["shared_path"])
    user_cfg = configfile(conf=env["user_path"], shared_config=shared_cfg)
    plugin_svc = PluginService(user_cfg)
    
    # Ensure it's active initially
    list_initial = plugin_svc.list_plugins()
    assert list_initial["sharedplug"]["enabled"] is True
    
    # 1. Disable the shared plugin (should shadow-disable it in user dir)
    res = plugin_svc.disable_plugin("sharedplug")
    assert res is True
    
    # Verify shadow bkp file exists in user plugins and has 0 bytes
    shadow_bkp = env["user_dir"] / "plugins" / "sharedplug.py.bkp"
    assert os.path.exists(shadow_bkp)
    assert os.path.getsize(shadow_bkp) == 0
    
    # Verify list_plugins lists it as disabled
    list_disabled = plugin_svc.list_plugins()
    assert list_disabled["sharedplug"]["enabled"] is False
    
    # 2. Re-enable the shadow-disabled plugin (should delete the user shadow file)
    res_enable = plugin_svc.enable_plugin("sharedplug")
    assert res_enable is True
    
    # Verify shadow file is deleted
    assert not os.path.exists(shadow_bkp)
    
    # Verify list_plugins lists it as active again
    list_active = plugin_svc.list_plugins()
    assert list_active["sharedplug"]["enabled"] is True
