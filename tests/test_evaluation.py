import pytest
from gtp import DefaultController, create_simulator
from gtp.block_graph import BlockGraph
from gtp.evaluation import Evaluation
from gtp.operation_graph import START, OnlineGraph
from gtp.reduction import Reduction
from test_operation_graph import DISPATCH_PARAMS, PARAMS


# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def snapshots(params, interval=3.37):  # 一定間隔の時点で，シミュレーションを止めて返す
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    env = create_simulator(params=params)
    while not env.simulation_completed.triggered:
        env.run(until=env.now +interval)
        yield env


@pytest.mark.parametrize('params', PARAMS, ids=str)
@pytest.mark.parametrize('length', ['L0', 'L3'])
def test_matches_longest_path(params, length):
    """直列化しなければ，参照実装の最長経路（トポロジカル順）と一致する"""
    tables = None
    count = 0
    for env in snapshots(params):
        graph = BlockGraph(env.gtps, length=length, tables=tables)
        tables = graph.tables
        expected = Reduction(OnlineGraph(env.gtps), length=length).times()
        origin = expected[START]
        actual = Evaluation(graph, serialize=False).times
        assert actual.keys() == expected.keys()
        for node, time in expected.items():
            assert abs(actual[node] -(time -origin)) <= (1e-9 if length == 'L0' else 0), (node, actual[node], time -origin)
        count += 1
    assert count > 0


@pytest.mark.parametrize('params', PARAMS, ids=str)
@pytest.mark.parametrize('length', ['L0', 'L3'])
def test_serialization(params, length):
    """直列化：全ノードが確定し，作業場の列が重ならず，PJob の順を守り，時刻は直列化前より早くならない"""
    tables = None
    changed = fans = 0
    for env in snapshots(params):
        graph = BlockGraph(env.gtps, length=length, tables=tables)
        tables = graph.tables
        plain = Evaluation(graph, serialize=False).times
        evaluation = Evaluation(graph)  # 全ノードが確定しなければ例外
        times = evaluation.times
        assert times.keys() == plain.keys()
        assert all(times[node] >= plain[node] -1e-9 for node in plain)
        changed += sum(times[node] > plain[node] +1e-9 for node in plain)
        fans += len(graph.fan_terms)
        for picker, (confirmed, others) in graph.station_lists.items():
            order = evaluation.orders[picker]
            assert len(order) == len(confirmed) +len(others)
            assert order[:len(confirmed)] == [block.tjob for block in confirmed]  # 確定した列は変えない
            indices = [tjob.pjob.idx for tjob in order[len(confirmed):]]
            assert indices == sorted(indices)  # PJob の順
            blocks = [graph.block_of[tjob, 'work'] for tjob in order]
            for prev, block in zip(blocks, blocks[1:]):
                if 'Q' in graph.ports[prev] and 'P' in graph.ports[block]:
                    assert times[block.port_key('P')] >= times[prev.port_key('Q')] -1e-9  # 重ならない
            assert len(evaluation.picks[picker]) == len(order)
        if length == 'L3':
            assert evaluation.inversions == 0
    assert fans > 0 and changed > 0  # 直列化で時刻が変わる場面を含む


@pytest.mark.parametrize('params', DISPATCH_PARAMS, ids=str)
def test_at_dispatch(params):
    """ディスパッチの中（他のイベントと同時刻の状態）でも評価でき，シミュレーション結果を変えない"""
    count = {'pjobs': 0, 'shuttle': 0, 'lift': 0, 'loop': 0}

    class EvaluatingController(DefaultController):
        def check(self, kind):
            for length in ('L0', 'L3'):
                evaluation = Evaluation(BlockGraph(self.gtps, length=length))
                if length == 'L3':
                    assert evaluation.inversions == 0
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

    env = create_simulator(params=params, controller_cls=EvaluatingController)
    env.run(until=env.simulation_completed)
    plain = create_simulator(params=params)
    plain.run(until=plain.simulation_completed)
    assert [s.makespan for s in env.gtps.stations] == [s.makespan for s in plain.gtps.stations]
    assert all(n > 0 for n in count.values())
