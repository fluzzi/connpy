"""Tests for connpy.core_plugins.sync"""
import pytest
from unittest.mock import MagicMock, patch, mock_open
from connpy.core_plugins.sync import sync

@pytest.fixture
def mock_connapp():
    app = MagicMock()
    app.config.defaultdir = "/fake/dir"
    app.config.file = "/fake/dir/config.yaml"
    app.config.key = "/fake/dir/.osk"
    app.config.config = {"sync": True}
    return app

class TestSyncPlugin:
    def test_init(self, mock_connapp):
        s = sync(mock_connapp)
        assert s.sync is True
        assert s.file == "/fake/dir/config.yaml"
        assert s.token_file == "/fake/dir/gtoken.json"

    @patch("connpy.core_plugins.sync.os.path.exists")
    @patch("connpy.core_plugins.sync.Credentials")
    def test_get_credentials_success(self, MockCreds, mock_exists, mock_connapp):
        mock_exists.return_value = True
        mock_cred_instance = MagicMock()
        mock_cred_instance.valid = True
        MockCreds.from_authorized_user_file.return_value = mock_cred_instance
        
        s = sync(mock_connapp)
        creds = s.get_credentials()
        assert creds == mock_cred_instance

    @patch("connpy.core_plugins.sync.os.path.exists")
    def test_get_credentials_not_found(self, mock_exists, mock_connapp):
        mock_exists.return_value = False
        s = sync(mock_connapp)
        assert s.get_credentials() == 0

    @patch("connpy.core_plugins.sync.zipfile.ZipFile")
    @patch("connpy.core_plugins.sync.os.path.basename")
    def test_compress_specific_files(self, mock_basename, MockZipFile, mock_connapp):
        mock_basename.return_value = "config.yaml"
        s = sync(mock_connapp)
        zip_mock = MagicMock()
        MockZipFile.return_value.__enter__.return_value = zip_mock
        
        s.compress_specific_files("/fake/zip.zip")
        zip_mock.write.assert_any_call(s.file, "config.yaml")
        zip_mock.write.assert_any_call(s.key, ".osk")

    @patch("connpy.core_plugins.sync.zipfile.ZipFile")
    @patch("connpy.core_plugins.sync.os.path.dirname")
    def test_decompress_zip_yaml(self, mock_dirname, MockZipFile, mock_connapp):
        mock_dirname.return_value = "/fake/dir"
        s = sync(mock_connapp)
        zip_mock = MagicMock()
        zip_mock.namelist.return_value = ["config.yaml", ".osk"]
        MockZipFile.return_value.__enter__.return_value = zip_mock
        
        assert s.decompress_zip("/fake/zip.zip") == 0
        zip_mock.extract.assert_any_call("config.yaml", "/fake/dir")
        zip_mock.extract.assert_any_call(".osk", "/fake/dir")

    @patch("connpy.core_plugins.sync.zipfile.ZipFile")
    @patch("connpy.core_plugins.sync.os.path.dirname")
    def test_decompress_zip_json_fallback(self, mock_dirname, MockZipFile, mock_connapp):
        mock_dirname.return_value = "/fake/dir"
        s = sync(mock_connapp)
        zip_mock = MagicMock()
        zip_mock.namelist.return_value = ["config.json", ".osk"]
        MockZipFile.return_value.__enter__.return_value = zip_mock
        
        assert s.decompress_zip("/fake/old_zip.zip") == 0
        zip_mock.extract.assert_any_call("config.json", "/fake/dir")

    @patch.object(sync, "get_credentials")
    @patch("connpy.core_plugins.sync.build")
    def test_get_appdata_files(self, mock_build, mock_get_credentials, mock_connapp):
        mock_get_credentials.return_value = MagicMock()
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        mock_service.files().list().execute.return_value = {
            "files": [
                {"id": "1", "name": "backup1.zip", "appProperties": {"timestamp": "1000", "date": "2024"}}
            ]
        }
        
        s = sync(mock_connapp)
        files = s.get_appdata_files()
        assert len(files) == 1
        assert files[0]["id"] == "1"
        assert files[0]["timestamp"] == "1000"

    @patch.object(sync, "get_credentials")
    @patch("connpy.core_plugins.sync.build")
    @patch("connpy.core_plugins.sync.MediaFileUpload")
    @patch("connpy.core_plugins.sync.os.path.basename")
    def test_backup_file_to_drive(self, mock_basename, mock_media, mock_build, mock_get_credentials, mock_connapp):
        mock_get_credentials.return_value = MagicMock()
        mock_basename.return_value = "backup.zip"
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        s = sync(mock_connapp)
        assert s.backup_file_to_drive("/fake/backup.zip", 1234567890000) == 0
        mock_service.files().create.assert_called_once()
