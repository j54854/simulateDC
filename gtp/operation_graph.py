from __future__ import annotations
from dataclasses import dataclass
import networkx as nx
from .models import Direction, Progress, GTPSystem, TJob, Cell, Conveyor, Loop, Station, Store

START = ('start',)  # 開始ノードのキー
TOL = 1e-6  # 時刻の比較の許容誤差
FW, BW = Direction.FORWARD, Direction.BACKWARD

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
@dataclass(eq=False)
class Operation:  # 作業（オペレーショングラフのノード）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    tjob: TJob
    round: int  # 訪問の周（再搬送中は，置場へ戻るまでが0，やり直す往路以降が1．それ以外は常に0）
    no: int  # 作業番号（docs/operation_graph.md の表）
    seg: int  # セル間移動（5・10・16・20・26・30）の送り出すセル番号．それ以外は0
    duration: float  # 処理時間の名目値（実行中なら残り時間）
    resources: tuple[tuple, ...] = ()  # 使う機械（台車を除く）
    running: bool = False  # 実行中か
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    @property
    def key(self) -> tuple:  # ノードのキー
        return ('op', self.tjob.pjob.idx, self.tjob.idx, self.round, self.no, self.seg)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def visit(self, resource: tuple) -> tuple:  # 機械の訪問の識別子
        if resource[0] == 'shuttle':  # シャトルだけは，1つの周の中で往路と復路の2回訪れる
            return (self.tjob, self.round, FW if self.no < 20 else BW)
        return (self.tjob, self.round)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class OnlineGraph:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    """時刻 t の状態から作るオンライングラフ（参照実装．速さは問わない）．

    定義は docs/operation_graph.md を参照．シミュレーションの状態を読むだけで書き換えず，
    random も呼ばない．

    Args:
        gtps: 対象のGTPシステム．
        pick_time: 未着手のピッキング時間の名目値．省略時は平均．
        known_pick: 実行中のピッキングの残り時間に，実現値を使うか（検証用）．実用時には終了時刻が
            わからないので，既定では経過時間を条件とする期待値を使う．
    """
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, gtps: GTPSystem, pick_time: float | None = None, known_pick: bool = False) -> None:
        self.gtps = gtps
        self.env = gtps.env
        self.now = self.env.now
        env = self.env
        # 接続口での荷卸しと発進を，処理時間の長さで区別するため
        assert abs(env.LT_LOOP -env.MT_LOOP) > TOL, 'LT_LOOP and MT_LOOP must differ'
        self.pick_time = self.expected_pick_rest(0) if pick_time is None else pick_time
        self.known_pick = known_pick
        self.car_of = {car.assigned_tjob: car for car in gtps.loop.cells if car.assigned_tjob is not None}
        self.chains = {tjob: self.remaining_chain(tjob) for tjob in gtps.unrestored_tjobs}  # TJob -> 残りの作業
        self.graph = nx.MultiDiGraph()
        self.graph.add_node(START, op=None)
        self.add_process_edges()
        self.visits = self.collect_visits()  # 資源 -> {訪問: 作業のリスト}
        self.add_shuttle_edges()
        self.add_forward_conv0_edges()
        self.add_forward_lift_edges()
        self.add_forward_conv1_edges()
        self.station_order = []  # 作業場ごとの，確定した作業場の順（conv2^BW 上 → 作業場 → conv2^FW 上 → 荷卸し中）
        self.add_station_edges()
        self.add_backward_edges()
        self.add_retry_edges()
        self.add_precedence_edges()
        self.add_bucket_edges()
        self.add_start_edges()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_process_edges(self) -> None:  # 作業のノードと工程エッジ
        for chain in self.chains.values():
            for op in chain:
                self.graph.add_node(op.key, op=op)
            for u, v in zip(chain, chain[1:]):
                self.add_edge(u, v, 'process')
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_edge(self, u: Operation, v: Operation, kind: str, resource: tuple | None = None, lag: float = 0, fan: bool = False) -> None:
        # エッジの長さは，始点の処理時間 ＋ lag（復帰時間）
        self.graph.add_edge(u.key, v.key, key=(kind, resource), kind=kind, resource=resource, lag=lag, length=u.duration +lag, fan=fan)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def collect_visits(self) -> dict[tuple, dict[tuple, list[Operation]]]:  # 資源ごとの，これから使う訪問
        visits = {}
        for chain in self.chains.values():
            for op in chain:
                for resource in op.resources:
                    visits.setdefault(resource, {}).setdefault(op.visit(resource), []).append(op)
        return visits
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def return_time(self, op: Operation) -> float:  # 作業の後の復帰時間
        store = op.tjob.bucket.home_store
        if op.no == 29:  # 復路リフト
            return store.floor *self.env.MT_LIFT
        if op.no == 33:  # 復路シャトル
            return (self.env.ROW -store.row) *self.env.MT_SHTL
        return 0
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_machine_edges(self, resource: tuple, confirmed: list[tuple]) -> None:
        # 機械順序エッジ：確定した訪問の鎖と，その末尾から未確定の訪問への扇
        visits = self.visits.get(resource, {})
        chain = [visit for visit in confirmed if visit in visits]  # この機械を使う作業が残っている訪問
        for u, v in zip(chain, chain[1:]):
            last = visits[u][-1]
            self.add_edge(last, visits[v][0], 'machine', resource, self.return_time(last))
        if not chain:
            return
        last = visits[chain[-1]][-1]
        for visit, ops in visits.items():
            if visit not in chain and visit[0] is not last.tjob:  # 同じTJobの後の訪問へは，工程エッジから導かれる
                self.add_edge(last, ops[0], 'machine', resource, self.return_time(last), fan=True)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_returning_edges(self, resource: tuple, rest: float) -> None:  # 復帰中の機械を使うすべての訪問への開始エッジ
        for ops in self.visits.get(resource, {}).values():
            self.graph.add_edge(START, ops[0].key, key=('start', resource), kind='start', resource=resource, lag=rest, length=rest, fan=False)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_shuttle_edges(self) -> None:
        for shuttle in (shuttle for row in self.gtps.shuttles for shuttle in row):
            resource = ('shuttle', shuttle.aisle, shuttle.floor)
            car = shuttle.cells[0]
            tjob = car.assigned_tjob
            if tjob is None:
                continue
            if tjob.progress == Progress.TO_FW_SHTL:
                self.add_machine_edges(resource, [(tjob, 0, FW)])
            elif tjob.bucket.is_at_home():  # 格納後の復帰中
                self.add_returning_edges(resource, self.move_rest(car, self.env.ROW -car.node.idx, self.env.MT_SHTL))
            else:
                self.add_machine_edges(resource, [(tjob, 0, BW)])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def tjobs_on(self, conveyor: Conveyor) -> list[TJob]:  # コンベヤ上のTJob（下流から順に）
        return [cell.bucket.current_tjob() for cell in reversed(conveyor.cells) if cell.bucket is not None]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_forward_conv0_edges(self) -> None:
        for aisle in range(self.env.AISLE):
            for floor in range(self.env.FLOOR):
                tjobs = self.tjobs_on(self.gtps.conveyors[0][aisle][floor][FW])
                tjob = self.gtps.shuttles[aisle][floor].cells[0].assigned_tjob
                if tjob is not None and tjob.progress == Progress.TO_FW_SHTL:  # 出庫の割付け済み
                    tjobs.append(tjob)
                for resource in self.conv_cells(0, aisle, floor, FW):
                    self.add_machine_edges(resource, [(tjob, 0) for tjob in tjobs])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_forward_lift_edges(self) -> None:
        for aisle in range(self.env.AISLE):
            tjob = self.gtps.lifts[aisle][FW].cells[0].assigned_tjob
            if tjob is not None:
                self.add_machine_edges(('lift', aisle, FW), [(tjob, 0)])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_forward_conv1_edges(self) -> None:
        for aisle in range(self.env.AISLE):
            tjobs = self.tjobs_on(self.gtps.conveyors[1][aisle][0][FW])
            tjob = self.gtps.lifts[aisle][FW].cells[0].assigned_tjob
            if tjob is not None:
                tjobs.append(tjob)
            for resource in self.conv_cells(1, aisle, 0, FW):
                self.add_machine_edges(resource, [(tjob, 0) for tjob in tjobs])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_dropping(self, tjob: TJob) -> bool:  # ループから荷卸し中か？（コンベヤに入る順が確定している）
        chain = self.chains[tjob]
        return bool(chain) and chain[0].no in (15, 25) and chain[0].running
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_station_edges(self) -> None:  # conv2^FW・作業場・conv2^BW（作業場に入る順を引き継ぐ）
        for picker in range(self.env.PICKER):
            forward, backward = self.gtps.conveyors[2][picker][0]
            tjobs = self.tjobs_on(backward)
            if not self.gtps.stations[picker].cells[0].is_open():
                tjobs.append(self.gtps.stations[picker].cells[0].bucket.current_tjob())
            tjobs += self.tjobs_on(forward)
            tjobs += [tjob for tjob in self.car_of if tjob.progress == Progress.ON_FW_LOOP
                and tjob.pjob.station.picker == picker and self.is_dropping(tjob)]
            self.station_order.append(tjobs)
            confirmed = [(tjob, 0) for tjob in tjobs]
            for resource in (*self.conv_cells(2, picker, 0, FW), ('station', picker), *self.conv_cells(2, picker, 0, BW)):
                self.add_machine_edges(resource, confirmed)  # 空になるバケットは，conv2^BW を使わないので除かれる
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_backward_edges(self) -> None:  # conv1^BW・復路リフト・conv0^BW（conv1^BW に入る順を引き継ぐ）
        for aisle in range(self.env.AISLE):
            lift = self.gtps.lifts[aisle][BW].cells[0]
            tjobs = []
            # 階の違うTJobは，この先共有する機械がないので，このリストの中での並びはエッジに影響しない
            for floor in range(self.env.FLOOR):
                tjobs += self.tjobs_on(self.gtps.conveyors[0][aisle][floor][BW])
            if not lift.is_open():
                tjobs.append(lift.bucket.current_tjob())
            tjobs += self.tjobs_on(self.gtps.conveyors[1][aisle][0][BW])
            tjobs += [tjob for tjob in self.car_of if tjob.progress == Progress.ON_BW_LOOP
                and tjob.bucket.home_store.aisle == aisle and self.is_dropping(tjob)]
            confirmed = [(tjob, 0) for tjob in tjobs]
            resources = [*self.conv_cells(1, aisle, 0, BW), ('lift', aisle, BW)]
            for floor in range(self.env.FLOOR):
                resources += self.conv_cells(0, aisle, floor, BW)
            for resource in resources:
                self.add_machine_edges(resource, confirmed)
            tjob = lift.assigned_tjob
            if tjob is not None and tjob.progress != Progress.TO_BW_LIFT:  # 荷卸し後の復帰中
                self.add_returning_edges(('lift', aisle, BW), self.move_rest(lift, lift.node.idx, self.env.MT_LIFT))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_retry_edges(self) -> None:  # 再搬送で置場へ戻る入庫 → やり直しの出庫（同じシャトルの2回の訪問）
        for tjob, chain in self.chains.items():
            for u, v in zip(chain, chain[1:]):
                if u.no == 33 and v.no == 1:  # 復帰時間を含めるので，工程エッジより長い
                    store = tjob.bucket.home_store
                    self.add_edge(u, v, 'machine', ('shuttle', store.aisle, store.floor), self.return_time(u))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_precedence_edges(self) -> None:  # 作業場順序（同じ作業場の隣り合うPJobの荷卸しの間）
        for picker in range(self.env.PICKER):
            groups = {}  # PJobのインデックス -> 荷卸し（15）のリスト
            for tjob, chain in self.chains.items():
                if tjob.pjob.station.picker == picker:
                    for op in chain:
                        if op.no == 15:
                            groups.setdefault(tjob.pjob.idx, []).append(op)
            indices = sorted(groups)
            for prev_idx, next_idx in zip(indices, indices[1:]):
                for u in groups[prev_idx]:
                    for v in groups[next_idx]:
                        self.add_edge(u, v, 'precedence', ('order', picker))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_bucket_edges(self) -> None:  # 同じバケットで割付け順に隣り合うTJobの入庫 → 出庫
        for bucket in dict.fromkeys(tjob.bucket for tjob in self.chains):  # 重複を除き順序を保つ
            tjobs = bucket.undone_tjobs()
            for prev_tjob, next_tjob in zip(tjobs, tjobs[1:]):
                if not self.chains[prev_tjob]:  # 格納済みなら，復帰中のシャトルの開始エッジが表す
                    continue
                last, first = self.chains[prev_tjob][-1], self.chains[next_tjob][0]
                assert (last.no, first.no) == (33, 1), f'Unexpected bucket order: {last.key}, {first.key}'
                self.add_edge(last, first, 'bucket', ('bucket', bucket.idx), self.return_time(last))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_start_edges(self) -> None:  # グラフ内に先行作業を持たない作業への開始エッジ
        for node in [node for node, degree in self.graph.in_degree() if degree == 0 and node != START]:
            self.graph.add_edge(START, node, key=('start', None), kind='start', resource=None, lag=0, length=0, fan=False)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def expected_pick_rest(self, elapsed: float) -> float:  # ピッキングの残り時間の期待値（経過時間が条件）
        pairs = [(t, p) for t, p in zip(self.env.T_PICK, self.env.P_PICK) if t > elapsed -TOL]
        total = sum(p for _, p in pairs)
        if total <= 0:
            return 0.0
        return max(0.0, sum(t *p for t, p in pairs) /total -elapsed)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def loop_time(self, src: int, dst: int) -> float:  # ループ上のノード間の移動時間
        return (dst -src) %self.env.LOOP *self.env.MT_LOOP
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def empties_bucket(self, tjob: TJob) -> bool:  # ピッキングでバケットが空になるか？
        return tjob is tjob.bucket.tjobs[-1] and tjob.bucket.get_balance() <= 0
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def conv_cells(self, stage: int, dim0: int, dim1: int, direction: Direction) -> list[tuple]:  # コンベヤのセルの資源
        return [('conv', stage, dim0, dim1, direction, c) for c in range(self.env.CONV_LEN[stage])]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def conv_rows(self, no: int, cells: list[tuple]) -> list[tuple]:  # セル間移動の作業の列
        return [(no, c, self.env.MT_CONV, (cells[c], cells[c +1])) for c in range(len(cells) -1)]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def forward_ops(self, tjob: TJob, round: int) -> list[Operation]:  # 作業 1〜19（空になるなら18まで）
        env = self.env
        store = tjob.bucket.home_store
        a, f, p = store.aisle, store.floor, tjob.pjob.station.picker
        shuttle, lift, station = ('shuttle', a, f), ('lift', a, FW), ('station', p)
        c0, c1, c2 = self.conv_cells(0, a, f, FW), self.conv_cells(1, a, 0, FW), self.conv_cells(2, p, 0, FW)
        run, rise = (env.ROW -store.row) *env.MT_SHTL, f *env.MT_LIFT
        rows = [
            (1, 0, run, (shuttle,)), (2, 0, env.LT_SHTL, (shuttle,)),
            (3, 0, run, (shuttle,)), (4, 0, env.LT_SHTL, (shuttle, c0[0])),
            *self.conv_rows(5, c0),
            (6, 0, rise, (lift,)), (7, 0, env.LT_LIFT, (lift, c0[-1])),
            (8, 0, rise, (lift,)), (9, 0, env.LT_LIFT, (lift, c1[0])),
            *self.conv_rows(10, c1),
            (11, 0, 0, ()), (12, 0, env.LT_LOOP, (c1[-1],)),  # 割付け前の迎車の名目値は0
            (13, 0, self.loop_time(env.AISLE_SEG[a][FW], env.PICKER_SEG[p][FW]), ()),
            (15, 0, env.LT_LOOP, (c2[0],)),
            *self.conv_rows(16, c2),
            (17, 0, env.MT_CONV, (station, c2[-1])), (18, 0, self.pick_time, (station,)),
            ]
        if not self.empties_bucket(tjob):
            rows.append((19, 0, env.MT_CONV, (station, self.conv_cells(2, p, 0, BW)[0])))
        return [Operation(tjob, round, *row) for row in rows]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def backward_ops(self, tjob: TJob, round: int) -> list[Operation]:  # 作業 20〜33
        env = self.env
        store = tjob.bucket.home_store
        a, f, p = store.aisle, store.floor, tjob.pjob.station.picker
        shuttle, lift = ('shuttle', a, f), ('lift', a, BW)
        c0, c1, c2 = self.conv_cells(0, a, f, BW), self.conv_cells(1, a, 0, BW), self.conv_cells(2, p, 0, BW)
        run, rise = (env.ROW -store.row) *env.MT_SHTL, f *env.MT_LIFT
        rows = [
            *self.conv_rows(20, c2),
            (21, 0, 0, ()), (22, 0, env.LT_LOOP, (c2[-1],)),  # 割付け前の迎車の名目値は0
            (23, 0, self.loop_time(env.PICKER_SEG[p][BW], env.AISLE_SEG[a][BW]), ()),
            (25, 0, env.LT_LOOP, (c1[0],)),
            *self.conv_rows(26, c1),
            (27, 0, env.LT_LIFT, (lift, c1[-1])), (28, 0, rise, (lift,)), (29, 0, env.LT_LIFT, (lift, c0[0])),
            *self.conv_rows(30, c0),
            (31, 0, env.LT_SHTL, (shuttle, c0[-1])), (32, 0, run, (shuttle,)), (33, 0, env.LT_SHTL, (shuttle,)),
            ]
        return [Operation(tjob, round, *row) for row in rows]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def retry_ops(self, tjob: TJob) -> list[Operation]:  # 再搬送で置場へ戻る作業 34 → 25〜33（周0）
        env = self.env
        aisle, picker = tjob.bucket.home_store.aisle, tjob.pjob.station.picker
        transport = Operation(tjob, 0, 34, 0, self.loop_time(env.PICKER_SEG[picker][FW], env.AISLE_SEG[aisle][BW]))
        return [transport] +[op for op in self.backward_ops(tjob, 0) if op.no >= 25]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def remaining_chain(self, tjob: TJob) -> list[Operation]:  # 残りの作業の鎖
        stored = tjob.progress == Progress.TO_BW_SHTL and tjob.bucket.is_at_home()  # 格納済みで，シャトルが復帰中
        if stored and not tjob.retry_needed:
            return []
        round = 1 if tjob.retry_needed else 0  # 再搬送中は，復路の残りの後に往路をやり直す
        chain = self.forward_ops(tjob, round)
        if not self.empties_bucket(tjob):
            chain += self.backward_ops(tjob, round)
        if stored:
            return chain
        if tjob.retry_needed:
            chain = self.retry_ops(tjob) +chain
        no, seg, rest = self.locate(tjob)
        if no == 14:  # 実行中の周回は，ループ搬送（13）のノードを置き換える
            idx = next(i for i, op in enumerate(chain) if op.no == 13)
            chain[idx].no = 14
        idx = next(i for i, op in enumerate(chain) if (op.no, op.seg) == (no, seg))
        chain = chain[idx:]
        if rest is not None:  # 実行中
            chain[0].duration = rest
            chain[0].running = True
        return chain
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def hold_rest(self, cell: Cell, tjob: TJob, duration: float | None = None) -> float | None:
        # 当該TJobの作業が実行中なら残り時間（実行中でなければNone）
        if cell.hold_end is None or cell.hold_end < self.now -TOL:
            return None
        # 直前の記録より前に始まった作業は，当該TJobの今の作業ではない（releaseは他の機械の復帰の終わりなので除く）
        since = max((stamp.time for stamp in tjob.history if stamp.kind != 'release'), default=0)
        if cell.hold_start < since -TOL:
            return None
        if duration is not None and abs(cell.hold_end -cell.hold_start -duration) > TOL:
            return None
        return max(0.0, cell.hold_end -self.now)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def move_rest(self, cell: Cell, steps: int, unit_time: float) -> float:  # 残りの移動時間（steps: 残りのノード数）
        rest = steps *unit_time
        moving = cell.hold_end is not None and cell.hold_end >= self.now -TOL
        if moving and steps > 0 and abs(cell.hold_end -cell.hold_start -unit_time) <= TOL:
            rest += max(0.0, cell.hold_end -self.now) -unit_time  # 次のノードへ移動中
        return rest
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def locate_at_loop_entrance(self, tjob: TJob, no: int) -> tuple[int, int, float | None]:  # 台車待ち（迎車no，荷積みno+1）
        car = self.car_of.get(tjob)
        if car is None:  # 未割付け
            return no, 0, None
        steps = (car.goal.idx -car.node.idx) %self.env.LOOP
        if steps > 0:
            return no, 0, self.move_rest(car, steps, self.env.MT_LOOP)
        rest = self.hold_rest(car, tjob, self.env.LT_LOOP)
        return (no, 0, 0.0) if rest is None else (no +1, 0, rest)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def locate(self, tjob: TJob) -> tuple[int, int, float | None]:
        """最初の未完了の作業を，シミュレータの状態から読み取る．

        Returns:
            (作業番号, セル番号, 残り時間)．残り時間は，未着手なら None．
        """
        env, gtps = self.env, self.gtps
        cell = tjob.bucket.cell
        unit = cell.unit
        store = tjob.bucket.home_store
        progress = tjob.progress
        if progress == Progress.TO_FW_SHTL:
            car = gtps.shuttles[store.aisle][store.floor].cells[0]
            if car.assigned_tjob is not tjob:  # 出庫待ち，またはバケットの順番待ち
                return 1, 0, None
            if isinstance(unit, Store):
                if car.node.idx > store.row:
                    return 1, 0, self.move_rest(car, car.node.idx -store.row, env.MT_SHTL)
                return 2, 0, self.hold_rest(car, tjob, env.LT_SHTL)
            if car.node.idx < env.ROW:
                return 3, 0, self.move_rest(car, env.ROW -car.node.idx, env.MT_SHTL)
            return 4, 0, self.hold_rest(car, tjob, env.LT_SHTL)
        if progress == Progress.TO_FW_LIFT:
            car = gtps.lifts[store.aisle][FW].cells[0]
            if isinstance(unit, Conveyor):
                if cell is not unit.cells[-1]:
                    return 5, cell.idx, self.hold_rest(cell, tjob)
                if car.assigned_tjob is not tjob:  # リフト待ち
                    return 6, 0, None
                if car.node.idx < store.floor:
                    return 6, 0, self.move_rest(car, store.floor -car.node.idx, env.MT_LIFT)
                return 7, 0, self.hold_rest(car, tjob, env.LT_LIFT)
            if car.node.idx > 0:
                return 8, 0, self.move_rest(car, car.node.idx, env.MT_LIFT)
            return 9, 0, self.hold_rest(car, tjob, env.LT_LIFT)
        if progress == Progress.TO_FW_LOOP:
            if cell is not unit.cells[-1]:
                return 10, cell.idx, self.hold_rest(cell, tjob)
            return self.locate_at_loop_entrance(tjob, 11)
        if progress in (Progress.ON_FW_LOOP, Progress.ON_BW_LOOP):
            assert isinstance(unit, Loop), f'Unexpected unit: {unit}'
            steps = (cell.goal.idx -cell.node.idx) %env.LOOP
            forward = progress == Progress.ON_FW_LOOP
            if steps > 0:
                if forward:
                    no = 14 if tjob.failure_count > 0 else 13
                else:
                    no = 34 if tjob.retry_needed else 23
                return no, 0, self.move_rest(cell, steps, env.MT_LOOP)
            return (15 if forward else 25), 0, self.hold_rest(cell, tjob, env.LT_LOOP)  # 発車直後（ノードが1つ進むまで）は周回を含めない（下界側の誤差で，一瞬で解消する）
        if progress == Progress.TO_PICKED:
            if isinstance(unit, Station):
                rest = self.hold_rest(cell, tjob)
                if rest is not None and not self.known_pick:  # 終了時刻（hold_end）は実用時にはわからない
                    rest = self.expected_pick_rest(self.now -cell.hold_start)
                return 18, 0, rest
            if cell is not unit.cells[-1]:
                return 16, cell.idx, self.hold_rest(cell, tjob)
            station_cell = tjob.pjob.station.cells[0]
            return 17, 0, self.hold_rest(station_cell, tjob) if station_cell.assigned_tjob is tjob else None
        if progress == Progress.TO_BW_LOOP:
            if isinstance(unit, Station):
                return 19, 0, self.hold_rest(cell, tjob)
            if cell is not unit.cells[-1]:
                return 20, cell.idx, self.hold_rest(cell, tjob)
            return self.locate_at_loop_entrance(tjob, 21)
        if progress == Progress.TO_BW_LIFT:
            car = gtps.lifts[store.aisle][BW].cells[0]
            if isinstance(unit, Conveyor):
                if cell is not unit.cells[-1]:
                    return 26, cell.idx, self.hold_rest(cell, tjob)
                return 27, 0, self.hold_rest(car, tjob, env.LT_LIFT) if car.assigned_tjob is tjob else None
            if car.node.idx < store.floor:
                return 28, 0, self.move_rest(car, store.floor -car.node.idx, env.MT_LIFT)
            return 29, 0, self.hold_rest(car, tjob, env.LT_LIFT)
        assert progress == Progress.TO_BW_SHTL, f'Unexpected progress: {tjob}'
        car = gtps.shuttles[store.aisle][store.floor].cells[0]
        if isinstance(unit, Conveyor):
            if cell is not unit.cells[-1]:
                return 30, cell.idx, self.hold_rest(cell, tjob)
            return 31, 0, self.hold_rest(car, tjob, env.LT_SHTL) if car.assigned_tjob is tjob else None
        if car.node.idx > store.row:
            return 32, 0, self.move_rest(car, car.node.idx -store.row, env.MT_SHTL)
        return 33, 0, self.hold_rest(car, tjob, env.LT_SHTL)
