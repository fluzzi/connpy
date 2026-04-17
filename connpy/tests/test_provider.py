import pytest
from unittest.mock import patch, MagicMock
from connpy.services.provider import ServiceProvider

def test_service_provider_local_mode():
    config_mock = MagicMock()
    with patch("connpy.services.provider.NodeService", create=True) as MockNodeService, \
         patch("connpy.services.provider.ProfileService", create=True), \
         patch("connpy.services.provider.ConfigService", create=True), \
         patch("connpy.services.provider.PluginService", create=True), \
         patch("connpy.services.provider.AIService", create=True), \
         patch("connpy.services.provider.SystemService", create=True), \
         patch("connpy.services.provider.ExecutionService", create=True), \
         patch("connpy.services.provider.ImportExportService", create=True):
        
        provider = ServiceProvider(config_mock, mode="local")
        
        assert provider.mode == "local"
        assert provider.config == config_mock
        # Verify that an attribute was created
        assert provider.nodes is not None

def test_service_provider_remote_mode():
    config_mock = MagicMock()
    with patch("connpy.services.provider.ConfigService", create=True) as MockConfigService, \
         patch("grpc.insecure_channel", create=True) as MockChannel:
        
        provider = ServiceProvider(config_mock, mode="remote", remote_host="localhost:50051")
        
        # Verify ConfigService is initialized locally
        assert provider.config_svc is not None
        
        # Verify grpc channel was created
        MockChannel.assert_called_once_with("localhost:50051")
        
        # Verify a stub was assigned
        assert provider.nodes is not None

def test_service_provider_unknown_mode():
    config_mock = MagicMock()
    with pytest.raises(ValueError, match="Unknown service mode: invalid_mode"):
        ServiceProvider(config_mock, mode="invalid_mode")