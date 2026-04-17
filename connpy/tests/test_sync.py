"""Tests for connpy.services.sync_service"""
import pytest
import os
from unittest.mock import MagicMock, patch
from connpy.services.sync_service import SyncService

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.defaultdir = "/fake/dir"
    config.file = "/fake/dir/config.yaml"
    config.key = "/fake/dir/.osk"
    config.cachefile = "/fake/dir/.cache"
    config.fzf_cachefile = "/fake/dir/.fzf_cache"
    config.config = {"sync": True, "sync_remote": False}
    return config

class TestSyncService:
    def test_init(self, mock_config):
        s = SyncService(mock_config)
        assert s.sync_enabled is True
        assert s.token_file == os.path.join("/fake/dir", "gtoken.json")

    @patch("connpy.services.sync_service.os.path.exists")
    @patch("connpy.services.sync_service.Credentials")
    def test_get_credentials_success(self, MockCreds, mock_exists, mock_config):
        mock_exists.return_value = True
        mock_cred_instance = MagicMock()
        mock_cred_instance.valid = True
        MockCreds.from_authorized_user_file.return_value = mock_cred_instance
        
        s = SyncService(mock_config)
        creds = s.get_credentials()
        assert creds == mock_cred_instance

    @patch("connpy.services.sync_service.os.path.exists")
    def test_get_credentials_not_found(self, mock_exists, mock_config):
        mock_exists.return_value = False
        s = SyncService(mock_config)
        assert s.get_credentials() is None

    @patch("connpy.services.sync_service.zipfile.ZipFile")
    @patch("connpy.services.sync_service.os.path.exists")
    @patch("connpy.services.sync_service.os.path.basename")
    def test_compress_and_upload_local(self, mock_basename, mock_exists, MockZipFile, mock_config):
        mock_basename.return_value = "config.yaml"
        mock_exists.return_value = True
        s = SyncService(mock_config)
        
        # Mocking list_backups and upload_file to avoid real API calls
        s.list_backups = MagicMock(return_value=[])
        s.upload_file = MagicMock(return_value=True)
        
        zip_mock = MagicMock()
        MockZipFile.return_value.__enter__.return_value = zip_mock
        
        s.compress_and_upload()
        # Verify zip was created with local config and key
        zip_mock.write.assert_any_call(s.config.file, "config.yaml")
        zip_mock.write.assert_any_call(s.config.key, ".osk")

    @patch("connpy.services.sync_service.zipfile.ZipFile")
    @patch("connpy.services.sync_service.os.path.exists")
    @patch("connpy.services.sync_service.os.path.dirname")
    @patch("connpy.services.sync_service.os.remove")
    def test_perform_restore(self, mock_remove, mock_dirname, mock_exists, MockZipFile, mock_config):
        mock_dirname.return_value = "/fake/dir"
        # Mock exists to return True for key and zip, but False for caches during the cleanup phase
        def exists_side_effect(path):
            if ".cache" in path or ".fzf_cache" in path:
                return False
            return True
        mock_exists.side_effect = exists_side_effect
        
        s = SyncService(mock_config)
        zip_mock = MagicMock()
        zip_mock.namelist.return_value = ["config.yaml", ".osk"]
        MockZipFile.return_value.__enter__.return_value = zip_mock
        
        with patch("connpy.services.sync_service.yaml.safe_load") as mock_load:
            mock_load.return_value = {"connections": {}, "profiles": {}, "config": {}}
            assert s.perform_restore("/fake/zip.zip") is True
            
        zip_mock.extract.assert_any_call(".osk", "/fake/dir")

    @patch.object(SyncService, "get_credentials")
    @patch("connpy.services.sync_service.build")
    def test_list_backups(self, mock_build, mock_get_credentials, mock_config):
        mock_get_credentials.return_value = MagicMock()
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        mock_service.files().list().execute.return_value = {
            "files": [
                {"id": "1", "name": "backup1.zip", "appProperties": {"timestamp": "1000", "date": "2024"}}
            ]
        }
        
        s = SyncService(mock_config)
        files = s.list_backups()
        assert len(files) == 1
        assert files[0]["id"] == "1"
        assert files[0]["timestamp"] == "1000"
