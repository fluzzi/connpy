"""Tests for connpy.services.sync_service"""
import datetime
import os
import sys
import warnings
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from connpy.services import sync_service
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

    def test_google_api_core_python_version_future_warning_is_silenced_and_idempotent(
        self,
    ):
        with warnings.catch_warnings(record=True) as recorded:
            warnings.resetwarnings()
            warnings.simplefilter("always")
            sync_service._silence_google_python_version_warning()
            sync_service._silence_google_python_version_warning()

            count = sum(
                1
                for f in warnings.filters
                if f[0] == "ignore"
                and f[2] is FutureWarning
                and getattr(f[1], "pattern", None)
                == sync_service._GOOGLE_PY_VERSION_WARNING_MESSAGE
                and getattr(f[3], "pattern", None)
                == sync_service._GOOGLE_PY_VERSION_WARNING_MODULE
            )
            assert count == 1

            warnings.warn_explicit(
                "You are using a Python version (3.11.2) which Google will stop supporting...",
                FutureWarning,
                filename="fake_file.py",
                lineno=1,
                module="google.api_core._python_version_support",
                registry={},
            )
            warnings.warn_explicit(
                "You are using a Python version (3.11.2)\nwhich Google will stop supporting...",
                FutureWarning,
                filename="fake_file.py",
                lineno=1,
                module="google.api_core._python_version_support",
                registry={},
            )
            assert len(recorded) == 0

            # 4 negative controls with registry={}
            warnings.warn_explicit(
                "Other deprecation",
                FutureWarning,
                filename="fake_file.py",
                lineno=1,
                module="google.api_core._python_version_support",
                registry={},
            )
            warnings.warn_explicit(
                "You are using a Python version (3.11.2) which Google will stop supporting...",
                FutureWarning,
                filename="fake_file.py",
                lineno=1,
                module="google.api_core.other",
                registry={},
            )
            warnings.warn_explicit(
                "You are using a Python version (3.11.2) which Google will stop supporting...",
                FutureWarning,
                filename="fake_file.py",
                lineno=1,
                module="other.module",
                registry={},
            )
            warnings.warn_explicit(
                "You are using a Python version (3.11.2) which Google will stop supporting...",
                DeprecationWarning,
                filename="fake_file.py",
                lineno=1,
                module="google.api_core._python_version_support",
                registry={},
            )
            assert len(recorded) == 4

    def test_real_google_api_core_check_python_version_is_silenced(self):
        pv_mod = pytest.importorskip("google.api_core._python_version_support")
        version_info_cls = namedtuple("version_info", ["major", "minor", "micro"])
        fake_sys = SimpleNamespace(
            version_info=version_info_cls(3, 11, 2),
            version="3.11.2",
        )
        with patch.object(pv_mod, "sys", fake_sys):
            with warnings.catch_warnings(record=True) as raw_recorded:
                warnings.resetwarnings()
                warnings.simplefilter("always")
                try:
                    pv_mod.check_python_version(today=datetime.date(2026, 10, 28))
                except (AttributeError, TypeError) as exc:
                    pytest.skip(f"Incompatible google-api-core internals: {exc}")
                if not any(issubclass(w.category, FutureWarning) for w in raw_recorded):
                    pytest.skip(
                        "Version table in installed google-api-core did not emit warning for 3.11.2 on 2026-10-28"
                    )

            with warnings.catch_warnings(record=True) as filtered_recorded:
                warnings.resetwarnings()
                warnings.simplefilter("always")
                sync_service._silence_google_python_version_warning()
                try:
                    pv_mod.check_python_version(today=datetime.date(2026, 10, 28))
                except (AttributeError, TypeError) as exc:
                    pytest.skip(f"Incompatible google-api-core internals: {exc}")
                assert len(filtered_recorded) == 0

    @pytest.mark.parametrize(
        "attr_name,module_name,target_attr",
        [(k, v[0], v[1]) for k, v in sync_service._GOOGLE_LAZY_IMPORTS.items()],
    )
    def test_getattr_lazy_symbols_reapply_filter_cache_and_unknown_raises(
        self, attr_name, module_name, target_attr
    ):
        had_attr = attr_name in sync_service.__dict__
        saved_val = sync_service.__dict__.get(attr_name)
        if had_attr:
            delattr(sync_service, attr_name)

        try:
            sentinel_obj = object()
            fake_mod = MagicMock(**{target_attr: sentinel_obj})
            with patch.dict(sys.modules, {module_name: fake_mod}):
                with warnings.catch_warnings(record=True) as recorded:
                    warnings.resetwarnings()
                    warnings.simplefilter("always")

                    resolved = getattr(sync_service, attr_name)
                    assert resolved is sentinel_obj
                    assert sync_service.__dict__[attr_name] is sentinel_obj

                    warnings.warn_explicit(
                        "You are using a Python version (3.11.2) which Google will stop supporting...",
                        FutureWarning,
                        filename="fake_file.py",
                        lineno=1,
                        module="google.api_core._python_version_support",
                        registry={},
                    )
                    assert len(recorded) == 0

                    warnings.warn_explicit(
                        "Unrelated future warning",
                        FutureWarning,
                        filename="other.py",
                        lineno=1,
                        module="other.module",
                        registry={},
                    )
                    assert len(recorded) == 1
        finally:
            if had_attr:
                setattr(sync_service, attr_name, saved_val)
            elif attr_name in sync_service.__dict__:
                delattr(sync_service, attr_name)

        with patch(
            "connpy.services.sync_service._silence_google_python_version_warning"
        ) as mock_silence:
            with pytest.raises(
                AttributeError,
                match="module 'connpy.services.sync_service' has no attribute 'non_existent_attr'",
            ):
                sync_service.__getattr__("non_existent_attr")
            mock_silence.assert_not_called()
            assert "non_existent_attr" not in sync_service.__dict__
