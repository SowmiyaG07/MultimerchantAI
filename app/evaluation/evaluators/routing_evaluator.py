def normalize_agents(agents):
    """
         Normalize agent names into a comparable format.
    """
    
    if not agents:
        return []
    
    return sorted(agents)

def evaluating_routing(actual,expected):
    """
        Compare actual agent selection with expected selection.
    """
    
    actual_agents = normalize_agents(actual.get("selected_agents"))
    
    expected_agents = normalize_agents(expected.get("selected_agents"))
    
    
    passed = actual_agents == expected_agents
    
    return {
        "key": "routing_accuracy",
        "score": 1 if passed else 0,
        "actual": actual_agents,
        "expected" : expected_agents
    }