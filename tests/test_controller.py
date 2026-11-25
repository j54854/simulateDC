from gtp import DefaultController, Progress, create_simulator


def test_candidates_are_not_filtered():
    """コントローラには，着手可能な候補がすべて渡る（絞り込みは方策が選ぶ）"""
    n_excluded = {'shuttle': 0, 'lift': 0, 'loop': 0}

    class CheckController(DefaultController):
        def count(self, kind, tjobs):
            n_excluded[kind] += len(self.precedence_filter(tjobs)) < len(tjobs)

        def dispatch_shuttle(self, shuttle, candidate_tjobs):
            assert candidate_tjobs == [
                tjob for tjob in shuttle.tjobs if tjob.progress == Progress.TO_FW_SHTL
                and tjob.bucket.is_at_home() and tjob is tjob.bucket.current_tjob()]
            self.count('shuttle', candidate_tjobs)
            return super().dispatch_shuttle(shuttle, candidate_tjobs)

        def dispatch_lift(self, lift, candidate_tjobs):
            assert candidate_tjobs == lift.tjobs
            self.count('lift', candidate_tjobs)
            return super().dispatch_lift(lift, candidate_tjobs)

        def dispatch_loop(self, loop, candidate_fw_tjobs):
            assert candidate_fw_tjobs == [tjob for tjob in loop.tjobs if tjob.progress == Progress.TO_FW_LOOP]
            self.count('loop', candidate_fw_tjobs)
            return super().dispatch_loop(loop, candidate_fw_tjobs)

    env = create_simulator(params={'PJOB': 100, 'SEED': 1234}, controller_cls=CheckController)
    env.run(until=env.simulation_completed)
    assert all(n > 0 for n in n_excluded.values()), n_excluded  # 絞り込みで除かれる候補が実際に渡っている


def test_candidates_can_be_modified():
    """渡された候補のリストを書き換えても，待ち行列は変わらない"""

    class ClearController(DefaultController):
        def dispatch_shuttle(self, shuttle, candidate_tjobs):
            return self.clear(shuttle, candidate_tjobs, super().dispatch_shuttle)

        def dispatch_lift(self, lift, candidate_tjobs):
            return self.clear(lift, candidate_tjobs, super().dispatch_lift)

        def dispatch_loop(self, loop, candidate_fw_tjobs):
            return self.clear(loop, candidate_fw_tjobs, super().dispatch_loop)

        def clear(self, unit, tjobs, dispatch):
            chosen = dispatch(unit, tjobs)
            before = list(unit.tjobs)
            tjobs.clear()
            assert unit.tjobs == before
            return chosen

    env = create_simulator(params={'PJOB': 20, 'SEED': 42}, controller_cls=ClearController)
    env.run(until=env.simulation_completed)
    assert [round(s.makespan, 2) for s in env.gtps.stations] == [118.6, 133.2, 133.2, 155.4, 190.6]


class FakeTJob:
    def __init__(self, late):
        self.late = late

    def late_preceding_tjob_count(self):
        return self.late


def test_precedence_filter():
    controller = create_simulator(params={'PJOB': 1}).gtps.controller
    tjobs = [FakeTJob(late) for late in (2, 1, 3, 1)]
    assert controller.precedence_filter(tjobs) == [tjobs[1], tjobs[3]]
    assert controller.precedence_filter([]) == []
