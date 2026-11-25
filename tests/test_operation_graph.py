import networkx as nx
import pytest
from gtp import create_simulator, DefaultController
from gtp.operation_graph import START, OnlineGraph

PARAMS = [
    {'PJOB': 20, 'SEED': 42},
    {'PJOB': 50, 'SEED': 1, 'REPEATABLE': 0},  # 再搬送を起こす
    {'PJOB': 50, 'SEED': 3, 'VEHICLE': 8},  # 台車不足
    {'PJOB': 100, 'SEED': 1234},
    {'PJOB': 50, 'SEED': 5, 'ITEM': 10, 'OPENABLE': 1},  # 同じバケットの共有を起こす
    {'PJOB': 50, 'SEED': 9, 'CONV_LEN': [3, 5, 3]},  # conv2 を短くして満杯を起こす
]
# 移載を伴う作業
TRANSFERS = {2, 4, 5, 7, 9, 10, 12, 15, 16, 17, 19, 20, 22, 25, 26, 27, 29, 30, 31, 33}


def run_with_snapshots(params, interval=3.37):
    """一定間隔でオンライングラフを作りながら，最後まで実行する（他のイベントと重なりにくい間隔）"""
    env = create_simulator(params=params)
    snapshots = []

    def monitor(env):
        while True:
            yield env.timeout(interval)
            graph = OnlineGraph(env.gtps, known_pick=True)
            states = {tjob: (len(tjob.history), tjob.retry_count +tjob.retry_needed) for tjob in graph.chains}
            snapshots.append((graph, states))

    env.process(monitor(env))
    env.run(until=env.simulation_completed)
    return env, snapshots


def resource_of(cell):
    """セルに対応する資源（台車・置場は None）"""
    unit = cell.unit
    kind = unit.__class__.__name__
    if kind == 'Shuttle':
        return ('shuttle', unit.aisle, unit.floor)
    if kind == 'Lift':
        return ('lift', unit.aisle, unit.direction)
    if kind == 'Station':
        return ('station', unit.picker)
    if kind == 'Conveyor':
        return ('conv', unit.stage, unit.dim0, unit.dim1, unit.direction, cell.idx)
    return None


def match_transfers(chain, future):
    """移載を伴う作業と，実績の移載を対応づける

    この後に再搬送が起きうるので，最後の荷卸し（15）より前は先頭から，15以後は末尾から数える．
    """
    idx = next((i for i, op in enumerate(chain) if op.no == 15), 0)
    before = [op for op in chain[:idx] if op.no in TRANSFERS]
    after = [op for op in chain[idx:] if op.no in TRANSFERS]
    assert len(before) +len(after) <= len(future)
    return list(zip(before, future)) +list(zip(after, future[len(future) -len(after):]))


def test_remaining_chains(snapshot_run):
    params, env, snapshots = snapshot_run
    plain = create_simulator(params=params)
    plain.run(until=plain.simulation_completed)
    assert [s.makespan for s in env.gtps.stations] == [s.makespan for s in plain.gtps.stations]  # 結果は変わらない
    seen = {'retrying': 0, 'emptied': 0, 'lap': 0, 'stored': 0}
    checked = 0
    for graph, states in snapshots:
        for tjob, chain in graph.chains.items():
            n_history, n_retry = states[tjob]
            assert all(op.duration >= 0 for op in chain)
            assert not any(op.running for op in chain[1:])  # 実行中になりうるのは先頭だけ
            seen['stored'] += not chain
            seen['retrying'] += any(op.round == 1 for op in chain)
            seen['emptied'] += bool(chain) and chain[-1].no == 18
            seen['lap'] += any(op.no == 14 for op in chain)
            future = [stamp for stamp in tjob.history[n_history:] if stamp.kind == 'transfer']
            pairs = match_transfers(chain, future)
            if tjob.retry_count == n_retry:  # この後に新たな再搬送がなければ，移載の列が実績と一致する
                assert len(pairs) == len(future), (tjob, graph.now)
                checked += 1
            for op, stamp in pairs:
                assert set(op.resources) == {resource_of(stamp.src), resource_of(stamp.dst)} -{None}, (op.key, stamp.time)
    assert checked > 0
    if params.get('REPEATABLE') == 0:
        assert seen['retrying'] > 0
    if params['PJOB'] == 100:
        assert seen['emptied'] > 0 and seen['lap'] > 0


def earliest_starts(graph):
    """最長経路による最早開始時刻（ピッキングは実現値に置き換える）"""
    g = graph.graph
    starts = {}
    for node in nx.topological_sort(g):  # 閉路があれば例外
        start = graph.now
        for u, _, data in g.in_edges(node, data=True):
            op = g.nodes[u]['op']
            if op is None:  # 開始ノード
                length = data['lag']
            else:
                length = realized(op) +data['lag']
            start = max(start, starts[u] +length)
        starts[node] = start
    return starts


def realized(op):
    """処理時間（未着手のピッキングは実現値）"""
    return op.tjob.work_time if op.no == 18 and not op.running else op.duration


def test_lower_bound(snapshot_run):
    """最早の移載時刻は，実際の移載時刻以下である"""
    params, env, snapshots = snapshot_run
    n_machine = n_fan = n_returning = 0
    for graph, states in snapshots:
        starts = earliest_starts(graph)
        for tjob, chain in graph.chains.items():
            future = [stamp for stamp in tjob.history[states[tjob][0]:] if stamp.kind == 'transfer']
            for op, stamp in match_transfers(chain, future):
                assert starts[op.key] +realized(op) <= stamp.time +1e-6, (op.key, graph.now, stamp.time)
        for _, _, data in graph.graph.edges(data=True):
            n_machine += data['kind'] == 'machine'
            n_fan += data['fan']
            n_returning += data['kind'] == 'start' and data['resource'] is not None
    assert n_machine > n_fan > 0 and n_returning > 0


def test_machine_order_matches_history(snapshot_run):
    """機械順序エッジと作業場順序エッジの向きが，実際の順と一致する"""
    params, env, snapshots = snapshot_run
    count = 0
    for graph, states in snapshots:
        actual = {}
        for tjob, chain in graph.chains.items():
            if tjob.retry_count == states[tjob][1]:
                future = [stamp for stamp in tjob.history[states[tjob][0]:] if stamp.kind == 'transfer']
                actual.update({op.key: stamp.time for op, stamp in match_transfers(chain, future)})
        for u, v, data in graph.graph.edges(data=True):
            if data['kind'] in ('machine', 'precedence') and u in actual and v in actual:
                assert actual[u] < actual[v], (u, v, graph.now)
                count += 1
    assert count > 0


def test_process_edges(snapshot_run):
    params, env, snapshots = snapshot_run
    for graph, _ in snapshots:
        chains = graph.chains.values()
        assert START in graph.graph
        assert graph.graph.number_of_nodes() == sum(len(chain) for chain in chains) +1
        n_process = sum(data['kind'] == 'process' for _, _, data in graph.graph.edges(data=True))
        assert n_process == sum(max(0, len(chain) -1) for chain in chains)


def test_other_edges(snapshot_run):
    """作業場順序・バケット・再搬送・開始エッジの張り方"""
    params, env, snapshots = snapshot_run
    n_retry = n_bucket = n_precedence = 0
    for graph, _ in snapshots:
        g = graph.graph
        assert nx.is_directed_acyclic_graph(g)
        assert len(nx.descendants(g, START)) == g.number_of_nodes() -1  # すべての作業が s から届く
        unloading = {}  # (作業場, PJob) -> 荷卸し（15）のノード
        for tjob, chain in graph.chains.items():
            for op in chain:
                if op.no == 15:
                    unloading.setdefault((tjob.pjob.station.picker, tjob.pjob.idx), set()).add(op.key)
        expected = set()
        for picker in range(graph.env.PICKER):
            idxs = sorted(idx for p, idx in unloading if p == picker)
            for i, j in zip(idxs, idxs[1:]):
                expected |= {(u, v) for u in unloading[picker, i] for v in unloading[picker, j]}
        assert {(u, v) for u, v, d in g.edges(data=True) if d['kind'] == 'precedence'} == expected
        n_precedence += len(expected)
        for u, v, data in g.edges(data=True):
            op, next_op = g.nodes[u]['op'], g.nodes[v]['op']
            if data['kind'] == 'bucket':
                assert (op.no, next_op.no) == (33, 1) and op.tjob is not next_op.tjob
                assert op.tjob.bucket is next_op.tjob.bucket
                n_bucket += 1
            elif data['kind'] == 'machine' and op.tjob is next_op.tjob:  # 同じ TJob の間は，再搬送だけ
                assert (op.round, op.no, next_op.round, next_op.no) == (0, 33, 1, 1)
                n_retry += 1
    assert n_precedence > 0
    if params['PJOB'] > 20:
        assert n_retry > 0
    if 'ITEM' in params:
        assert n_bucket > 0


DISPATCH_PARAMS = [
    {'PJOB': 20, 'SEED': 42},
    {'PJOB': 25, 'SEED': 1, 'REPEATABLE': 0},
    {'PJOB': 25, 'SEED': 5, 'ITEM': 10, 'OPENABLE': 1},
]


@pytest.mark.parametrize('params', DISPATCH_PARAMS)
def test_dispatch_time_graphs(params):
    """ディスパッチの中（他のイベントと同時刻の状態）で構築しても，結果を変えず，性質が成り立つ"""
    snapshots = []

    class GraphController(DefaultController):
        def snapshot(self, kind):
            graph = OnlineGraph(self.gtps, known_pick=True)
            snapshots.append((kind, graph, {tjob: len(tjob.history) for tjob in graph.chains}))

        def dispatch_pjobs(self, waiting_pjobs, last_picker):
            self.snapshot('pjobs')
            return super().dispatch_pjobs(waiting_pjobs, last_picker)

        def dispatch_shuttle(self, shuttle, candidate_tjobs):
            self.snapshot('shuttle')
            return super().dispatch_shuttle(shuttle, candidate_tjobs)

        def dispatch_lift(self, lift, candidate_tjobs):
            self.snapshot('lift')
            return super().dispatch_lift(lift, candidate_tjobs)

        def dispatch_loop(self, loop, candidate_fw_tjobs):
            self.snapshot('loop')
            return super().dispatch_loop(loop, candidate_fw_tjobs)

    env = create_simulator(params=params, controller_cls=GraphController)
    env.run(until=env.simulation_completed)
    plain = create_simulator(params=params)
    plain.run(until=plain.simulation_completed)
    assert [s.makespan for s in env.gtps.stations] == [s.makespan for s in plain.gtps.stations]
    assert {kind for kind, _, _ in snapshots} == {'pjobs', 'shuttle', 'lift', 'loop'}
    for kind, graph, n_history in snapshots:
        assert len(nx.descendants(graph.graph, START)) == graph.graph.number_of_nodes() -1
        starts = earliest_starts(graph)  # 閉路があれば例外
        for tjob, chain in graph.chains.items():
            future = [stamp for stamp in tjob.history[n_history[tjob]:] if stamp.kind == 'transfer']
            for op, stamp in match_transfers(chain, future):
                assert set(op.resources) == {resource_of(stamp.src), resource_of(stamp.dst)} -{None}, (kind, op.key)
                assert starts[op.key] +realized(op) <= stamp.time +1e-6, (kind, op.key, graph.now)


def test_running_pick_is_not_peeked():
    """既定では，実行中のピッキングの終了時刻（実現値）を使わない"""
    env = create_simulator(params=PARAMS[0])
    graph = OnlineGraph(env.gtps)
    assert graph.pick_time == pytest.approx(10.8)
    for elapsed, rest in ((0, 10.8), (7, 7.0), (13, 23.0), (61, 0)):
        assert graph.expected_pick_rest(elapsed) == pytest.approx(rest)
    count = 0
    while not env.simulation_completed.triggered:
        env.run(until=env.now +3.37)
        graphs = [OnlineGraph(env.gtps, known_pick=known) for known in (False, True)]
        for station in env.gtps.stations:
            cell = station.cells[0]
            if cell.bucket is None or cell.assigned_tjob not in graphs[0].chains:
                continue
            ops = [graph.chains[cell.assigned_tjob][0] for graph in graphs]
            if ops[0].no == 18 and ops[0].running:
                elapsed = env.now -cell.hold_start
                assert ops[0].duration == pytest.approx(graphs[0].expected_pick_rest(elapsed))
                assert ops[1].duration == pytest.approx(cell.hold_end -env.now)
                count += 1
    assert count > 0
