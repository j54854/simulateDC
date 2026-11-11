import pytest
from gtp import create_simulator, DefaultController, Direction, TJobGraph, edge_length
from gtp.graph import START, tjob_key


class GraphController(DefaultController):
    """ディスパッチのたびにグラフを構築してから，デフォルトの判断に委ねるコントローラ"""

    def __init__(self, env, gtps):
        super().__init__(env, gtps)
        self.tjob_graph = TJobGraph(gtps)

    def dispatch_pjobs(self, waiting_pjobs, last_picker):
        self.tjob_graph.update()
        return super().dispatch_pjobs(waiting_pjobs, last_picker)

    def dispatch_shuttle(self, shuttle, candidate_tjobs):
        self.tjob_graph.update()
        return super().dispatch_shuttle(shuttle, candidate_tjobs)

    def dispatch_lift(self, lift, candidate_tjobs):
        self.tjob_graph.update()
        return super().dispatch_lift(lift, candidate_tjobs)

    def dispatch_loop(self, loop, candidate_fw_tjobs):
        self.tjob_graph.update()
        return super().dispatch_loop(loop, candidate_fw_tjobs)


def test_graph_does_not_change_simulation():
    """グラフを構築してもシミュレーション結果（golden 値）が変わらない"""
    env = create_simulator(params={'PJOB': 20, 'SEED': 42}, controller_cls=GraphController)
    env.run(until=env.simulation_completed)
    makespans = [round(s.makespan, 2) for s in env.gtps.stations]
    assert makespans == [118.6, 133.2, 133.2, 155.4, 190.6]


def check_invariants(env, graph):
    gtps = env.gtps
    tjob_nodes = {n for n, d in graph.nodes(data=True) if d['node_type'] == 'tjob'}
    # TJob ノード集合は unpicked_tjobs と一致
    assert tjob_nodes == {tjob_key(t) for t in gtps.unpicked_tjobs}
    # ダミーはスタートとアイドル作業場のみ
    working = {d['picker'] for n, d in graph.nodes(data=True) if d.get('position') == 'station'}
    dummies = {n for n, d in graph.nodes(data=True) if d['node_type'] != 'tjob'}
    assert dummies == {START} | {('station', p) for p in range(env.PICKER) if p not in working}
    # start エッジは S から全作業場ノードへちょうど1本ずつ
    start_edges = [(u, v) for u, v, d in graph.edges(data=True) if d['kind'] == 'start']
    assert len(start_edges) == env.PICKER
    assert all(u == START for u, v in start_edges)
    # precedence は同じ作業場で，pjob.idx が隣り合うグループ間のみ
    for u, v, d in graph.edges(data=True):
        if d['kind'] == 'precedence':
            du, dv = graph.nodes[u], graph.nodes[v]
            assert du['picker'] == dv['picker']
            between = {graph.nodes[n]['pjob'] for n in tjob_nodes
                       if graph.nodes[n]['picker'] == du['picker'] and du['pjob'] < graph.nodes[n]['pjob'] < dv['pjob']}
            assert du['pjob'] < dv['pjob'] and not between
    # コンベヤ上の対象 TJob はセル順どおり（下流 -> 上流）に chain で結ばれる
    conveyors = [gtps.conveyors[2][p][0][Direction.FORWARD] for p in range(env.PICKER)]
    conveyors += [gtps.conveyors[1][a][0][Direction.FORWARD] for a in range(env.AISLE)]
    conveyors += [gtps.conveyors[0][a][f][Direction.FORWARD] for a in range(env.AISLE) for f in range(env.FLOOR)]
    for conveyor in conveyors:
        keys = [tjob_key(c.bucket.current_tjob()) for c in reversed(conveyor.cells) if c.bucket is not None]
        for u, v in zip(keys, keys[1:]):
            assert graph.has_edge(u, v, key='chain')
    # 全エッジの長さは非負
    assert all(edge_length(d) >= 0 for _, _, d in graph.edges(data=True))


@pytest.mark.parametrize('params', [
    {'PJOB': 50, 'SEED': 42},
    {'PJOB': 50, 'SEED': 1, 'OPENABLE': 1},  # バケットの共有（off_route・bucket エッジ）を起こす
])
def test_invariants_on_snapshots(params):
    """シミュレーション中の各時点のグラフが仕様上の不変条件を満たす"""
    env = create_simulator(params=params)
    tjob_graph = TJobGraph(env.gtps)
    checked = []

    def sampler(env):
        while True:
            yield env.timeout(1)
            check_invariants(env, tjob_graph.update())
            checked.append(env.now)

    env.process(sampler(env))
    env.run(until=env.simulation_completed)
    assert len(checked) > 0


def test_edge_length():
    """内訳からエッジ長を計算できる"""
    data = {'n_unit': 2, 'alphas': ('loop', 'lift'), 'n_beta': 1}
    assert edge_length(data) == 5
    assert edge_length(data, alpha={'loop': 3, 'lift': 0.5, 'shuttle': 1}, beta=2) == 7.5
