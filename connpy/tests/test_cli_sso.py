import pytest
from unittest.mock import MagicMock, patch
from connpy.cli.sso_handler import SSOHandler

def test_sso_handler_add_provider_with_allowed_domains():
    # 1. Setup mock app structure
    app_mock = MagicMock()
    app_mock.services.mode = "local"
    app_mock.config.config = {"sso": {"providers": {}}}
    
    handler = SSOHandler(app_mock)
    
    # Mock inquirer prompts
    mock_answers = {
        "jwks_url": "https://accounts.google.com/.well-known/jwks.json",
        "secret": "my-secret-key",
        "username_claim": "email",
        "algorithms": "RS256, HS256",
        "allowed_domains": "yyy.com, company.org"
    }
    
    args_mock = MagicMock()
    args_mock.provider = "google"
    
    with patch("inquirer.prompt", return_value=mock_answers):
        handler.add_provider(args_mock)
        
    # Verify update_setting was called with the correct data structure
    app_mock.services.config_svc.update_setting.assert_called_once()
    saved_key, saved_sso_config = app_mock.services.config_svc.update_setting.call_args[0]
    
    assert saved_key == "sso"
    assert "providers" in saved_sso_config
    assert "google" in saved_sso_config["providers"]
    
    google_config = saved_sso_config["providers"]["google"]
    assert google_config["jwks_url"] == "https://accounts.google.com/.well-known/jwks.json"
    assert google_config["secret"] == "my-secret-key"
    assert google_config["username_claim"] == "email"
    assert google_config["algorithms"] == ["RS256", "HS256"]
    assert google_config["allowed_domains"] == ["yyy.com", "company.org"]

def test_sso_handler_add_provider_allowed_domains_empty():
    app_mock = MagicMock()
    app_mock.services.mode = "local"
    app_mock.config.config = {"sso": {"providers": {}}}
    
    handler = SSOHandler(app_mock)
    
    mock_answers = {
        "jwks_url": "https://accounts.google.com/.well-known/jwks.json",
        "secret": "",
        "username_claim": "sub",
        "algorithms": "RS256",
        "allowed_domains": "   " # empty input
    }
    
    args_mock = MagicMock()
    args_mock.provider = "google"
    
    with patch("inquirer.prompt", return_value=mock_answers):
        handler.add_provider(args_mock)
        
    saved_key, saved_sso_config = app_mock.services.config_svc.update_setting.call_args[0]
    google_config = saved_sso_config["providers"]["google"]
    
    assert "allowed_domains" not in google_config
