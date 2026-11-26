"""縮約グラフの評価（時刻順の最長経路と，作業場の直列化）

仕様は docs/tjob_graph.md の「評価（直列化）の段階」．モジュール名・クラス名は仮．
グラフ（BlockGraph）を読むだけで書き換えない．
"""
from __future__ import annotations
import heapq
import math
from .block_graph import BlockGraph
from .operation_graph import START

NODE, ARRIVE = 0, 1  # 待ち行列の出来事の種類（ノードの確定，a の確定）


# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Evaluation:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    """縮約グラフの各ノードの時刻を，時刻の小さい順に確定して求める．

    入ってくる項の元がすべて確定したノードを待ち行列に入れ，時刻の最も小さいものから確定する．
    到着・再帰還・帰還ブロックは，ブロックの終わり（時刻 ＋ ブロックの長さ）を優先度とする．
    時刻は開始ノードを 0 とする相対時刻（L0 は秒，L3 は単位）．

    Args:
        graph: 縮約グラフ．
        serialize: 作業場の順が未確定の TJob を直列化するか（False なら扇のままの最長経路）．

    Attributes:
        times: ノード -> 時刻．
        orders: 作業場 -> 作業場の列（TJob のリスト．確定した列 ＋ 直列化した列）．
        picks: 作業場 -> P のベクトル（列の順．P を過ぎた TJob は 0）．
        inversions: a の逆転の回数（作業場ごとに，取り出した a が前に取り出した a より小さかった回数の和）．
    """
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, graph: BlockGraph, serialize: bool = True) -> None:
        self.graph = graph
        self.serialize = serialize
        self.times = {}  # 確定したノード -> 時刻
        self.best = dict.fromkeys(graph.nodes, -math.inf)  # 未確定のノード -> 確定した元から得た時刻の最大
        self.indegree = dict.fromkeys(graph.nodes, 0)  # 未確定のノード -> 確定していない元の数
        self.out = {node: [] for node in (START, *graph.nodes)}  # ノード -> [(先，値，a の元になる項か)]
        self.tails = {block.key: graph.block_length(block) for block in graph.blocks if block.kind != 'work'}
        self.heap = []
        self.count = 0  # 待ち行列の同点を分ける通し番号
        self.held = set()  # 作業場の列に加わるまで確定させないノード（未確定の作業ブロックのポート）
        self.arrivals = {}  # 未確定の作業ブロックの Y -> ブロック（a の元になる項の先）
        self.lines = {}  # 作業場 -> 規則ごとの列（station_rules の順）
        self.blocks = {}  # 作業場 -> 列のブロック
        self.remaining = {}  # 作業場 -> {PJob の番号: まだ列に加わっていない TJob の数}
        self.waiting = {}  # 作業場 -> {PJob の番号: a を取り出したが，前の PJob を待って保留しているブロックのリスト}
        self.last_a = {}  # 作業場 -> 最後に取り出した a
        self.inversions = 0
        terms = graph.terms
        if serialize:
            terms = [term for term in terms if term not in graph.fan_terms]
            self.prepare_lines()
        # a の元になる項：到着 → 作業（または s からのループの残り）．接続口で荷卸しを待つ TJob は，s からの開始の項（a ＝ 0）
        chained = {term.dst for term in terms if term.kind == 'chain' and term.dst in self.arrivals}
        sources = set()  # a の元になる項を持つ Y
        for term in terms:
            arrival = term.dst in self.arrivals and term.kind == ('chain' if term.dst in chained else 'start')
            if arrival:
                assert term.dst not in sources, f'Two arrival terms into {term.dst}'
                sources.add(term.dst)
            self.add_edge(term.src, term.dst, term.value, arrival)
        assert sources == self.arrivals.keys(), 'Some unconfirmed work blocks have no arrival term'
        self.finalize(START, 0)
        self.run()
        if len(self.times) != len(self.out):
            rest = [node for node in self.out if node not in self.times]
            raise RuntimeError(f'{len(rest)} nodes are not finalized: {rest[:5]}')
        self.orders = {picker: [block.tjob for block in blocks] for picker, blocks in self.blocks.items()}
        self.picks = {picker: [self.times.get(graph.pick_node(block), 0) for block in blocks]
            for picker, blocks in self.blocks.items()}
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def prepare_lines(self) -> None:  # 作業場ごとの列を，確定した列から始める
        graph = self.graph
        for picker, (confirmed, others) in graph.station_lists.items():
            self.lines[picker] = [list(graph.station_members(rule, confirmed)) for rule in graph.station_rules(picker)]
            self.blocks[picker] = list(confirmed)
            self.remaining[picker] = {}
            self.waiting[picker] = {}
            for block in others:
                assert 'Y' in graph.ports[block], f'Unconfirmed work block has already been unloaded: {block.key}'
                idx = block.tjob.pjob.idx
                self.remaining[picker][idx] = self.remaining[picker].get(idx, 0) +1
                self.held.update(block.port_key(port) for port in graph.ports[block])
                self.arrivals[block.port_key('Y')] = block
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_edge(self, src: tuple, dst: tuple, value: float, arrival: bool = False) -> None:  # 項を加える
        # arrival: a の元になる項（到着 → 作業，または s から）か．元が確定済みなら，すぐに伝える（評価の途中で張る鎖の項）
        if src in self.times:
            self.best[dst] = max(self.best[dst], self.times[src] +value)
        else:
            self.out[src].append((dst, value, arrival))
            self.indegree[dst] += 1
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def push(self, priority: float, kind: int, tie: int, item) -> None:
        heapq.heappush(self.heap, (priority, kind, tie, self.count, item))
        self.count += 1
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def ready(self, node: tuple) -> None:  # 元がすべて確定したノードを待ち行列に入れる
        self.push(self.best[node] +self.tails.get(node, 0), NODE, 0, node)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def finalize(self, node: tuple, time: float) -> None:  # ノードを確定し，先へ伝える
        self.times[node] = time
        for dst, value, arrival in self.out[node]:
            self.best[dst] = max(self.best[dst], time +value)
            self.indegree[dst] -= 1
            if arrival:  # a が決まった
                block = self.arrivals[dst]
                self.push(time +value, ARRIVE, block.tjob.idx, block)
            if self.indegree[dst] == 0 and dst not in self.held:
                self.ready(dst)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def run(self) -> None:  # 待ち行列が空になるまで，時刻の小さい順に確定する
        while self.heap:
            priority, kind, _, _, item = heapq.heappop(self.heap)
            if kind == NODE:
                self.finalize(item, self.best[item])
            else:
                self.arrive(item, priority)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def arrive(self, block, a: float) -> None:  # a を取り出した：PJob の順を守って，作業場の列に加える
        picker = block.tjob.pjob.station.picker
        if a < self.last_a.get(picker, -math.inf):
            self.inversions += 1
        self.last_a[picker] = a
        remaining = self.remaining[picker]
        if block.tjob.pjob.idx != min(remaining):  # 前の PJob の TJob がまだ列に加わっていない
            self.waiting[picker].setdefault(block.tjob.pjob.idx, []).append(block)
            return
        self.join(picker, block)
        while remaining and min(remaining) in self.waiting[picker]:  # 前の PJob がそろったら，保留していたものを取り出した順に加える
            for waiting in self.waiting[picker].pop(min(remaining)):
                self.join(picker, waiting)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def join(self, picker: int, block) -> None:  # 列の末尾に加え，列の前のTJobからの鎖の項を張る
        graph = self.graph
        for rule, line in zip(graph.station_rules(picker), self.lines[picker]):
            if rule[5] is not None and rule[5] not in graph.ports[block]:
                continue  # この規則の列には数えない
            if len(line) >= rule[1]:
                term = graph.station_term(rule, line[-rule[1]], block)
                if term is not None:
                    self.add_edge(term.src, term.dst, term.value)
            line.append(block)
        self.blocks[picker].append(block)
        remaining = self.remaining[picker]
        idx = block.tjob.pjob.idx
        remaining[idx] -= 1
        if remaining[idx] == 0:
            del remaining[idx]
        for port in graph.ports[block]:
            node = block.port_key(port)
            self.held.discard(node)
            if self.indegree[node] == 0:
                self.ready(node)
