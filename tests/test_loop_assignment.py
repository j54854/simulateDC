from gtp import create_simulator
from gtp.models import Cell


def test_goal_of_assigned_car_is_not_overwritten(monkeypatch):
    """押されたのと同じ時刻に割り付けられた台車の行き先が，退避先で上書きされない"""
    assign_goal = Cell.assign_goal
    n_assigned = 0

    def checked_assign_goal(self, goal_node):
        nonlocal n_assigned
        if self.assigned_tjob is not None:
            n_assigned += 1
            assert goal_node.idx == self.assigned_tjob.get_goal_node_idx(), f'Car {self.idx} at {self.env.now}'
        return assign_goal(self, goal_node)

    monkeypatch.setattr(Cell, 'assign_goal', checked_assign_goal)
    env = create_simulator(params={'PJOB': 100, 'SEED': 14})  # 修正前は，上書きの後に例外で止まっていた
    env.run(until=env.simulation_completed)
    assert n_assigned > 0
