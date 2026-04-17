import pytest
from connpy.services.profile_service import ProfileService
from connpy.services.exceptions import ProfileNotFoundError, ProfileAlreadyExistsError

def test_profile_crud(populated_config):
    """Test basic CRUD operations for profiles."""
    service = ProfileService(populated_config)
    
    # List
    profiles = service.list_profiles()
    assert "default" in profiles
    assert "office-user" in profiles
    
    # Get
    office = service.get_profile("office-user")
    assert office["user"] == "officeadmin"
    
    # Add
    new_data = {
        "user": "newadmin",
        "password": "newpassword"
    }
    service.add_profile("new-profile", new_data)
    assert "new-profile" in service.list_profiles()
    assert service.get_profile("new-profile")["user"] == "newadmin"
    
    # Update
    update_data = {
        "user": "updatedadmin"
    }
    service.update_profile("new-profile", update_data)
    assert service.get_profile("new-profile")["user"] == "updatedadmin"
    
    # Delete
    service.delete_profile("new-profile")
    assert "new-profile" not in service.list_profiles()

def test_profile_inheritance_parity(populated_config):
    """
    Test that profiles can inherit from other profiles.
    Regression: ProfileService currently doesn't resolve inheritance within profiles.
    """
    service = ProfileService(populated_config)
    
    # Create a profile that inherits from 'office-user'
    # 'office-user' has user='officeadmin', password='officepass'
    inherited_data = {
        "user": "@office-user",
        "options": "-v"
    }
    service.add_profile("inherited-profile", inherited_data)
    
    # When we get the profile, we expect it to be resolved if inheritance is supported
    # This is a common pattern in connpy for nodes, but should it work for profiles?
    # The task mentions "profile CRUD and inheritance parity".
    
    profile = service.get_profile("inherited-profile")
    
    # If inheritance is resolved, user should be 'officeadmin'
    # This is expected to FAIL if ProfileService just returns the raw dict.
    assert profile["user"] == "officeadmin"
    assert profile["password"] == "officepass"
    assert profile["options"] == "-v"

def test_delete_default_profile_fails(populated_config):
    """Test that deleting the 'default' profile is prohibited."""
    service = ProfileService(populated_config)
    from connpy.services.exceptions import InvalidConfigurationError
    
    with pytest.raises(InvalidConfigurationError, match="Cannot delete the 'default' profile"):
        service.delete_profile("default")

def test_delete_used_profile_fails(populated_config):
    """Test that deleting a profile used by nodes is prohibited."""
    service = ProfileService(populated_config)
    from connpy.services.exceptions import InvalidConfigurationError
    
    # In populated_config, we need to make sure a node uses a profile
    # Let's add a node that uses 'office-user'
    populated_config._connections_add(id="testnode", host="1.1.1.1", user="@office-user")
    
    with pytest.raises(InvalidConfigurationError, match="is used by nodes"):
        service.delete_profile("office-user")
