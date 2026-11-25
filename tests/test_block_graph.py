import pytest
from gtp import DefaultController, create_simulator
from gtp.block_graph import BlockGraph
from gtp.operation_graph import OnlineGraph
from gtp.reduction import Reduction
from test_operation_graph import DISPATCH_PARAMS, PARAMS

KINDS = {'start', 'chain', 'aisle', 'backward', 'precedence', 'bucket', 'station'}  # 項の種類


def compare(direct, reduction, tolerance=1e-9):
    """直接構築と参照実装のノードと項を突き合わせる"""
    assert len(direct.nodes) == len(set(direct.nodes))
    assert set(direct.nodes) == set(reduction.nodes)
    actual = {term[:4]: term.value for term in direct.terms}
    expected = {term[:4]: term.value for term in reduction.terms}
    assert len(expected) == len(reduction.terms)
    assert len(actual) == len(direct.terms)  # 同じ項が2本ない
    assert actual.keys() == expected.keys(), (set(actual) ^ set(expected))
    for key, value in expected.items():
        assert abs(actual[key] -value) <= tolerance, (key, actual[key], value)
    return len(actual)


@pytest.mark.parametrize('params', PARAMS, ids=str)
@pytest.mark.parametrize('known_pick', [False, True])
def test_matches_reduction(params, known_pick):
    """一定間隔の時点で，参照実装と一致する"""
    env = create_simulator(params=params)
    heads = set()
    tables = None
    count = 0
    kinds = set()
    while not env.simulation_completed.triggered:
        env.run(until=env.now +3.37)
        direct = BlockGraph(env.gtps, known_pick=known_pick, tables=tables)
        tables = direct.tables  # 累積表は使い回せる
        online = OnlineGraph(env.gtps, known_pick=known_pick)
        count += compare(direct, Reduction(online))
        l3 = BlockGraph(env.gtps, known_pick=known_pick, length='L3', tables=tables)
        compare(l3, Reduction(online, length='L3'), tolerance=0)
        assert all(term.value == int(term.value) for term in l3.terms)
        kinds |= {term.kind for term in direct.terms}
        heads |= {location[0] for location in direct.locations.values()}
    assert count > 0
    assert kinds >= KINDS -{'bucket'}
    if 'ITEM' in params:
        assert 'bucket' in kinds
    if params['PJOB'] > 20:
        assert heads >= {1, 3, 5, 10, 13, 15, 16, 18, 20, 23, 26, 28, 30, 32, 34}  # さまざまな位置を含む


@pytest.mark.parametrize('params', DISPATCH_PARAMS, ids=str)
def test_matches_reduction_at_dispatch(params):
    """ディスパッチの中（他のイベントと同時刻の状態）でも一致し，シミュレーション結果を変えない"""
    count = {'pjobs': 0, 'shuttle': 0, 'lift': 0, 'loop': 0}

    class CompareController(DefaultController):
        def check(self, kind):
            online = OnlineGraph(self.gtps)
            compare(BlockGraph(self.gtps), Reduction(online))
            compare(BlockGraph(self.gtps, length='L3'), Reduction(online, length='L3'), tolerance=0)
            count[kind] += 1

        def dispatch_pjobs(self, waiting_pjobs, last_picker):
            self.check('pjobs')
            return super().dispatch_pjobs(waiting_pjobs, last_picker)

        def dispatch_shuttle(self, shuttle, candidate_tjobs):
            self.check('shuttle')
            return super().dispatch_shuttle(shuttle, candidate_tjobs)

        def dispatch_lift(self, lift, candidate_tjobs):
            self.check('lift')
            return super().dispatch_lift(lift, candidate_tjobs)

        def dispatch_loop(self, loop, candidate_fw_tjobs):
            self.check('loop')
            return super().dispatch_loop(loop, candidate_fw_tjobs)

    env = create_simulator(params=params, controller_cls=CompareController)
    env.run(until=env.simulation_completed)
    plain = create_simulator(params=params)
    plain.run(until=plain.simulation_completed)
    assert [s.makespan for s in env.gtps.stations] == [s.makespan for s in plain.gtps.stations]
    assert all(n > 0 for n in count.values())


def test_weights():
    """L3 の区間の重みを変えても一致する"""
    weights = {'shuttle': 3, 'lift': 5, 'loop': 7, 'pick': 11}
    env = create_simulator(params=PARAMS[3])
    while not env.simulation_completed.triggered:
        env.run(until=env.now +19.37)
        direct = BlockGraph(env.gtps, length='L3', weights=weights)
        compare(direct, Reduction(OnlineGraph(env.gtps), length='L3', weights=weights), tolerance=0)
