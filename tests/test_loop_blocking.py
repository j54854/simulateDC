from gtp import create_simulator
from gtp.models import Loop, Progress


def test_blocked_car_waits_until_pushed(monkeypatch):
    """荷卸し先が満杯の台車は，後続の台車に押されない限り前進しない"""
    move_ahead = Loop.move_ahead
    pushed_out = []

    def checked_move_ahead(self, env, vehicle):
        car = self.cells[vehicle]
        tjob = car.assigned_tjob
        if (tjob is not None and car.goal is car.node
                and tjob.progress in (Progress.ON_FW_LOOP, Progress.ON_BW_LOOP)
                and not car.node.to.cell.is_open()):
            # 順序制約による荷卸し失敗の周回は対象外
            if tjob.progress == Progress.ON_BW_LOOP or tjob.late_preceding_tjob_count() <= 0:
                assert car.is_pushed, f'Car {vehicle} left without being pushed at {env.now}'
                pushed_out.append(vehicle)
        return move_ahead(self, env, vehicle)

    monkeypatch.setattr(Loop, 'move_ahead', checked_move_ahead)
    env = create_simulator(params={'PJOB': 100, 'SEED': 1234})
    env.run(until=env.simulation_completed)
    assert len(pushed_out) > 0  # 押し出しが実際に起きる条件である
