import os
import shutil
import pytest
import datetime
import jwt
import yaml
from pathlib import Path
from connpy.services.user_service import UserService

@pytest.fixture
def test_config_dir(tmp_path):
    """Creates a temporary config directory for testing user registry."""
    config_dir = tmp_path / "conn_config"
    config_dir.mkdir()
    return config_dir

@pytest.fixture
def user_service(test_config_dir):
    """Initializes UserService pointing to a temporary directory."""
    return UserService(str(test_config_dir))


class TestUserService:
    def test_no_users(self, user_service):
        """Verifies that a new registry is empty by default."""
        users = user_service.list_users()
        assert users == []

    def test_create_user_default(self, user_service):
        """Tests Mode A: fresh user config and key creation."""
        username = "testuser"
        res = user_service.create_user(username, "mypassword")
        
        assert res["username"] == username
        assert res["config_path"] is None
        assert "created" in res
        
        # Verify folder, config.yaml and .osk key are created
        user_dir = os.path.join(user_service.users_dir, username)
        assert os.path.isdir(user_dir)
        assert os.path.isdir(os.path.join(user_dir, "plugins"))
        assert os.path.isdir(os.path.join(user_dir, "ai_sessions"))
        assert os.path.isfile(os.path.join(user_dir, "config.yaml"))
        assert os.path.isfile(os.path.join(user_dir, ".osk"))

    def test_create_user_custom_path(self, user_service, tmp_path):
        """Tests Mode B: using an existing valid config path."""
        # Setup existing custom config directory
        custom_dir = tmp_path / "custom_user_conn"
        custom_dir.mkdir()
        
        config_file = custom_dir / "config.yaml"
        # Write basic config.yaml
        config_data = {
            "config": {"case": False, "idletime": 30, "fzf": False},
            "connections": {},
            "profiles": {}
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)
            
        res = user_service.create_user("fluzzi", "fluzzipass", config_path=str(custom_dir))
        
        assert res["username"] == "fluzzi"
        assert res["config_path"] == str(custom_dir)
        
        # Verify no directory is created under the server's user folder
        user_dir = os.path.join(user_service.users_dir, "fluzzi")
        assert not os.path.exists(user_dir)

    def test_create_user_custom_path_auto_init(self, user_service, tmp_path):
        """Ensures create_user automatically initializes a missing directory and default config.yaml."""
        custom_dir = tmp_path / "new_custom_config"
        
        # Test creation where the directory does not exist yet
        res = user_service.create_user("john", "pass", config_path=str(custom_dir))
        assert res["username"] == "john"
        assert res["config_path"] == str(custom_dir)
        
        # Verify custom path and subdirs/configs were created
        assert os.path.isdir(custom_dir)
        assert os.path.exists(os.path.join(custom_dir, "config.yaml"))
        assert os.path.isdir(os.path.join(custom_dir, "plugins"))
        assert os.path.isdir(os.path.join(custom_dir, "ai_sessions"))

    def test_create_duplicate_user(self, user_service):
        """Ensures duplicate usernames are rejected."""
        user_service.create_user("dupuser", "password")
        with pytest.raises(ValueError, match="already exists"):
            user_service.create_user("dupuser", "anotherpass")

    def test_delete_user_default(self, user_service):
        """Tests Mode A: deleting a server-managed user cleans up directories."""
        username = "deluser"
        user_service.create_user(username, "password")
        user_dir = os.path.join(user_service.users_dir, username)
        assert os.path.isdir(user_dir)
        
        user_service.delete_user(username)
        # Directory should be cleaned up
        assert not os.path.exists(user_dir)
        # Registry should be updated
        assert len(user_service.list_users()) == 0

    def test_delete_user_custom_path(self, user_service, tmp_path):
        """Tests Mode B: deleting a custom-path user leaves files untouched."""
        custom_dir = tmp_path / "fluzzi_custom"
        custom_dir.mkdir()
        config_file = custom_dir / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump({"config": {}, "connections": {}, "profiles": {}}, f)
            
        username = "fluzzi"
        user_service.create_user(username, "pass", config_path=str(custom_dir))
        
        user_service.delete_user(username)
        # Registry cleared
        assert len(user_service.list_users()) == 0
        # Files remain untouched
        assert os.path.isdir(str(custom_dir))
        assert os.path.isfile(str(config_file))

    def test_list_users(self, user_service):
        """Tests listing all registered users with their metadata."""
        user_service.create_user("user1", "pass1")
        user_service.create_user("user2", "pass2")
        
        users = user_service.list_users()
        assert len(users) == 2
        usernames = [u["username"] for u in users]
        assert "user1" in usernames
        assert "user2" in usernames

    def test_get_user(self, user_service):
        """Tests retrieving a single user's configuration metadata."""
        user_service.create_user("user1", "pass1")
        user = user_service.get_user("user1")
        
        assert user["username"] == "user1"
        assert user["config_path"] is None
        assert "created" in user
        
        with pytest.raises(ValueError, match="not found"):
            user_service.get_user("nonexistent")

    def test_authenticate_valid(self, user_service):
        """Verifies successful authentication."""
        user_service.create_user("john", "my-secure-password")
        assert user_service.authenticate("john", "my-secure-password") is True

    def test_authenticate_invalid(self, user_service):
        """Verifies unsuccessful authentication on incorrect or missing credentials."""
        user_service.create_user("john", "my-secure-password")
        
        assert user_service.authenticate("john", "wrong-password") is False
        assert user_service.authenticate("nonexistent", "my-secure-password") is False

    def test_jwt_roundtrip(self, user_service):
        """Tests generating a JWT token and verifying it back to the username."""
        username = "jwttester"
        user_service.create_user(username, "pass")
        
        token = user_service.generate_jwt(username)
        assert isinstance(token, str)
        
        verified_user = user_service.verify_jwt(token)
        assert verified_user == username

    def test_jwt_expired(self, user_service):
        """Tests that expired JWT tokens are rejected and return None."""
        username = "jwttester"
        user_service.create_user(username, "pass")
        
        # Manually generate an expired token by setting exp to the past
        registry = user_service._load_registry()
        expired_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)
        payload = {
            "sub": username,
            "exp": expired_time
        }
        token = jwt.encode(payload, registry["jwt_secret"], algorithm="HS256")
        if isinstance(token, bytes):
            token = token.decode("utf-8")
            
        verified_user = user_service.verify_jwt(token)
        assert verified_user is None

    def test_change_password(self, user_service):
        """Tests changing password for a user."""
        username = "passchanger"
        user_service.create_user(username, "oldpass")
        
        # Old credentials authenticate
        assert user_service.authenticate(username, "oldpass") is True
        
        # Change password
        user_service.change_password(username, "oldpass", "newpass")
        
        # Old password fails, new password works
        assert user_service.authenticate(username, "oldpass") is False
        assert user_service.authenticate(username, "newpass") is True
        
        # Change with invalid old password should fail
        with pytest.raises(ValueError, match="Invalid credentials"):
            user_service.change_password(username, "wrongold", "evennewer")

    def test_admin_change_password(self, user_service):
        """Tests administrative password change (no old password required)."""
        username = "adminpasschanger"
        user_service.create_user(username, "oldpass")
        
        # Admin changes password directly
        user_service.admin_change_password(username, "newpass")
        
        # Verify credentials
        assert user_service.authenticate(username, "oldpass") is False
        assert user_service.authenticate(username, "newpass") is True
