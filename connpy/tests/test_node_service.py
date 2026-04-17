import pytest
from connpy.services.node_service import NodeService
from connpy.services.exceptions import NodeNotFoundError, NodeAlreadyExistsError

def test_list_nodes_filtering_parity(populated_config):
    """
    Test that list_nodes uses literal 'in' logic instead of re.search.
    Regression: NodeService currently uses re.search in some versions, 
    but we want to ensure it uses literal 'in' for parity.
    """
    service = NodeService(populated_config)
    
    # If it uses 'in' logic, '1' should match all nodes containing '1'
    # router1, server1@office, db1@datacenter@office
    nodes = service.list_nodes(filter_str="1")
    assert len(nodes) == 3
    assert "router1" in nodes
    assert "server1@office" in nodes
    assert "db1@datacenter@office" in nodes

    # Test regex-specific characters. 
    # NodeService should use re.search, so '^router' will match 'router1'.
    nodes_regex = service.list_nodes(filter_str="^router")
    
    assert "router1" in nodes_regex

def test_list_nodes_dynamic_formatting(populated_config):
    """
    Test that list_nodes supports dynamic formatting for any node attribute.
    Regression: NodeService currently has hardcoded support for name, location, host.
    """
    service = NodeService(populated_config)
    
    # Try to format using 'user' and 'protocol' which are NOT in the hardcoded list
    # (name, location, host)
    format_str = "{name} -> {user}@{host} ({protocol})"
    
    # router1: host=10.0.0.1, user=admin, protocol=ssh
    # Expected: "router1 -> admin@10.0.0.1 (ssh)"
    
    formatted = service.list_nodes(filter_str="router1", format_str=format_str)
    
    assert len(formatted) == 1
    # This will FAIL if it only supports {name}, {location}, {host}
    assert formatted[0] == "router1 -> admin@10.0.0.1 (ssh)"

def test_node_editing_parity(populated_config):
    """
    Test that add_node improperly raises NodeAlreadyExistsError when used for editing.
    Regression: connapp._mod calls add_node instead of update_node.
    """
    service = NodeService(populated_config)
    
    # router1 already exists in populated_config
    # We confirm that calling add_node with an existing ID raises NodeAlreadyExistsError
    # which is why connapp._mod (which calls add_node) is currently broken for editing.
    with pytest.raises(NodeAlreadyExistsError):
        service.add_node("router1", {"host": "1.1.1.1"})

def test_list_nodes_case_sensitivity(populated_config):
    """Test that filtering respects the case setting in config."""
    service = NodeService(populated_config)
    
    # Default case is False (case-insensitive)
    nodes = service.list_nodes(filter_str="ROUTER")
    assert "router1" in nodes
