import os
import datetime
import hashlib
import pytest
import yaml
from connpy.services.user_service import UserService


@pytest.fixture
def test_config_dir(tmp_path):
    """Creates a temporary config directory for testing."""
    config_dir = tmp_path / "conn_config"
    config_dir.mkdir()
    return config_dir


@pytest.fixture
def user_service(test_config_dir):
    """Initializes UserService pointing to a temporary directory."""
    return UserService(str(test_config_dir))


@pytest.fixture
def user_with_token(user_service):
    """Creates a user and returns (user_service, username, token_result)."""
    username = "tokenuser"
    user_service.create_user(username, "password123")
    result = user_service.create_api_token(username, "Test Token")
    return user_service, username, result


class TestApiTokenCreation:
    def test_create_api_token_returns_raw_token(self, user_service):
        """Verifies that create_api_token returns a raw token with the correct prefix."""
        user_service.create_user("alice", "pass")
        result = user_service.create_api_token("alice", "CI Pipeline")

        assert "raw_token" in result
        assert result["raw_token"].startswith("cnp_pat_")
        assert len(result["raw_token"]) > 16
        assert "token_id" in result
        assert result["token_id"].startswith("tok_")
        assert result["name"] == "CI Pipeline"

    def test_create_api_token_stores_hash_not_plaintext(self, user_service):
        """Ensures only the SHA-256 hash is persisted, never the raw token."""
        user_service.create_user("bob", "pass")
        result = user_service.create_api_token("bob", "My App")

        registry = user_service._load_registry()
        tokens = registry["users"]["bob"]["api_tokens"]
        assert len(tokens) == 1

        token_meta = list(tokens.values())[0]
        expected_hash = hashlib.sha256(result["raw_token"].encode("utf-8")).hexdigest()
        assert token_meta["token_hash"] == expected_hash
        # Raw token must NOT be stored
        assert result["raw_token"] not in str(token_meta)

    def test_create_api_token_with_expiration(self, user_service):
        """Verifies that expires_at is set correctly when expires_in_days is provided."""
        user_service.create_user("charlie", "pass")
        user_service.create_api_token("charlie", "Temp Token", expires_in_days=30)

        registry = user_service._load_registry()
        token_meta = list(registry["users"]["charlie"]["api_tokens"].values())[0]
        assert token_meta["expires_at"] is not None

        exp_dt = datetime.datetime.fromisoformat(token_meta["expires_at"])
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = exp_dt - now
        assert 29 <= delta.days <= 30

    def test_create_api_token_permanent_by_default(self, user_service):
        """Verifies that expires_at is None when no expiration is specified."""
        user_service.create_user("dave", "pass")
        user_service.create_api_token("dave", "Permanent Token")

        registry = user_service._load_registry()
        token_meta = list(registry["users"]["dave"]["api_tokens"].values())[0]
        assert token_meta["expires_at"] is None

    def test_create_api_token_nonexistent_user(self, user_service):
        """Ensures creating a token for a non-existent user raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            user_service.create_api_token("ghost", "Token")

    def test_create_api_token_empty_name(self, user_service):
        """Ensures empty token names are rejected."""
        user_service.create_user("eve", "pass")
        with pytest.raises(ValueError, match="cannot be empty"):
            user_service.create_api_token("eve", "")

    def test_create_multiple_tokens(self, user_service):
        """Verifies a user can have multiple tokens."""
        user_service.create_user("frank", "pass")
        t1 = user_service.create_api_token("frank", "Token 1")
        t2 = user_service.create_api_token("frank", "Token 2")

        assert t1["token_id"] != t2["token_id"]
        assert t1["raw_token"] != t2["raw_token"]

        tokens = user_service.list_api_tokens("frank")
        assert len(tokens) == 2


class TestApiTokenVerification:
    def test_verify_valid_token(self, user_with_token):
        """Verifies that a valid raw token authenticates correctly."""
        svc, username, result = user_with_token
        verified = svc.verify_api_token(result["raw_token"])
        assert verified == username

    def test_verify_invalid_token(self, user_service):
        """Verifies that a random/invalid token returns None."""
        user_service.create_user("alice", "pass")
        assert user_service.verify_api_token("cnp_pat_invalid_token_here") is None

    def test_verify_expired_token(self, user_service):
        """Verifies that an expired token returns None."""
        user_service.create_user("alice", "pass")
        result = user_service.create_api_token("alice", "Expiring", expires_in_days=1)

        # Manually set expires_at to the past
        registry = user_service._load_registry()
        token_meta = list(registry["users"]["alice"]["api_tokens"].values())[0]
        token_meta["expires_at"] = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        ).isoformat()
        user_service._save_registry(registry)
        # Invalidate cache so verify_api_token re-reads
        user_service._token_index = {}

        assert user_service.verify_api_token(result["raw_token"]) is None

    def test_verify_updates_last_used_at(self, user_with_token):
        """Verifies that last_used_at is updated upon successful verification."""
        svc, username, result = user_with_token

        # Initially last_used_at should be None
        registry = svc._load_registry()
        token_meta = list(registry["users"][username]["api_tokens"].values())[0]
        assert token_meta["last_used_at"] is None

        # Verify the token
        svc.verify_api_token(result["raw_token"])

        # Now last_used_at should be set
        registry = svc._load_registry()
        token_meta = list(registry["users"][username]["api_tokens"].values())[0]
        assert token_meta["last_used_at"] is not None


class TestApiTokenListing:
    def test_list_tokens_returns_metadata(self, user_with_token):
        """Verifies list returns metadata without sensitive data."""
        svc, username, result = user_with_token
        tokens = svc.list_api_tokens(username)

        assert len(tokens) == 1
        t = tokens[0]
        assert t["token_id"] == result["token_id"]
        assert t["name"] == "Test Token"
        assert t["token_prefix"].startswith("cnp_pat_")
        assert "created_at" in t
        # Must NOT expose token_hash or raw_token
        assert "token_hash" not in t
        assert "raw_token" not in t

    def test_list_tokens_empty(self, user_service):
        """Verifies listing tokens for a user with none returns empty list."""
        user_service.create_user("alice", "pass")
        assert user_service.list_api_tokens("alice") == []

    def test_list_tokens_nonexistent_user(self, user_service):
        """Ensures listing tokens for a non-existent user raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            user_service.list_api_tokens("ghost")


class TestApiTokenRevocation:
    def test_revoke_token(self, user_with_token):
        """Verifies that a revoked token is immediately invalid."""
        svc, username, result = user_with_token

        # Token works before revocation
        assert svc.verify_api_token(result["raw_token"]) == username

        # Revoke
        removed = svc.revoke_api_token(username, result["token_id"])
        assert removed is True

        # Token must fail after revocation
        assert svc.verify_api_token(result["raw_token"]) is None

        # List should be empty
        assert svc.list_api_tokens(username) == []

    def test_revoke_nonexistent_token(self, user_service):
        """Verifies revoking a non-existent token returns False."""
        user_service.create_user("alice", "pass")
        assert user_service.revoke_api_token("alice", "tok_nonexistent") is False

    def test_revoke_nonexistent_user(self, user_service):
        """Ensures revoking a token for a non-existent user raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            user_service.revoke_api_token("ghost", "tok_abc")


class TestJwtUnchanged:
    def test_jwt_still_works(self, user_service):
        """Confirms that existing JWT session tokens still authenticate correctly."""
        user_service.create_user("jwtuser", "pass")
        token = user_service.generate_jwt("jwtuser")
        verified = user_service.verify_jwt(token)
        assert verified == "jwtuser"
