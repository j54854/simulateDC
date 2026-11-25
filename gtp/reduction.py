from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, NamedTuple
import networkx as nx
from .models import TJob
from .operation_graph import START, FW, BW, OnlineGraph, Operation

SERIES = (13, 14, 23, 24, 34)  # 直列縮約する作業（ループ搬送・周回）
PORTS = ('Y', 'P', 'Q', 'O')  # 作業ブロックのポート（仮称．荷卸し・搬入・搬出の完了・積み出し）
# L3 の区間（名前，最後の作業番号．鎖の上の順）．重みのない区間（コンベヤ）は0
SEGMENTS = (('shuttle_out', 4), ('conv0_out', 5), ('lift_out', 9), ('conv1_out', 10), ('loop_out', 14),
    ('conv2_out', 16), ('pick', 19), ('conv2_in', 22), ('loop_in', 24),
    ('conv1_in', 26), ('lift_in', 29), ('conv0_in', 30), ('shuttle_in', 33), ('loop_retry', 34))
SEGMENT_INDEX = {name: i for i, (name, _) in enumerate(SEGMENTS)}
DEFAULT_WEIGHTS = {'shuttle': 2, 'lift': 1, 'loop': 1, 'pick': 1}  # 区間の重み α の既定値
KINDS = ('rereturn', 'arrival', 'work', 'return')  # ブロックの型（再帰還・到着・作業・帰還．鎖の上の順）

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
@dataclass(eq=False)
class Block:  # ブロック（1つのTJobの鎖の連続した区間のうち，残っている作業）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    tjob: TJob
    kind: str  # 型（KINDS のいずれか）
    ops: list[Operation] = field(default_factory=list)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    @property
    def key(self) -> tuple:  # ノードのキー
        return ('block', self.tjob.pjob.idx, self.tjob.idx, self.kind)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def port_key(self, port: str) -> tuple:  # ポートのノードのキー（作業ブロックのみ）
        return (*self.key, port)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def op(self, no: int) -> Operation | None:  # 指定された番号の作業（残っていなければNone）
        return next((op for op in self.ops if op.no == no), None)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
@dataclass(eq=False)
class Link:  # 同じTJobのブロックの間のつながり（直列縮約した作業を含む）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    src: Block | None  # 前のブロック（Noneなら開始ノード）
    dst: Block  # 次のブロック
    series: list[Operation]  # 間にある，直列縮約した作業

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Term(NamedTuple):  # （組，資源）ごとの項（不等式 X(dst) >= X(src) + value）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    src: tuple  # 始点のノード
    dst: tuple  # 終点のノード
    kind: str  # 種類（start・chain・station・aisle・backward・precedence・bucket）
    resource: tuple | None  # 資源（同じTJobのつながりと s からの長さ0のエッジでは None）
    value: float  # 長さ

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def edge_kind(u: Operation, v: Operation, kind: str, resource: tuple) -> str:  # オンラインのエッジに対応する種類
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    if kind != 'machine':
        return kind  # precedence・bucket
    if resource[0] == 'station' or resource[:2] == ('conv', 2):
        return 'station'
    if resource[0] == 'shuttle':
        return 'aisle' if u.no <= 4 and v.no <= 4 else 'backward'
    direction = resource[2] if resource[0] == 'lift' else resource[4]
    return 'backward' if direction == BW else 'aisle'

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def segment_of(op: Operation) -> int:  # 作業の属する区間
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    return next(i for i, (_, last) in enumerate(SEGMENTS) if op.no <= last)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Reduction:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    """オンライングラフの縮約（参照実装．速さは問わない）．

    定義は docs/tjob_graph.md の「オペレーショングラフからの導出（縮約）」を参照．
    
    Args:
        online: 縮約するオンライングラフ．
        ports: 作業ブロックをポートに分けるか（Falseなら，作業ブロックも剛体のまま）．
        length: 'L0'（剛体近似の長さ．秒）または 'L3'（ピッキング1回を単位とする，簡略化した長さ）．
        weights: L3 の区間の重み（'shuttle'・'lift'・'loop'・'pick'）．省略時は DEFAULT_WEIGHTS．
    """
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, online: OnlineGraph, ports: bool = True, length: str = 'L0', weights: dict[str, int] | None = None) -> None:
        assert length in ('L0', 'L3'), f'Unknown length: {length}'
        self.online = online
        self.length = length
        self.weights = DEFAULT_WEIGHTS if weights is None else weights
        # L3 の単位（ピッキング時間の代表値 ＋ 搬入 ＋ 搬出）
        self.unit = online.expected_pick_rest(0) +2 *online.env.MT_CONV
        self.ports = ports
        self.blocks = []  # 全ブロック
        self.links = []  # 同じTJobのブロックの間のつながり
        self.block_of = {}  # 作業のキー -> ブロック（直列縮約した作業は含まない）
        for tjob, chain in online.chains.items():
            self.split(tjob, chain)
        self.anchor = {}  # 作業のキー -> (ノード, ノードの時刻から作業の開始までの時間)
        self.nodes = []  # ノードのキー
        self.port_cuts = {}  # 作業ブロック -> {ポート: opsの中での位置}（残っているポートだけ）
        self.set_anchors()
        self.terms = []
        self.add_start_terms()
        self.add_chain_terms()
        self.add_resource_terms()
        if ports:
            self.add_port_terms()
            self.add_buffer_terms()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def split(self, tjob: TJob, chain: list[Operation]) -> None:  # 1つのTJobの鎖を分割する
        retrying = any(op.round == 1 for op in chain)  # 再搬送中なら，周0が再帰還
        current, series = None, []
        for op in chain:
            if op.no in SERIES:
                series.append(op)
                continue
            if op.no <= 12:
                kind = 'arrival'
            elif op.no <= 22:
                kind = 'work'
            else:
                kind = 'rereturn' if retrying and op.round == 0 else 'return'
            if current is None or current.kind != kind:
                block = Block(tjob, kind)
                self.blocks.append(block)
                self.links.append(Link(current, block, series))
                current, series = block, []
            assert not series, f'Series operation inside a block: {op.key}'
            current.ops.append(op)
            self.block_of[op.key] = current
        assert not series, f'Chain ends with a series operation: {tjob}'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def set_anchors(self) -> None:  # 剛体近似：ブロックの作業を，先頭の作業からの間隔で固定する
        for block in self.blocks:
            if self.ports and block.kind == 'work':
                self.set_port_anchors(block)
                continue
            self.nodes.append(block.key)
            offset = 0.0
            for op in block.ops:
                self.anchor[op.key] = (block.key, offset)
                offset += op.duration
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def set_port_anchors(self, block: Block) -> None:  # 作業ブロックのポート（過ぎたポートは除く）
        no_q = 19 if block.op(19) is not None or block.ops[-1].no > 19 else 18  # 空になるなら，Qは18の終了
        cuts = {}
        # Y・P は作業の開始，Q・O は作業の終了．その作業が終わっていれば，過ぎたポートである
        for port, no, at_end in (('Y', 15, False), ('P', 17, False), ('Q', no_q, True), ('O', 22, True)):
            op = block.op(no)
            if op is None:
                continue
            cuts[port] = block.ops.index(op) +at_end
            self.nodes.append(block.port_key(port))
            self.anchor[op.key] = (block.port_key(port), -op.duration if at_end else 0.0)
        self.port_cuts[block] = cuts
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_start_terms(self) -> None:  # 開始エッジ（長さ0のものと，復帰中の機械からのもの）
        for node in self.nodes:
            self.terms.append(Term(START, node, 'start', None, 0.0))
        if self.length == 'L3':  # 残りの復帰時間は0に丸め，長さ0のエッジと重なるので張らない
            return
        for _, v, data in self.online.graph.out_edges(START, data=True):
            if data['resource'] is not None:
                node, offset = self.anchor[v]
                self.terms.append(Term(START, node, 'start', data['resource'], data['lag'] -offset))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_chain_terms(self) -> None:  # 同じTJobのブロックの間（先頭で直列縮約した作業は s から）
        for link in self.links:
            if link.src is None and not link.series:
                continue
            dst, _ = self.anchor[link.dst.ops[0].key]  # ブロックの先頭の作業なので，間隔は0
            if link.src is None:
                self.terms.append(Term(START, dst, 'chain', None, self.total(link.series)))
                continue
            if self.ports and link.src.kind == 'work':  # 最後のポートO（作業22の終了）から
                src, inside = link.src.port_key('O'), 0
            else:
                src, inside = link.src.key, self.total(link.src.ops)
            self.terms.append(Term(src, dst, 'chain', None, inside +self.total(link.series)))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_resource_terms(self) -> None:  # 資源から来るエッジ
        nodes = self.online.graph.nodes
        for u, v, data in self.online.graph.edges(data=True):
            resource = data['resource']
            if data['kind'] in ('process', 'start'):
                continue
            if self.ports and resource[:2] == ('conv', 2):
                continue  # conv2 のセルの項は，容量・順序の項に置き換える
            op_u, op_v = nodes[u]['op'], nodes[v]['op']
            (src, src_offset), (dst, dst_offset) = self.anchor[u], self.anchor[v]
            if self.length == 'L0':
                value = src_offset +op_u.duration +data['lag'] -dst_offset
            else:
                value = self.leave_weight(op_u, resource) -self.entry_weight(op_v, resource)
            self.terms.append(Term(src, dst, edge_kind(op_u, op_v, data['kind'], resource), resource, value))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def total(self, ops: list[Operation]) -> float:  # 作業の列の長さの和（L3では，残っている区間の重みの和）
        if self.length == 'L0':
            return sum(op.duration for op in ops)
        segments = {segment_of(op) for op in ops}
        pickup = sum(round(op.duration /self.unit) for op in ops if op.no in (11, 21))  # 割付け済みの台車の迎車
        return pickup +sum(self.weight(segment, ops) for segment in segments)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def weight(self, segment: int, ops: list[Operation]) -> int:  # 区間の重み
        name = SEGMENTS[segment][0]
        if name == 'loop_out' or name == 'loop_in':  # 迎車・荷積みを除く，ループ搬送（または周回）の重み
            return self.weights['loop'] *any(op.no in SERIES for op in ops if segment_of(op) == segment)
        return self.weights.get(name.split('_')[0], 0)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def resource_segment(self, op: Operation, resource: tuple) -> int:  # 資源を使う区間
        if resource[0] in ('order', 'station'):
            return SEGMENT_INDEX['conv2_out' if resource[0] == 'order' else 'pick']
        if resource[0] in ('shuttle', 'bucket'):
            return SEGMENT_INDEX['shuttle_out' if op.no <= 4 else 'shuttle_in']
        direction = resource[2] if resource[0] == 'lift' else resource[4]
        name = 'lift' if resource[0] == 'lift' else f'conv{resource[1]}'
        return SEGMENT_INDEX[name +('_out' if direction == FW else '_in')]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def entry_weight(self, op: Operation, resource: tuple) -> float:  # L3 の入口オフセット
        block = self.block_of[op.key]
        if self.ports and block.kind == 'work':
            return 0  # ポートから測る
        segment = self.resource_segment(op, resource)
        return self.total([other for other in block.ops if segment_of(other) < segment])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def leave_weight(self, op: Operation, resource: tuple) -> float:  # L3 の出口オフセット
        block = self.block_of[op.key]
        if self.ports and block.kind == 'work':
            return 0  # ポートから測る
        return self.entry_weight(op, resource) +self.weight(self.resource_segment(op, resource), block.ops)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_port_terms(self) -> None:  # 作業ブロックの中のポートの間（過ぎたポートからは s から）
        for block, cuts in self.port_cuts.items():
            src, start = START, 0
            for port in PORTS:
                if port not in cuts:
                    continue
                if port == 'Y':  # 荷卸しの開始へは，到着ブロックまたは s からのつながりがある
                    src, start = block.port_key(port), cuts[port]
                    continue
                length = self.total(block.ops[start:cuts[port]])
                self.terms.append(Term(src, block.port_key(port), 'chain', None, length))
                src, start = block.port_key(port), cuts[port]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_buffer_terms(self) -> None:  # conv2 の容量・順序の項
        capacity = self.online.env.CONV_LEN[2]
        works = {block.tjob: block for block in self.port_cuts}
        for picker, order in enumerate(self.online.station_order):
            confirmed = [works[tjob] for tjob in order]
            others = [block for block in works.values()
                if block.tjob.pjob.station.picker == picker and block not in confirmed]
            # 往路の容量：conv2^FW に空きができる（C2台前の搬入の終了）
            self.add_lagged_terms(('capacity', picker, FW), confirmed, others, capacity, 'P', 'Y',
                lambda block: block.op(17).duration if self.length == 'L0' else 0)
            # 復路の容量・順序：空になるバケットは conv2^BW を使わないので，列に数えない
            confirmed = [block for block in confirmed if not self.ends_at_station(block)]
            others = [block for block in others if not self.ends_at_station(block)]
            self.add_lagged_terms(('capacity', picker, BW), confirmed, others, capacity, 'O', 'Q', lambda block: 0.0)
            self.add_lagged_terms(('order', picker, BW), confirmed, others, 1, 'O', 'O',
                lambda block: self.online.env.LT_LOOP if self.length == 'L0' else 0)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def ends_at_station(self, block: Block) -> bool:  # ピッキングでバケットが空になるか？
        return block.ops[-1].no == 18
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_lagged_terms(self, resource: tuple, confirmed: list[Block], others: list[Block], lag: int,
            src_port: str, dst_port: str, value: Callable[[Block], float]) -> None:
        # 列の中で lag 台前のTJobから張る．順序が未確定のものへは，次に列に入るとみなして張る
        pairs = list(zip(confirmed, confirmed[lag:]))
        if len(confirmed) >= lag:
            pairs += [(confirmed[-lag], block) for block in others]
        for src, dst in pairs:
            if src_port not in self.port_cuts[src] or dst_port not in self.port_cuts[dst]:
                continue  # 過ぎたポート
            head = dst.ops[0]
            if head.running and (dst_port, head.no) in (('Y', 15), ('Q', 19), ('O', 22)):
                continue  # すでに使い始めている
            self.terms.append(Term(src.port_key(src_port), dst.port_key(dst_port), 'station', resource, value(src)))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def graph(self) -> nx.MultiDiGraph:  # 項をエッジとするグラフ
        graph = nx.MultiDiGraph()
        graph.add_node(START)
        for term in self.terms:
            graph.add_edge(term.src, term.dst, kind=term.kind, resource=term.resource, value=term.value)
        return graph
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def times(self) -> dict[tuple, float]:  # 最長経路による各ノードの時刻（開始ノードの時刻は，L0では t，L3では0）
        graph = self.graph()
        times = {START: self.online.now if self.length == 'L0' else 0}
        for node in nx.topological_sort(graph):  # 閉路があれば例外
            if node != START:
                times[node] = max(times[u] +data['value'] for u, _, data in graph.in_edges(node, data=True))
        return times
