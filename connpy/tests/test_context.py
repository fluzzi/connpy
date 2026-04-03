"""Tests for connpy.core_plugins.context"""
import pytest
from unittest.mock import MagicMock, patch
from connpy.core_plugins.context import context_manager, Preload, Entrypoint

@pytest.fixture
def mock_connapp():
    connapp = MagicMock()
    connapp.config.config = {
        "contexts": {"all": [".*"]},
        "current_context": "all"
    }
    return connapp

class TestContextManager:
    def test_init(self, mock_connapp):
        cm = context_manager(mock_connapp)
        assert cm.contexts == {"all": [".*"]}
        assert cm.current_context == "all"
        assert len(cm.regex) == 1

    def test_add_context_success(self, mock_connapp):
        cm = context_manager(mock_connapp)
        cm.add_context("prod", ["^prod_.*"])
        assert "prod" in cm.contexts
        mock_connapp._change_settings.assert_called_with("contexts", cm.contexts)

    def test_add_context_invalid_name(self, mock_connapp):
        cm = context_manager(mock_connapp)
        with pytest.raises(SystemExit) as exc:
            cm.add_context("prod-env", ["Regex"])
        assert exc.value.code == 1

    def test_add_context_already_exists(self, mock_connapp):
        cm = context_manager(mock_connapp)
        with pytest.raises(SystemExit) as exc:
            cm.add_context("all", ["Regex"])
        assert exc.value.code == 2

    def test_modify_context_success(self, mock_connapp):
        cm = context_manager(mock_connapp)
        cm.add_context("prod", ["old"])
        cm.modify_context("prod", ["new"])
        assert cm.contexts["prod"] == ["new"]

    def test_modify_context_all(self, mock_connapp):
        cm = context_manager(mock_connapp)
        with pytest.raises(SystemExit) as exc:
            cm.modify_context("all", ["new"])
        assert exc.value.code == 3

    def test_modify_context_not_exists(self, mock_connapp):
        cm = context_manager(mock_connapp)
        with pytest.raises(SystemExit) as exc:
            cm.modify_context("fake", ["new"])
        assert exc.value.code == 4

    def test_delete_context_success(self, mock_connapp):
        cm = context_manager(mock_connapp)
        cm.add_context("prod", ["old"])
        cm.delete_context("prod")
        assert "prod" not in cm.contexts

    def test_delete_context_all(self, mock_connapp):
        cm = context_manager(mock_connapp)
        with pytest.raises(SystemExit) as exc:
            cm.delete_context("all")
        assert exc.value.code == 3

    def test_delete_context_current(self, mock_connapp):
        mock_connapp.config.config["current_context"] = "prod"
        mock_connapp.config.config["contexts"]["prod"] = [".*"]
        cm = context_manager(mock_connapp)
        with pytest.raises(SystemExit) as exc:
            cm.delete_context("prod")
        assert exc.value.code == 5

    def test_set_context_success(self, mock_connapp):
        cm = context_manager(mock_connapp)
        cm.contexts["prod"] = [".*"]
        cm.set_context("prod")
        mock_connapp._change_settings.assert_called_with("current_context", "prod")

    def test_set_context_already_set(self, mock_connapp):
        cm = context_manager(mock_connapp)
        with pytest.raises(SystemExit) as exc:
            cm.set_context("all")
        assert exc.value.code == 0

    def test_match_regexp(self, mock_connapp):
        mock_connapp.config.config["contexts"]["all"] = ["^prod", "^test"]
        cm = context_manager(mock_connapp)
        assert cm.match_any_regex("prod_node", cm.regex) is True
        assert cm.match_any_regex("test_node", cm.regex) is True
        assert cm.match_any_regex("dev_node", cm.regex) is False

    def test_modify_node_list(self, mock_connapp):
        mock_connapp.config.config["contexts"]["all"] = ["^prod"]
        cm = context_manager(mock_connapp)
        nodes = ["prod_1", "dev_1", "prod_2"]
        result = cm.modify_node_list(result=nodes)
        assert result == ["prod_1", "prod_2"]

    def test_modify_node_dict(self, mock_connapp):
        mock_connapp.config.config["contexts"]["all"] = ["^prod"]
        cm = context_manager(mock_connapp)
        nodes = {"prod_1": {}, "dev_1": {}, "prod_2": {}}
        result = cm.modify_node_dict(result=nodes)
        assert set(result.keys()) == {"prod_1", "prod_2"}
