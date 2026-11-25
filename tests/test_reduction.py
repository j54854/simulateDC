import networkx as nx
import pytest
from gtp.operation_graph import BW, FW, START
from gtp.reduction import KINDS, PORTS, SERIES, Reduction

RANGES = {'rereturn': (25, 33), 'arrival': (1, 12), 'work': (15, 22), 'return': (25, 33)}


def test_split(snapshot_run):
    """ブロックの構成：各作業がちょうど1つのブロックか直列縮約に入り，順序が保たれる"""
    params, env, snapshots = snapshot_run
    seen = set()  # (先頭のブロックの型, 先頭の作業番号)
    for online, _ in snapshots:
        reduction = Reduction(online)
        assert len({block.key for block in reduction.blocks}) == len(reduction.blocks)
        by_tjob = {}
        for link in reduction.links:
            by_tjob.setdefault(link.dst.tjob, []).append(link)
        for tjob, chain in online.chains.items():
            links = by_tjob.get(tjob, [])
            # 直列縮約した作業とブロックの作業を順につなぐと，元の鎖に戻る
            assert [op for link in links for op in (*link.series, *link.dst.ops)] == chain
            for i, link in enumerate(links):
                assert link.src is (links[i -1].dst if i > 0 else None)
            kinds = [link.dst.kind for link in links]
            assert kinds == sorted(kinds, key=KINDS.index)
            assert ('rereturn' in kinds) <= ('arrival' in kinds)
            for link in links:
                low, high = RANGES[link.dst.kind]
                assert all(low <= op.no <= high for op in link.dst.ops)
            if chain:
                seen.add((links[0].dst.kind, chain[0].no))
            if chain and chain[-1].no == 18:  # 空になるバケット：帰還ブロックがない
                assert kinds[-1] == 'work'
    assert {kind for kind, _ in seen} >= {'arrival', 'work', 'return'}
    if params['PJOB'] > 20:
        assert ('rereturn', 34) in seen


def test_series_operations_are_exactly_reducible(snapshot_run):
    """直列縮約する作業に出入りするのは，鎖の工程エッジ（先頭なら開始エッジ）1本だけである"""
    params, env, snapshots = snapshot_run
    for online, _ in snapshots:
        g = online.graph
        for node, data in g.nodes(data=True):
            if node != START and data['op'].no in SERIES:
                assert g.in_degree(node) == 1 and g.out_degree(node) == 1
                (u, _, d), = g.in_edges(node, data=True)
                assert d['kind'] == 'process' or (u == START and d['length'] == 0)
                (_, _, d), = g.out_edges(node, data=True)
                assert d['kind'] == 'process'


def test_block_topology_is_acyclic(snapshot_run):
    """ブロックにまとめたグラフに閉路がない（エッジの長さによらない構造の性質）"""
    params, env, snapshots = snapshot_run
    for online, _ in snapshots:
        reduction = Reduction(online)
        block_of = reduction.block_of
        graph = nx.DiGraph((link.src.key, link.dst.key) for link in reduction.links if link.src is not None)
        for u, v in online.graph.edges():
            if u in block_of and v in block_of and block_of[u] is not block_of[v]:
                graph.add_edge(block_of[u].key, block_of[v].key)
        assert nx.is_directed_acyclic_graph(graph), online.now


def test_terms_equal_offset_differences(snapshot_run):
    """資源の項が，入口・出口オフセットの差 f_B(k) - e_B'(k) に一致し，（組，資源）ごとに1本である"""
    _, env, snapshots = snapshot_run
    kinds = set()
    for online, _ in snapshots:
        reduction = Reduction(online, ports=False)
        blocks = {block.key: block for block in reduction.blocks}
        pairs = set()
        for term in reduction.terms:
            if term.kind in ('start', 'chain'):
                continue
            assert (term.src, term.dst, term.resource) not in pairs
            pairs.add((term.src, term.dst, term.resource))
            kinds.add(term.kind)
            src, dst = blocks[term.src], blocks[term.dst]
            if term.kind == 'precedence':
                first, last = [op for op in dst.ops if op.no == 15], [op for op in src.ops if op.no == 15]
            elif term.kind == 'bucket':
                first, last = [op for op in dst.ops if op.no == 1], [op for op in src.ops if op.no == 33]
            else:
                first, last = [[op for op in block.ops if term.resource in op.resources] for block in (dst, src)]
            entry = sum(op.duration for op in dst.ops[:dst.ops.index(first[0])])
            leave = sum(op.duration for op in src.ops[:src.ops.index(last[-1]) +1])
            store = src.tjob.bucket.home_store
            if term.resource[0] == 'lift' and last[-1].no == 29:
                leave += store.floor *env.MT_LIFT
            if last[-1].no == 33:
                leave += (env.ROW -store.row) *env.MT_SHTL
            assert term.value == pytest.approx(leave -entry), term
    assert kinds >= {'station', 'aisle', 'backward', 'precedence'}


def test_example_in_document(snapshot_run):
    """文書の例（同じ階の置場で出庫を待つ i, j）の表のうち，シャトルと conv0^FW の行"""
    _, env, snapshots = snapshot_run
    count = 0
    for online, _ in snapshots:
        reduction = Reduction(online, ports=False)
        blocks = {block.key: block for block in reduction.blocks}
        for term in reduction.terms:
            if term.kind != 'aisle' or term.resource[0] == 'lift':
                continue
            i, j = blocks[term.src], blocks[term.dst]
            if i.ops[0].no > 3 or j.ops[0].no != 1 or term.resource[:2] == ('conv', 1):
                continue
            a_i, a_j = [sum(op.duration for op in block.ops if op.no <= 3) for block in (i, j)]
            rise = i.tjob.bucket.home_store.floor *env.MT_LIFT
            if term.resource[0] == 'shuttle':
                expected = a_i +env.LT_SHTL
            elif term.resource[5] == 0:
                expected = a_i +env.LT_SHTL +env.MT_CONV -a_j
            elif term.resource[5] < env.CONV_LEN[0] -1:
                expected = a_i -a_j +2 *env.MT_CONV
            else:
                expected = a_i -a_j +env.MT_CONV +rise +env.LT_LIFT
            assert term.value == pytest.approx(expected), term
            count += 1
    assert count > 0


def test_rigid_times_are_not_earlier_than_online(snapshot_run):
    """剛体近似の時刻は，オンライングラフの最早開始時刻以上である（制約を強めるだけなので）"""
    _, _, snapshots = snapshot_run
    later = 0
    for online, _ in snapshots:
        reduction = Reduction(online, ports=False)
        times = reduction.times()
        starts = {}
        for node in nx.topological_sort(online.graph):
            starts[node] = max((starts[u] +data['length'] for u, _, data in online.graph.in_edges(node, data=True)), default=online.now)
        for key, (node, offset) in reduction.anchor.items():
            assert times[node] +offset >= starts[key] -1e-6, key
            later += times[node] +offset > starts[key] +1e-6
    assert later > 0  # 実際に強まっている


def is_work(node):
    return node != START and node[3] == 'work'


def test_ports(snapshot_run):
    """ポートのノード・項の構成"""
    params, env, snapshots = snapshot_run
    remaining = set()
    resources = set()
    capacities = set()
    for online, _ in snapshots:
        rigid, ported = Reduction(online, ports=False), Reduction(online)
        # 到着・帰還どうしの項は，ポートの有無によらない
        assert ({term for term in rigid.terms if not is_work(term.src) and not is_work(term.dst)}
                == {term for term in ported.terms if not is_work(term.src) and not is_work(term.dst)})
        # 残っているポート
        for block, cuts in ported.port_cuts.items():
            nos = {op.no for op in block.ops}
            emptied = block.ops[-1].no == 18
            expected = [port for port, no in zip(PORTS, (15, 17, 18 if emptied else 19, 22)) if no in nos]
            assert list(cuts) == expected
            assert len(cuts) > 0
            remaining.add((block.ops[0].no, ''.join(cuts)))
        assert set(ported.nodes) == set(ported.graph()) -{START}
        keys = [(term.src, term.dst, term.resource, term.kind) for term in ported.terms]
        assert len(keys) == len(set(keys))
        for term in ported.terms:
            if term.kind == 'station':
                resources.add(term.resource[0])
                if term.resource[0] == 'capacity':
                    capacities.add(('capacity', term.resource[2]))
                    # 往路は搬入 → 荷卸し，復路は積み出し → 搬出の完了
                    _, _, direction = term.resource
                    assert (term.src[-1], term.dst[-1]) == (('P', 'Y') if direction == FW else ('O', 'Q'))
            if is_work(term.src) or is_work(term.dst):
                assert term.value >= 0, term  # 作業ブロックに出入りする項は負にならない
    assert {'YPQO', 'PQO', 'QO', 'O'} <= {ports for _, ports in remaining}
    assert {'station', 'order'} <= resources
    if 'CONV_LEN' in params:
        assert {('capacity', FW), ('capacity', BW)} <= capacities


def test_port_times(snapshot_run):
    """ポートは剛体近似を緩めるだけなので，ポートの時刻は，剛体の対応する時刻以下である"""
    _, _, snapshots = snapshot_run
    earlier = {port: 0 for port in PORTS}
    for online, _ in snapshots:
        rigid, ported = Reduction(online, ports=False), Reduction(online)
        rigid_times, times = rigid.times(), ported.times()  # 閉路があれば例外
        for block, cuts in ported.port_cuts.items():
            for port, cut in cuts.items():
                rigid_time = rigid_times[block.key] +sum(op.duration for op in block.ops[:cut])
                assert times[block.port_key(port)] <= rigid_time +1e-6, (block.key, port)
                earlier[port] += times[block.port_key(port)] < rigid_time -1e-6
        for block in ported.blocks:
            if block.kind != 'work':
                assert times[block.key] <= rigid_times[block.key] +1e-6, block.key
    assert earlier['P'] > 0 and earlier['Q'] > 0


def stage(block):
    """ブロックの先頭の位置"""
    no = block.ops[0].no
    if block.kind == 'arrival':
        return 'store' if no <= 4 else 'lift' if no <= 9 else 'conv1'
    return 'conv1' if no <= 26 else 'lift' if no <= 29 else 'conv0' if no <= 30 else 'shuttle'


def test_l3_topology(snapshot_run):
    """L3 は L0 と値だけが違う（復帰中の機械からの開始エッジを除く）"""
    _, _, snapshots = snapshot_run
    for online, _ in snapshots:
        for ports in (True, False):
            l0, l3 = Reduction(online, ports), Reduction(online, ports, 'L3')
            edges0 = {term[:4] for term in l0.terms if term.kind != 'start' or term.resource is None}
            assert edges0 == {term[:4] for term in l3.terms}
            assert l0.nodes == l3.nodes
            assert all(term.value == int(term.value) for term in l3.terms)
            l3.times()


def test_l3_values(snapshot_run):
    """文書の L3 の表の値"""
    _, env, snapshots = snapshot_run
    seen = set()
    for online, _ in snapshots:
        reduction = Reduction(online, length='L3')
        assert reduction.unit == pytest.approx(12.8)
        blocks = {block.key: block for block in reduction.blocks}
        for term in reduction.terms:
            src, dst, value = blocks.get(term.src[:4]), blocks.get(term.dst[:4]), term.value
            if term.kind in ('start', 'precedence'):
                assert value == 0
            elif term.kind == 'station':
                assert value == 0
            elif term.kind == 'bucket':
                remaining = {'conv1': 3, 'lift': 3, 'conv0': 2, 'shuttle': 2}[stage(src)]
                assert value == remaining
                seen.add('bucket')
            elif term.kind == 'aisle':
                name = term.resource[0]
                if name == 'shuttle':
                    assert value == 2
                elif name == 'lift':
                    assert value == (1 if stage(src) != 'store' else 3) -(2 if stage(dst) == 'store' else 0)
                elif stage(src) == 'conv1' and term.resource[1] == 1:
                    assert value == {'conv1': 0, 'lift': -1, 'store': -3}[stage(dst)]
                seen.add(('aisle', name, value))
            elif term.kind == 'backward':
                name = term.resource[0]
                if name == 'shuttle':
                    if src.kind == 'arrival':
                        assert value == 2 -(1 if stage(dst) in ('conv1', 'lift') else 0)
                    elif dst.kind == 'arrival':  # 入庫 → 出庫
                        assert value == {'conv1': 3, 'lift': 3, 'conv0': 2, 'shuttle': 2}[stage(src)]
                elif name == 'lift':
                    assert value == 1
                else:
                    assert value <= 1
                seen.add(('backward', name, value))
            elif term.kind == 'chain':
                if src is None:  # s から
                    port = term.dst[4] if len(term.dst) > 4 else None
                    if port == 'Q':
                        assert value == 1
                    elif port == 'P':
                        assert value == 0
                    elif port == 'O':
                        assert 0 <= value <= 2
                    else:  # ループ上（荷卸し前）
                        assert value == 1
                    seen.add(('start', port))
                elif src.kind == 'arrival':  # 到着 → 作業
                    pickup = round(src.ops[0].duration /reduction.unit) if src.ops[0].no == 11 else 0
                    assert value == {'store': 4, 'lift': 2, 'conv1': 1}[stage(src)] +pickup
                    seen.add(('arrival', stage(src)))
                elif src.kind == 'rereturn':
                    assert value == {'conv1': 3, 'lift': 3, 'conv0': 2, 'shuttle': 2}[stage(src)]
                elif dst.kind == 'return':  # 作業 → 帰還（Oから）
                    assert value == 1
                else:  # ポートの間
                    expected = {('Y', 'P'): 0, ('P', 'Q'): 1, ('Q', 'O'): 0}[term.src[4], term.dst[4]]
                    assert value == expected
    assert {('arrival', 'store'), ('arrival', 'lift'), ('arrival', 'conv1'), ('start', 'Q'), ('start', 'Y')} <= seen
    assert ('aisle', 'lift', 1) in seen and ('aisle', 'lift', -1) in seen
