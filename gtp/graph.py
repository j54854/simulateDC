from __future__ import annotations
from typing import NamedTuple
import networkx as nx
from .models import Direction, Progress, GTPSystem, TJob, Cell

# エッジ長のパラメータの既定値（α: 台車の入口側の長さ，β: バケット関連の長さ）
DEFAULT_ALPHA = {'loop': 1.0, 'lift': 1.0, 'shuttle': 1.0}
DEFAULT_BETA = 1.0

START = ('start',)  # スタートノードのキー

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Cost(NamedTuple):  # エッジ長の内訳
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    n_unit: int = 0  # 長さ1の境界の個数
    alphas: tuple[str, ...] = ()  # 越えた台車の入口側の種類
    n_beta: int = 0  # βの個数
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def plus(self, other: Cost) -> Cost:  # 内訳の加算
        return Cost(self.n_unit +other.n_unit, self.alphas +other.alphas, self.n_beta +other.n_beta)

UNIT = Cost(n_unit=1)
BETA = Cost(n_beta=1)
ALPHA_LOOP = Cost(alphas=('loop',))
ALPHA_LIFT = Cost(alphas=('lift',))
ALPHA_SHUTTLE = Cost(alphas=('shuttle',))

# ループより上流の各位置について，conv1 の位置からの境界の累積（direct エッジ用）
UPSTREAM_COST = {
    'conv1': Cost(),
    'lift': UNIT,
    'conv0': UNIT.plus(ALPHA_LIFT),
    'shuttle': UNIT.plus(ALPHA_LIFT).plus(UNIT),
    'store': UNIT.plus(ALPHA_LIFT).plus(UNIT).plus(ALPHA_SHUTTLE),
    'beyond_store': UNIT.plus(ALPHA_LIFT).plus(UNIT).plus(ALPHA_SHUTTLE).plus(BETA),
    }

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def tjob_key(tjob: TJob) -> tuple[str, int, int]:  # TJobノードのキー
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    return ('tjob', tjob.pjob.idx, tjob.idx)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def edge_length(data: dict, alpha: dict[str, float] | None = None, beta: float | None = None) -> float:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    """エッジ属性の内訳からエッジ長を計算する．

    Args:
        data: エッジ属性の辞書（n_unit・alphas・n_beta を含む）．
        alpha: 台車の種類（'loop'・'lift'・'shuttle'）ごとの α．省略時は DEFAULT_ALPHA．
        beta: β．省略時は DEFAULT_BETA．

    Returns:
        n_unit + Σ alpha[alphas の各要素] + n_beta * beta．
    """
    alpha = DEFAULT_ALPHA if alpha is None else alpha
    beta = DEFAULT_BETA if beta is None else beta
    return data['n_unit'] +sum(alpha[kind] for kind in data['alphas']) +data['n_beta'] *beta

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class TJobGraph:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    """搬送ジョブをノードとし，ピッキング作業を行える順序を表す有向グラフ．

    仕様は docs/tjob_graph.md を参照．シミュレーションの状態を読むだけで書き換えず，
    random も呼ばない（共有乱数を消費するとシミュレーション結果が変わるため）．
    """
    SCOPES = ('unpicked',)  # 対応している対象範囲
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, gtps: GTPSystem, scope: str = 'unpicked') -> None:
        if scope not in self.SCOPES:
            raise NotImplementedError(f'Scope {scope!r} is not supported yet')
        self.gtps = gtps
        self.env = gtps.env
        self.scope = scope
        self.graph = nx.MultiDiGraph()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def update(self) -> nx.MultiDiGraph:
        """現時点の状態からグラフを構築し直して返す（第1段階として毎回スクラッチから構築）．

        Returns:
            ノード・エッジに属性を持つ MultiDiGraph．エッジのキーは種類（kind）．
        """
        self.members = list(self.gtps.unpicked_tjobs)  # 対象TJobのリスト（順序を固定するためリストで保持）
        self.member_set = set(self.members)
        self.graph = nx.MultiDiGraph()
        self.scan()
        self.add_nodes()
        self.add_station_side_edges()
        self.add_upstream_edges()
        self.add_direct_edges()
        self.add_precedence_edges()
        self.add_bucket_edges()
        return self.graph
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def member_on(self, cell: Cell) -> TJob | None:  # セル上のバケットの現在ジョブ（対象外ならNone）
        if cell.bucket is None:
            return None
        tjob = cell.bucket.current_tjob()
        return tjob if tjob in self.member_set else None
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def members_on(self, conveyor, progress: Progress) -> list[TJob]:  # コンベヤ上の対象TJob（下流から順に）
        tjobs = [self.member_on(cell) for cell in reversed(conveyor.cells)]
        tjobs = [tjob for tjob in tjobs if tjob is not None]
        for tjob in tjobs:
            assert tjob.progress == progress, f'Unexpected progress on conveyor: {tjob}'
        return tjobs
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def scan(self) -> None:  # 設備側から走査して，対象TJobの位置を分類する
        env, gtps = self.env, self.gtps
        self.position = {}  # TJob -> 位置
        # 作業場側（作業場 p ごと）
        self.station_tjob = [None] *env.PICKER  # 作業中のTJob（アイドルならNone）
        self.conv2 = [[] for picker in range(env.PICKER)]
        self.cars = [[] for picker in range(env.PICKER)]  # 作業場 p 行きのTJobを積んだ台車
        for picker in range(env.PICKER):
            tjob = self.member_on(gtps.stations[picker].cells[0])
            if tjob is not None:
                assert tjob.progress == Progress.TO_PICKED, f'Unexpected progress at station: {tjob}'
                self.station_tjob[picker] = tjob
                self.position[tjob] = 'station'
            self.conv2[picker] = self.members_on(gtps.conveyors[2][picker][0][Direction.FORWARD], Progress.TO_PICKED)
            for tjob in self.conv2[picker]:
                self.position[tjob] = 'conv2'
        for car in gtps.loop.cells:
            tjob = self.member_on(car)
            if tjob is not None and tjob.progress == Progress.ON_FW_LOOP:  # 再搬送で戻るものは置場の向こう側
                self.cars[tjob.pjob.station.picker].append(tjob)
                self.position[tjob] = 'car'
        # 上流側（系列 a ごと，階層 f ごと）
        self.conv1 = [[] for aisle in range(env.AISLE)]
        self.lift = [None] *env.AISLE
        self.conv0 = [[[] for floor in range(env.FLOOR)] for aisle in range(env.AISLE)]
        self.shuttle = [[None] *env.FLOOR for aisle in range(env.AISLE)]
        self.store = [[[] for floor in range(env.FLOOR)] for aisle in range(env.AISLE)]
        self.beyond_store = [[[] for floor in range(env.FLOOR)] for aisle in range(env.AISLE)]
        self.off_route = []
        for aisle in range(env.AISLE):
            self.conv1[aisle] = self.members_on(gtps.conveyors[1][aisle][0][Direction.FORWARD], Progress.TO_FW_LOOP)
            for tjob in self.conv1[aisle]:
                self.position[tjob] = 'conv1'
            tjob = self.member_on(gtps.lifts[aisle][Direction.FORWARD].cells[0])
            if tjob is not None:
                assert tjob.progress == Progress.TO_FW_LIFT, f'Unexpected progress on lift: {tjob}'
                self.lift[aisle] = tjob
                self.position[tjob] = 'lift'
            for floor in range(env.FLOOR):
                self.conv0[aisle][floor] = self.members_on(gtps.conveyors[0][aisle][floor][Direction.FORWARD], Progress.TO_FW_LIFT)
                for tjob in self.conv0[aisle][floor]:
                    self.position[tjob] = 'conv0'
                tjob = self.member_on(gtps.shuttles[aisle][floor].cells[0])
                if tjob is not None and tjob.progress == Progress.TO_FW_SHTL:  # 入庫中のものは置場の向こう側
                    self.shuttle[aisle][floor] = tjob
                    self.position[tjob] = 'shuttle'
        # 設備上にない対象TJob
        for tjob in self.members:
            if tjob in self.position:
                continue
            store = tjob.bucket.home_store
            if tjob.retry_needed:  # 再搬送のため入庫経路を戻っている
                self.beyond_store[store.aisle][store.floor].append(tjob)
                self.position[tjob] = 'beyond_store'
            elif tjob is not tjob.bucket.current_tjob():  # バケットの順番待ち
                self.off_route.append(tjob)
                self.position[tjob] = 'off_route'
            else:  # 置場で取り出しを待っている
                assert tjob.progress == Progress.TO_FW_SHTL and tjob.bucket.is_at_home(), f'Unexpected tjob state: {tjob}'
                self.store[store.aisle][store.floor].append(tjob)
                self.position[tjob] = 'store'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_nodes(self) -> None:
        self.graph.add_node(START, node_type='start')
        self.station_node = []  # 作業場ノードのキー（作業中ならTJobのキー）
        for picker in range(self.env.PICKER):
            if self.station_tjob[picker] is None:
                key = ('station', picker)
                self.graph.add_node(key, node_type='station', picker=picker)
            else:
                key = tjob_key(self.station_tjob[picker])
            self.station_node.append(key)
        for tjob in self.members:
            store = tjob.bucket.home_store
            self.graph.add_node(
                tjob_key(tjob), node_type='tjob', position=self.position[tjob],
                picker=tjob.pjob.station.picker, pjob=tjob.pjob.idx, progress=int(tjob.progress),
                aisle=store.aisle, floor=store.floor, retry_needed=tjob.retry_needed, tjob=tjob)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_edge(self, u: tuple, v: tuple, kind: str, cost: Cost) -> None:
        self.graph.add_edge(u, v, key=kind, kind=kind, n_unit=cost.n_unit, alphas=cost.alphas, n_beta=cost.n_beta)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def visit(self, frontier: list[tuple[tuple, Cost]], tjob: TJob) -> list[tuple[tuple, Cost]]:
        # フロンティアの各要素から当該TJobにchainエッジを張り，フロンティアを当該TJobで置き換える
        for u, cost in frontier:
            self.add_edge(u, tjob_key(tjob), 'chain', cost)
        return [(tjob_key(tjob), Cost())]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def visit_conveyor(self, frontier: list[tuple[tuple, Cost]], tjobs: list[TJob]) -> list[tuple[tuple, Cost]]:
        # コンベヤ上のTJobを下流から順にたどる（隣接は長さ1，空のコンベヤは何も加えない）
        for i, tjob in enumerate(tjobs):
            if i > 0:
                frontier = advance(frontier, UNIT)
            frontier = self.visit(frontier, tjob)
        return frontier
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_station_side_edges(self) -> None:  # start エッジと作業場側の chain エッジ
        for picker in range(self.env.PICKER):
            self.add_edge(START, self.station_node[picker], 'start', Cost())
            frontier = [(self.station_node[picker], Cost())]
            for tjob in self.conv2[picker]:  # 作業場ノードを conv2 列の最下流要素とみなす
                frontier = self.visit(advance(frontier, UNIT), tjob)
            for tjob in self.cars[picker]:  # 台車へは並列（台車の出口側は長さ1）
                self.add_edge(frontier[0][0], tjob_key(tjob), 'chain', frontier[0][1].plus(UNIT))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_upstream_edges(self) -> None:  # ループより上流の chain エッジ
        for aisle in range(self.env.AISLE):
            frontier = []  # 上流側の鎖は direct エッジでのみ下流とつながる
            frontier = self.visit_conveyor(frontier, self.conv1[aisle])
            frontier = advance(frontier, UNIT)  # conv1 -> リフト（出口側）
            if self.lift[aisle] is not None:
                frontier = self.visit(frontier, self.lift[aisle])
            frontier = advance(frontier, ALPHA_LIFT)  # リフト -> conv0（入口側）
            for floor in range(self.env.FLOOR):  # 階層ごとに分岐
                floor_frontier = self.visit_conveyor(list(frontier), self.conv0[aisle][floor])
                floor_frontier = advance(floor_frontier, UNIT)  # conv0 -> シャトル（出口側）
                if self.shuttle[aisle][floor] is not None:
                    floor_frontier = self.visit(floor_frontier, self.shuttle[aisle][floor])
                floor_frontier = advance(floor_frontier, ALPHA_SHUTTLE)  # シャトル -> 置場（入口側）
                for u, cost in floor_frontier:  # 置場待ちと置場の向こう側は並列（フロンティアは更新しない）
                    for tjob in self.store[aisle][floor]:
                        self.add_edge(u, tjob_key(tjob), 'chain', cost)
                    for tjob in self.beyond_store[aisle][floor]:
                        self.add_edge(u, tjob_key(tjob), 'chain', cost.plus(BETA))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_direct_edges(self) -> None:  # 台車からループより上流の同じ作業場行きの全TJobへ
        upstream = [tjob for tjob in self.members if self.position[tjob] in UPSTREAM_COST]
        for picker in range(self.env.PICKER):
            if len(self.cars[picker]) > 0:
                sources = [(tjob_key(tjob), ALPHA_LOOP) for tjob in self.cars[picker]]
            else:  # 台車がなければ conv2 列の最上流から（空の台車を飛ばすので出口側の1を加える）
                tail = tjob_key(self.conv2[picker][-1]) if self.conv2[picker] else self.station_node[picker]
                sources = [(tail, UNIT.plus(ALPHA_LOOP))]
            for tjob in upstream:
                if tjob.pjob.station.picker != picker:
                    continue
                for u, cost in sources:
                    self.add_edge(u, tjob_key(tjob), 'direct', cost.plus(UPSTREAM_COST[self.position[tjob]]))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_precedence_edges(self) -> None:  # 同じ作業場の隣り合うPJobのTJob間
        for picker in range(self.env.PICKER):
            groups = {}  # PJobのインデックス -> TJobのリスト
            for tjob in self.members:
                if tjob.pjob.station.picker == picker:
                    groups.setdefault(tjob.pjob.idx, []).append(tjob)
            indices = sorted(groups)
            for prev_idx, next_idx in zip(indices, indices[1:]):
                for u in groups[prev_idx]:
                    for v in groups[next_idx]:
                        self.add_edge(tjob_key(u), tjob_key(v), 'precedence', UNIT)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_bucket_edges(self) -> None:  # 同じバケットで割付け順に隣り合うTJob間
        buckets = list(dict.fromkeys(tjob.bucket for tjob in self.members))  # 重複を除き順序を保つ
        for bucket in buckets:
            tjobs = [tjob for tjob in bucket.undone_tjobs() if tjob in self.member_set]
            for u, v in zip(tjobs, tjobs[1:]):
                self.add_edge(tjob_key(u), tjob_key(v), 'bucket', BETA)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def advance(frontier: list[tuple[tuple, Cost]], cost: Cost) -> list[tuple[tuple, Cost]]:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # フロンティアの各要素に境界の長さを加算する
    return [(u, acc.plus(cost)) for u, acc in frontier]
