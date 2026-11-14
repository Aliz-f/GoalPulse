from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from goal_management.lifecycle import transition
def test_transition_matrix_preserves_terminal_states():
    assert transition('active','pause')=='paused'
    assert transition('paused','resume')=='active'
    assert transition('completed','resume') is None
    assert transition('cancelled','complete') is None
