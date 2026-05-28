import os
import pytest
import grpc
import argparse
from unittest.mock import MagicMock, patch
from connpy.connapp import connapp
from connpy.services.provider import ServiceProvider
from connpy.cli.user_handler import UserHandler
from connpy.cli.login_handler import LoginHandler
from connpy.grpc_layer.stubs import AuthClientInterceptor, AuthStub

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.config = {"service_mode": "local", "remote_host": "localhost:8048"}
    config.defaultdir = "/mock/default/dir"
    return config

@pytest.fixture
def app_instance(mock_config):
    with patch("connpy.services.provider.ServiceProvider") as mock_provider_cls:
        mock_provider = MagicMock()
        mock_provider.context = MagicMock()
        mock_provider.nodes = MagicMock()
        mock_provider.profiles = MagicMock()
        mock_provider.config_svc = MagicMock()
        mock_provider.plugins = MagicMock()
        mock_provider.sync = MagicMock()
        mock_provider.mode = "local"
        mock_provider.remote_host = "localhost:8048"
        mock_provider_cls.return_value = mock_provider
        
        app = connapp(mock_config)
        # Mock UserService on app services
        app.services.users = MagicMock()
        return app

class TestCLIMultiUserParsing:
    def test_parser_contains_user_login_logout(self, app_instance):
        parser, _ = app_instance.get_parser()
        
        # Verify subcommands exist by finding the _SubParsersAction
        subparsers_action = None
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparsers_action = action
                break
        
        assert subparsers_action is not None
        subcommands = subparsers_action.choices.keys()
        assert "user" in subcommands
        assert "login" in subcommands
        assert "logout" in subcommands

    def test_user_parser_arguments(self, app_instance):
        parser, _ = app_instance.get_parser()
        
        # Parse add user
        args = parser.parse_args(["user", "--add", "newguy"])
        assert args.add == ["newguy"]
        assert args.func == app_instance._user.dispatch

        # Parse delete user
        args = parser.parse_args(["user", "--del", "oldguy"])
        assert args.delete == ["oldguy"]

        # Parse list users
        args = parser.parse_args(["user", "--list"])
        assert args.list is True

        # Parse show user
        args = parser.parse_args(["user", "--show", "someguy"])
        assert args.show == ["someguy"]

        # Parse regen-password
        args = parser.parse_args(["user", "--regen-password", "someguy"])
        assert args.regen_password == ["someguy"]

        # Parse path
        args = parser.parse_args(["user", "--add", "newguy", "--path", "/some/path"])
        assert args.add == ["newguy"]
        assert args.path == ["/some/path"]

    def test_login_logout_parser_arguments(self, app_instance):
        parser, _ = app_instance.get_parser()
        
        args = parser.parse_args(["login", "someuser"])
        assert args.username == "someuser"
        assert args.status is False
        assert args.func == app_instance._login.dispatch
        
        args = parser.parse_args(["login", "--status"])
        assert args.status is True
        
        args = parser.parse_args(["login", "-s"])
        assert args.status is True
        
        args = parser.parse_args(["logout"])
        assert args.func == app_instance._login.dispatch


class TestUserHandlerDispatch:
    def test_user_handler_fails_in_remote_mode(self, app_instance):
        app_instance.services.mode = "remote"
        handler = UserHandler(app_instance)
        
        args = MagicMock()
        args.add = ["testuser"]
        
        with pytest.raises(SystemExit) as excinfo:
            handler.dispatch(args)
        assert excinfo.value.code == 1

    def test_user_handler_routes_add_correctly(self, app_instance):
        app_instance.services.mode = "local"
        handler = UserHandler(app_instance)
        
        args = MagicMock()
        args.add = ["newuser"]
        args.delete = None
        args.list = False
        args.show = None
        args.regen_password = None
        
        with patch.object(handler, "add_user") as mock_add:
            handler.dispatch(args)
            assert args.action == "add"
            assert args.username == "newuser"
            mock_add.assert_called_once_with(args)

    def test_user_handler_routes_list_correctly(self, app_instance):
        app_instance.services.mode = "local"
        handler = UserHandler(app_instance)
        
        args = MagicMock()
        args.add = None
        args.delete = None
        args.list = True
        args.show = None
        args.regen_password = None
        
        with patch.object(handler, "list_users") as mock_list:
            handler.dispatch(args)
            assert args.action == "list"
            mock_list.assert_called_once_with(args)


class TestAuthClientInterceptor:
    def test_auth_client_interceptor_adds_bearer_token(self):
        # Mock token provider
        token_provider = MagicMock(return_value="my-super-secret-token")
        interceptor = AuthClientInterceptor(token_provider)
        
        # Mock ClientCallDetails using namedtuple
        from collections import namedtuple
        ClientCallDetails = namedtuple('ClientCallDetails', ['method', 'timeout', 'metadata', 'credentials', 'wait_for_ready', 'compression'])
        
        mock_details = ClientCallDetails(
            method="/connpy.NodeService/list_nodes",
            timeout=10,
            metadata=[],
            credentials=None,
            wait_for_ready=True,
            compression=None
        )
        
        intercepted_details = interceptor._add_metadata(mock_details)
        
        # Verify metadata was injected
        metadata_dict = dict(intercepted_details.metadata)
        assert "authorization" in metadata_dict
        assert metadata_dict["authorization"] == "Bearer my-super-secret-token"

    def test_auth_client_interceptor_no_token(self):
        token_provider = MagicMock(return_value=None)
        interceptor = AuthClientInterceptor(token_provider)
        
        from collections import namedtuple
        ClientCallDetails = namedtuple('ClientCallDetails', ['method', 'timeout', 'metadata', 'credentials', 'wait_for_ready', 'compression'])
        
        mock_details = ClientCallDetails(
            method="/connpy.NodeService/list_nodes",
            timeout=10,
            metadata=[],
            credentials=None,
            wait_for_ready=True,
            compression=None
        )
        
        intercepted_details = interceptor._add_metadata(mock_details)
        
        # Verify metadata remains empty
        assert len(intercepted_details.metadata) == 0


class TestLoginHandlerStatus:
    def test_status_no_token(self, app_instance):
        handler = LoginHandler(app_instance)
        
        with patch("os.path.exists", return_value=False):
            with patch("connpy.printer.warning") as mock_warning:
                handler.show_status()
                mock_warning.assert_called_once_with("No active session found. You can log in using 'connpy login'.")

    def test_status_invalid_token(self, app_instance):
        handler = LoginHandler(app_instance)
        
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data="invalid-token")):
                with patch("connpy.printer.error") as mock_error:
                    handler.show_status()
                    mock_error.assert_called_once_with("Invalid local session token format.")

    def test_status_valid_token(self, app_instance):
        handler = LoginHandler(app_instance)
        
        # Mock token payload: {"sub": "testuser", "exp": 1780007003}
        # Part 1 (header): eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9
        # Part 2 (payload): eyJzdWIiOiJ0ZXN0dXNlciIsImV4cCI6MTc4MDAwNzAwM30
        # Part 3 (sig): signature
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0dXNlciIsImV4cCI6MTc4MDAwNzAwM30.signature"
        
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=token)):
                with patch("connpy.printer.success") as mock_success:
                    with patch("connpy.printer.info") as mock_info:
                        # Patch time so exp is in the future
                        with patch("datetime.datetime") as mock_dt:
                            mock_dt.now.return_value.timestamp.return_value = 1780000000
                            # Mock fromtimestamp for expiration display
                            mock_dt.fromtimestamp.return_value.strftime.return_value = "2026-05-28 19:23:23 UTC"
                            
                            handler.show_status()
                            mock_success.assert_called_once_with("Logged in as 'testuser'")


def mock_open(*args, **kwargs):
    from unittest.mock import mock_open as unittest_mock_open
    return unittest_mock_open(*args, **kwargs)
