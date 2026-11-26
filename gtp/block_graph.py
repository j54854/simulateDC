from __future__ import annotations
from dataclasses import dataclass
from typing import NamedTuple
from .models import Direction, Progress, GTPSystem, Simulation_Environment, Store, TJob
from .operation_graph import START
from .reduction import DEFAULT_WEIGHTS, PORTS, SEGMENTS, Term
from .state import FW, BW, StateReader

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Timetable:  # ブロックの作業の，先頭の作業からの開始時刻（待たずに進んだ場合の累積）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, rows: list[tuple[int, int, float]]) -> None:  # rows: (作業番号，セル番号，処理時間) の列
        self.start, self.duration = {}, {}
        time = 0.0
        for no, seg, duration in rows:
            self.start[no, seg] = time
            self.duration[no, seg] = duration
            time += duration
        self.first = rows[0][:2]
        self.last = rows[-1][:2]
        self.total = time

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Timetables:  # ブロックの型と置場ごとの累積表（パラメータだけで決まるので，グラフの間で使い回せる）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    """迎車（11・21）は割付け前の名目値 0 で持つ．割り付けた後の迎車は必ずブロックの先頭で実行中なので，残り時間で扱う．

    Args:
        env: シミュレーション環境．
        pick_time: 未着手のピッキング時間の名目値．
    """
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env: Simulation_Environment, pick_time: float) -> None:
        self.env = env
        self.pick_time = pick_time
        self.cache = {}
        work = [(15, 0, env.LT_LOOP), *self.conveyor(16, 2), (17, 0, env.MT_CONV), (18, 0, pick_time)]
        self.emptying = Timetable(work)  # 空になるバケットの作業ブロック（18まで）
        self.work = Timetable([*work, (19, 0, env.MT_CONV), *self.conveyor(20, 2), (21, 0, 0.0), (22, 0, env.LT_LOOP)])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def conveyor(self, no: int, stage: int) -> list[tuple[int, int, float]]:  # セル間移動の行
        return [(no, c, self.env.MT_CONV) for c in range(self.env.CONV_LEN[stage] -1)]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def arrival(self, store: Store) -> Timetable:  # 到着ブロック（作業 1〜12）
        key = ('arrival', store.floor, store.row)
        if key not in self.cache:
            env = self.env
            run, rise = (env.ROW -store.row) *env.MT_SHTL, store.floor *env.MT_LIFT
            self.cache[key] = Timetable([
                (1, 0, run), (2, 0, env.LT_SHTL), (3, 0, run), (4, 0, env.LT_SHTL), *self.conveyor(5, 0),
                (6, 0, rise), (7, 0, env.LT_LIFT), (8, 0, rise), (9, 0, env.LT_LIFT), *self.conveyor(10, 1),
                (11, 0, 0.0), (12, 0, env.LT_LOOP)])
        return self.cache[key]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def back(self, store: Store) -> Timetable:  # 帰還ブロック・再帰還ブロック（作業 25〜33）
        key = ('back', store.floor, store.row)
        if key not in self.cache:
            env = self.env
            run, rise = (env.ROW -store.row) *env.MT_SHTL, store.floor *env.MT_LIFT
            self.cache[key] = Timetable([
                (25, 0, env.LT_LOOP), *self.conveyor(26, 1),
                (27, 0, env.LT_LIFT), (28, 0, rise), (29, 0, env.LT_LIFT), *self.conveyor(30, 0),
                (31, 0, env.LT_SHTL), (32, 0, run), (33, 0, env.LT_SHTL)])
        return self.cache[key]

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
@dataclass(eq=False)
class DirectBlock:  # ブロック（作業を展開せず，先頭の作業と累積表で表す）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    tjob: TJob
    kind: str  # 型（reduction.KINDS のいずれか）
    round: int  # 訪問の周
    table: Timetable
    head: tuple[int, int]  # 先頭の作業（作業番号，セル番号）
    rest: float | None = None  # 先頭の作業の残り時間（未着手ならNone）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __post_init__(self) -> None:
        self.key = ('block', self.tjob.pjob.idx, self.tjob.idx, self.kind)
        self.running = self.rest is not None
        # 表の時刻のうち，ブロックの先頭の時刻に当たるもの
        self.base = self.table.start[self.head]
        if self.running:
            self.base += self.table.duration[self.head] -self.rest
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def has(self, no: int, seg: int = 0) -> bool:  # 作業が残っているか？
        return (no, seg) in self.table.start and (no, seg) >= self.head
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def start(self, no: int, seg: int = 0) -> float:  # 作業の開始（すでに先頭がその作業以後なら0）
        return 0.0 if (no, seg) <= self.head else self.table.start[no, seg] -self.base
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def end(self, no: int, seg: int = 0) -> float:  # 作業の終了
        return self.table.start[no, seg] +self.table.duration[no, seg] -self.base
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def length(self) -> float:  # ブロックの長さ
        return self.table.total -self.base
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def port_key(self, port: str) -> tuple:  # ポートのノードのキー（作業ブロックのみ）
        return (*self.key, port)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Usage(NamedTuple):  # ブロックの中での，資源の使い方
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    resource: tuple
    first: tuple[int, int]  # 資源を使う最初の作業
    last: tuple[int, int]  # 資源を使う最後の作業
    no: int  # 資源を使う区間（L3）を表す作業番号

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class BlockGraph(StateReader):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    """縮約グラフを，作業を展開せずに状態から直接作る（ポートあり）．

    Reduction（参照実装）と同じ項を作る．定義は docs/tjob_graph.md の「オペレーショングラフからの導出（縮約）」を参照．
    シミュレーションの状態を読むだけで書き換えず，random も呼ばない．

    Args:
        gtps: 対象のGTPシステム．
        pick_time: 未着手のピッキング時間の名目値．省略時は平均．
        known_pick: 実行中のピッキングの残り時間に，実現値を使うか（検証用）．
        length: 'L0'（剛体近似の長さ．秒）または 'L3'（ピッキング1回を単位とする，簡略化した長さ）．
        weights: L3 の区間の重み（'shuttle'・'lift'・'loop'・'pick'）．省略時は DEFAULT_WEIGHTS．
        tables: 累積表．省略時は新しく作る（繰り返し構築するときは，使い回すと速い）．
    """
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, gtps: GTPSystem, pick_time: float | None = None, known_pick: bool = False,
            length: str = 'L0', weights: dict[str, int] | None = None, tables: Timetables | None = None) -> None:
        assert length in ('L0', 'L3'), f'Unknown length: {length}'
        super().__init__(gtps, pick_time, known_pick)
        self.tables = Timetables(self.env, self.pick_time) if tables is None else tables
        assert self.tables.pick_time == self.pick_time, 'Timetables are made with another pick_time'
        self.length = length
        self.weights = DEFAULT_WEIGHTS if weights is None else weights
        self.unit = self.expected_pick_rest(0) +2 *self.env.MT_CONV  # L3 の単位
        self.cumulative = {}  # 作業番号 -> その区間より前の区間の重みの和（ブロックの外のループ搬送は含めない）
        total = no = 0
        for name, last in SEGMENTS:
            while no < last:
                no += 1
                self.cumulative[no] = total
            if not name.startswith('loop'):
                total += self.weights.get(name.split('_')[0], 0)
        self.blocks = []  # 全ブロック
        self.nodes = []  # ノードのキー
        self.ports = {}  # 作業ブロック -> {ポート: ブロックの先頭からの時間}（残っているポートだけ）
        self.terms = []
        self.block_of = {}  # (TJob, 型) -> ブロック
        self.by_floor = {}  # (系列, 階層) -> 到着・再帰還・帰還ブロックのリスト
        self.station_lists = {}  # 作業場 -> (確定した列，順序が未確定の作業ブロック)（評価で使う）
        self.fan_terms = set()  # 作業場の列の扇の項（直列化で鎖に置き換える）
        for tjob in gtps.unrestored_tjobs:
            self.add_tjob(tjob)
        self.add_start_terms()
        self.add_shuttle_terms()
        self.add_arrival_terms()
        self.add_back_terms()
        self.add_retry_terms()
        self.add_precedence_terms()
        self.add_bucket_terms()
        self.add_station_terms()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_tjob(self, tjob: TJob) -> None:  # 1つのTJobのブロックと，その間の項
        blocks, series = self.make_blocks(tjob)
        store = tjob.bucket.home_store
        src = None
        for block in blocks:
            self.blocks.append(block)
            self.block_of[tjob, block.kind] = block
            if block.kind == 'work':
                self.add_ports(block)
                entry = block.port_key('Y') if 'Y' in self.ports[block] else None  # 荷卸しが済んでいれば，前のブロックはない
            else:
                self.nodes.append(block.key)
                self.by_floor.setdefault((store.aisle, store.floor), []).append(block)
                entry = block.key
            if src is not None:
                inside = 0 if src.kind == 'work' else self.block_length(src)  # 作業ブロックは，最後のポートO（作業22の終了）から
                self.terms.append(Term(src.port_key('O') if src.kind == 'work' else src.key, entry, 'chain', None,
                    inside +self.travel(src, block)))
            elif series is not None:  # 先頭がループの上
                self.terms.append(Term(START, entry, 'chain', None, series if self.length == 'L0' else self.weights['loop']))
            src = block
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def make_blocks(self, tjob: TJob) -> tuple[list[DirectBlock], float | None]:  # 残っているブロックと，ループの残り時間
        store = tjob.bucket.home_store
        location = self.locations.get(tjob)  # 格納済みならNone
        if location is None and not tjob.retry_needed:
            return [], None
        round = 1 if tjob.retry_needed else 0
        plan = []  # 残りうるブロックの（型，周，表）
        if tjob.retry_needed and location is not None:
            plan.append(('rereturn', 0, self.tables.back(store)))
        emptying = self.empties_bucket(tjob)
        plan.append(('arrival', round, self.tables.arrival(store)))
        plan.append(('work', round, self.tables.emptying if emptying else self.tables.work))
        if not emptying:
            plan.append(('return', round, self.tables.back(store)))
        no, seg, rest = (1, 0, None) if location is None else location
        series = None
        if no in (13, 14, 23, 34):  # ループの上：次のブロックはまだ始まっていない
            series = rest
            plan = plan[{13: 1, 14: 1, 23: 2, 34: 0}[no]:]
            head, rest = plan[0][2].first, None
        else:
            if not tjob.retry_needed or location is None:
                plan = plan[0 if no <= 12 else 1 if no <= 22 else 2:]
            head = (no, seg)
        blocks = [DirectBlock(tjob, *plan[0], head, rest)]
        blocks += [DirectBlock(tjob, *item, item[2].first) for item in plan[1:]]
        return blocks, series
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def travel(self, src: DirectBlock, dst: DirectBlock) -> float:  # 同じTJobの隣り合うブロックの間のループ搬送の時間
        aisle, picker = src.tjob.bucket.home_store.aisle, src.tjob.pjob.station.picker
        if self.length == 'L3':
            return 0 if dst.kind == 'arrival' else self.weights['loop']
        if dst.kind == 'work':  # 作業 13
            return self.loop_time(self.env.AISLE_SEG[aisle][FW], self.env.PICKER_SEG[picker][FW])
        if dst.kind == 'return':  # 作業 23
            return self.loop_time(self.env.PICKER_SEG[picker][BW], self.env.AISLE_SEG[aisle][BW])
        return 0.0  # 再帰還 → 到着
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_ports(self, block: DirectBlock) -> None:  # 作業ブロックのポートと，ポートの間の項（過ぎたポートは除く）
        ports = {}
        if block.has(15):
            ports['Y'] = block.start(15)
        if block.has(17):
            ports['P'] = block.start(17)
        if block.table is self.tables.emptying:  # 空になるバケット：Qは18の終了で，Oはない
            ports['Q'] = block.end(18)
        else:
            if block.has(19):
                ports['Q'] = block.end(19)
            ports['O'] = block.end(22)
        self.ports[block] = ports
        src, time = START, 0.0
        for port in PORTS:
            if port not in ports:
                continue
            node = block.port_key(port)
            self.nodes.append(node)
            if port != 'Y':  # 荷卸しの開始へは，到着ブロックまたは s からの項がある
                length = ports[port] -time
                if self.length == 'L3':
                    length = {'P': 0, 'Q': self.weights['pick'], 'O': self.pickup(block)}[port]
                self.terms.append(Term(src, node, 'chain', None, length))
            src, time = node, ports[port]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_start_terms(self) -> None:  # 開始エッジ（長さ0のものと，復帰中の機械からのもの）
        self.terms += [Term(START, node, 'start', None, 0) for node in self.nodes]
        if self.length == 'L3':  # 残りの復帰時間は0に丸め，長さ0のエッジと重なるので張らない
            return
        shuttle_rests, lift_rests = {}, {}  # 復帰中の機械 -> 残りの復帰時間
        for shuttle in (shuttle for row in self.gtps.shuttles for shuttle in row):
            rest = self.shuttle_return_rest(shuttle)
            if rest is not None:
                shuttle_rests[shuttle.aisle, shuttle.floor] = rest
        for aisle in range(self.env.AISLE):
            rest = self.lift_return_rest(aisle)
            if rest is not None:
                lift_rests[aisle] = rest
        for block in self.blocks:
            if block.kind == 'work':
                continue
            store = block.tjob.bucket.home_store
            rest = shuttle_rests.get((store.aisle, store.floor))
            if rest is not None and (block.kind != 'arrival' or block.has(4)):
                offset = block.start(1 if block.kind == 'arrival' else 31)
                self.terms.append(Term(START, block.key, 'start', ('shuttle', store.aisle, store.floor), rest -offset))
            rest = lift_rests.get(store.aisle)
            if rest is not None and block.kind != 'arrival' and block.has(29):
                self.terms.append(Term(START, block.key, 'start', ('lift', store.aisle, BW), rest -block.start(27)))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def conveyor_usages(self, stage: int, dim0: int, dim1: int, direction: Direction, enter: int, move: int, leave: int) -> list[Usage]:
        # コンベヤの各セルの使い方（enter: 先頭に入れる作業，move: セル間移動，leave: 末尾から出す作業）
        n = self.env.CONV_LEN[stage]
        return [Usage(('conv', stage, dim0, dim1, direction, c), (enter, 0) if c == 0 else (move, c -1),
            (leave, 0) if c == n -1 else (move, c), move) for c in range(n)]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def return_time(self, block: DirectBlock, last: tuple[int, int]) -> float:  # 最後の作業の後の復帰時間
        store = block.tjob.bucket.home_store
        if last[0] == 29:  # 復路リフト
            return store.floor *self.env.MT_LIFT
        if last[0] == 33:  # 復路シャトル
            return (self.env.ROW -store.row) *self.env.MT_SHTL
        return 0.0
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def entry(self, block: DirectBlock, usage: Usage) -> float:  # 入口オフセット
        if self.length == 'L0':
            return block.start(*usage.first)
        return max(0, self.cumulative[usage.no] -self.cumulative[block.head[0]])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def leave(self, block: DirectBlock, usage: Usage) -> float:  # 出口オフセット
        if self.length == 'L0':
            return block.end(*usage.last) +self.return_time(block, usage.last)
        return self.entry(block, usage) +self.weights.get(usage.resource[0], 0)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def block_length(self, block: DirectBlock) -> float:  # ブロックの長さ（復帰時間を除く）
        if self.length == 'L0':
            return block.length()
        last = 12 if block.kind == 'arrival' else 33
        total = self.cumulative[last] -self.cumulative[block.head[0]] +self.pickup(block)
        return total if block.kind == 'arrival' else total +self.weights['shuttle']  # 最後の区間（シャトル）自身の重み
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def pickup(self, block: DirectBlock) -> int:  # 割付け済みの台車の迎車（L3）
        return round(block.rest /self.unit) if block.running and block.head[0] in (11, 21) else 0
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_resource_terms(self, usage: Usage, kind: str, confirmed: list[DirectBlock], candidates: list[DirectBlock]) -> None:
        # 資源の項：確定した列の隣どうし（鎖）と，末尾から未確定のブロックへ（扇）
        chain = [block for block in confirmed if block.has(*usage.last)]  # この資源を使う作業が残っているもの
        for src, dst in zip(chain, chain[1:]):
            self.terms.append(Term(src.key, dst.key, kind, usage.resource, self.leave(src, usage) -self.entry(dst, usage)))
        if not chain:
            return
        tail = chain[-1]
        leave = self.leave(tail, usage)
        for dst in candidates:
            if dst.tjob is not tail.tjob and dst not in chain and dst.has(*usage.last):
                self.terms.append(Term(tail.key, dst.key, kind, usage.resource, leave -self.entry(dst, usage)))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_shuttle_terms(self) -> None:  # シャトル（割付け済みのTJobから，同じ階のほかの訪問への扇）
        for shuttle in (shuttle for row in self.gtps.shuttles for shuttle in row):
            tjob = shuttle.cells[0].assigned_tjob
            if tjob is None or self.shuttle_return_rest(shuttle) is not None:
                continue
            forward = tjob.progress == Progress.TO_FW_SHTL
            tail = self.block_of[tjob, 'arrival' if forward else self.back_kind(tjob)]
            resource = ('shuttle', shuttle.aisle, shuttle.floor)
            out, into = Usage(resource, (1, 0), (4, 0), 1), Usage(resource, (31, 0), (33, 0), 31)  # 出庫と入庫
            leave = self.leave(tail, out if forward else into)
            for dst in self.by_floor.get((shuttle.aisle, shuttle.floor), []):
                arrival = dst.kind == 'arrival'
                usage = out if arrival else into
                if dst.tjob is tjob or not dst.has(*usage.last):
                    continue
                kind = 'aisle' if forward and arrival else 'backward'
                self.terms.append(Term(tail.key, dst.key, kind, resource, leave -self.entry(dst, usage)))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def back_kind(self, tjob: TJob) -> str:  # 今の復路のブロックの型
        return 'rereturn' if tjob.retry_needed else 'return'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_arrival_terms(self) -> None:  # conv0^FW・往路リフト・conv1^FW
        for aisle in range(self.env.AISLE):
            arrivals = []  # 系列の到着ブロック
            for floor in range(self.env.FLOOR):
                candidates = [block for block in self.by_floor.get((aisle, floor), []) if block.kind == 'arrival']
                arrivals += candidates
                confirmed = [self.block_of[tjob, 'arrival'] for tjob in self.forward_conv0_order(aisle, floor)]
                if confirmed:
                    for usage in self.conveyor_usages(0, aisle, floor, FW, 4, 5, 7):
                        self.add_resource_terms(usage, 'aisle', confirmed, candidates)
            tjob = self.gtps.lifts[aisle][FW].cells[0].assigned_tjob
            if tjob is not None:
                usage = Usage(('lift', aisle, FW), (6, 0), (9, 0), 6)
                self.add_resource_terms(usage, 'aisle', [self.block_of[tjob, 'arrival']], arrivals)
            confirmed = [self.block_of[tjob, 'arrival'] for tjob in self.forward_conv1_order(aisle)]
            if confirmed:
                for usage in self.conveyor_usages(1, aisle, 0, FW, 9, 10, 12):
                    self.add_resource_terms(usage, 'aisle', confirmed, arrivals)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_back_terms(self) -> None:  # conv1^BW・復路リフト・conv0^BW
        for aisle in range(self.env.AISLE):
            confirmed = [self.block_of[tjob, self.back_kind(tjob)] for tjob in self.backward_order(aisle)]
            if not confirmed:
                continue
            backs = []  # 系列の帰還・再帰還ブロック
            for floor in range(self.env.FLOOR):
                candidates = [block for block in self.by_floor.get((aisle, floor), []) if block.kind != 'arrival']
                backs += candidates
                on_floor = [block for block in confirmed if block.tjob.bucket.home_store.floor == floor]
                if on_floor:
                    for usage in self.conveyor_usages(0, aisle, floor, BW, 29, 30, 31):
                        self.add_resource_terms(usage, 'backward', on_floor, candidates)
            for usage in (*self.conveyor_usages(1, aisle, 0, BW, 25, 26, 27), Usage(('lift', aisle, BW), (27, 0), (29, 0), 27)):
                self.add_resource_terms(usage, 'backward', confirmed, backs)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def back_length(self, block: DirectBlock) -> float:  # 帰還・再帰還ブロックの長さ（シャトルの復帰時間を含む）
        if self.length == 'L3':  # 復帰時間は，その機械の区間の重みに含まれる
            return self.block_length(block)
        return block.length() +self.return_time(block, (33, 0))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_retry_terms(self) -> None:  # 再搬送で置場へ戻る入庫 → やり直しの出庫（同じシャトルの2回の訪問）
        for block in self.blocks:
            if block.kind == 'rereturn':
                store = block.tjob.bucket.home_store
                self.terms.append(Term(block.key, self.block_of[block.tjob, 'arrival'].key, 'backward',
                    ('shuttle', store.aisle, store.floor), self.back_length(block)))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_precedence_terms(self) -> None:  # 作業場順序（同じ作業場の隣り合うPJobの荷卸しの間）
        groups = {}  # 作業場 -> {PJobのインデックス: 荷卸しが残っている作業ブロックのリスト}
        for block, ports in self.ports.items():
            if 'Y' in ports:
                pjob = block.tjob.pjob
                groups.setdefault(pjob.station.picker, {}).setdefault(pjob.idx, []).append(block)
        for picker, pjobs in groups.items():
            indices = sorted(pjobs)
            for prev_idx, next_idx in zip(indices, indices[1:]):
                for src in pjobs[prev_idx]:
                    for dst in pjobs[next_idx]:
                        self.terms.append(Term(src.port_key('Y'), dst.port_key('Y'), 'precedence', ('order', picker), src.end(15) if self.length == 'L0' else 0))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_bucket_terms(self) -> None:  # 同じバケットで割付け順に隣り合うTJobの入庫 → 出庫
        for bucket in dict.fromkeys(tjob.bucket for tjob in self.gtps.unrestored_tjobs):  # 重複を除き順序を保つ
            tjobs = bucket.undone_tjobs()
            for prev_tjob, next_tjob in zip(tjobs, tjobs[1:]):
                src = self.block_of.get((prev_tjob, 'return'))  # 格納済みなら，復帰中のシャトルの開始エッジが表す
                if src is not None:
                    self.terms.append(Term(src.key, self.block_of[next_tjob, 'arrival'].key, 'bucket',
                        ('bucket', bucket.idx), self.back_length(src)))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_station_terms(self) -> None:  # 作業場と，conv2 の容量・順序
        works = {}  # 作業場 -> 作業ブロックのリスト
        for block in self.ports:
            works.setdefault(block.tjob.pjob.station.picker, []).append(block)
        for picker, blocks in works.items():
            confirmed = [self.block_of[tjob, 'work'] for tjob in self.station_queue(picker)]
            members = set(confirmed)
            others = [block for block in blocks if block not in members]
            self.station_lists[picker] = (confirmed, others)  # 確定した列が空の作業場も残す（直列化で使う）
            if not confirmed:
                continue
            for rule in self.station_rules(picker):
                self.add_lagged_terms(rule, self.station_members(rule, confirmed), self.station_members(rule, others))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def pick_node(self, block: DirectBlock) -> tuple | None:  # ピッキングの開始の時刻を持つノード（過ぎていればNone）
        return block.port_key('P') if 'P' in self.ports[block] else None
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def station_rules(self, picker: int) -> list[tuple]:  # 作業場の列に沿って張る項の規則
        # （資源，何台前から張るか，元のポート，先のポート，値（Noneなら搬入の残り時間），列に数えるのに要るポート）
        capacity = self.env.CONV_LEN[2]
        return [
            (('station', picker), 1, 'Q', 'P', 0.0, 'Q'),  # 作業場：前のTJobの搬出（空になるならピッキング）が終わってから，次の搬入
            (('capacity', picker, FW), capacity, 'P', 'Y', None, None),  # 往路の容量：conv2^FW に空きができる（C2台前の搬入の終了）
            (('capacity', picker, BW), capacity, 'O', 'Q', 0.0, 'O'),  # 復路の容量：空になるバケットは conv2^BW を使わないので，列に数えない
            (('order', picker, BW), 1, 'O', 'O', self.env.LT_LOOP, 'O'),  # 復路の順序
        ]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def station_members(self, rule: tuple, blocks: list[DirectBlock]) -> list[DirectBlock]:  # その規則の列に数えるブロック
        port = rule[5]
        return blocks if port is None else [block for block in blocks if port in self.ports[block]]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def add_lagged_terms(self, rule: tuple, confirmed: list[DirectBlock], others: list[DirectBlock]) -> None:
        # 列の中で lag 台前のTJobから張る（鎖）．順序が未確定のものへは，次に列に入るとみなして張る（扇）
        lag = rule[1]
        for src, dst in zip(confirmed, confirmed[lag:]):
            term = self.station_term(rule, src, dst)
            if term is not None:
                self.terms.append(term)
        if len(confirmed) >= lag:
            for dst in others:
                term = self.station_term(rule, confirmed[-lag], dst)
                if term is not None:
                    self.terms.append(term)
                    self.fan_terms.add(term)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def station_term(self, rule: tuple, src: DirectBlock, dst: DirectBlock) -> Term | None:  # 作業場の列で src の後に dst が来るときの項
        resource, _, src_port, dst_port, value, _ = rule
        if src_port not in self.ports[src] or dst_port not in self.ports[dst]:
            return None  # 過ぎたポート
        if dst.running and (dst_port, dst.head[0]) in (('Y', 15), ('Q', 19), ('O', 22)):
            return None  # すでに使い始めている
        length = src.end(17) -src.start(17) if value is None else value
        return Term(src.port_key(src_port), dst.port_key(dst_port), 'station', resource, length if self.length == 'L0' else 0)
