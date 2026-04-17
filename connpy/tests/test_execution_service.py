import pytest
from unittest.mock import MagicMock, patch
from connpy.services.execution_service import ExecutionService

def test_run_commands_callback(populated_config):
    """Test that run_commands correctly passes on_node_complete to the executor."""
    service = ExecutionService(populated_config)
    
    # Mock the Nodes class in connpy.services.execution_service
    with patch("connpy.services.execution_service.Nodes") as MockNodes:
        mock_executor = MockNodes.return_value
        mock_executor.run.return_value = {"router1": "output"}
        
        callback = MagicMock()
        
        service.run_commands(
            nodes_filter="router1",
            commands=["show version"],
            on_node_complete=callback
        )
        
        # Verify executor.run was called with on_complete=callback
        # Note: ExecutionService calls executor.run(..., on_complete=on_node_complete, ...)
        MockNodes.return_value.run.assert_called_once()
        args, kwargs = MockNodes.return_value.run.call_args
        assert kwargs["on_complete"] == callback

def test_test_commands_callback_regression(populated_config):
    """
    Test that test_commands correctly passes on_node_complete to the executor.
    Regression: ExecutionService.test_commands currently ignores on_node_complete.
    """
    service = ExecutionService(populated_config)
    
    with patch("connpy.services.execution_service.Nodes") as MockNodes:
        mock_executor = MockNodes.return_value
        mock_executor.test.return_value = {"router1": {"PASS": True}}
        
        callback = MagicMock()
        
        service.test_commands(
            nodes_filter="router1",
            commands=["show version"],
            expected=["12.4"],
            on_node_complete=callback
        )
        
        # This is expected to FAIL because ExecutionService.test_commands 
        # doesn't pass on_complete to executor.test
        MockNodes.return_value.test.assert_called_once()
        args, kwargs = MockNodes.return_value.test.call_args
        
        # We expect 'on_complete' to be in kwargs and equal to our callback
        assert "on_complete" in kwargs, "on_complete parameter missing in call to executor.test"
        assert kwargs["on_complete"] == callback
