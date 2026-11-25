from __future__ import annotations
from .models import Direction, Progress, GTPSystem, TJob, Cell, Conveyor, Shuttle, Loop, Station, Store

TOL = 1e-6  # 時刻の比較の許容誤差
FW, BW = Direction.FORWARD, Direction.BACKWARD

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class StateReader:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    """時刻 t のシミュレータの状態から，グラフの構築に要る情報を読み取る（読むだけで書き換えず，random も呼ばない）．

    オンライングラフ（OnlineGraph）と，縮約グラフの直接構築の共通の基底クラス．

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
        # 接続口での荷卸しと発進を，処理時間の長さで区別するため
        assert abs(self.env.LT_LOOP -self.env.MT_LOOP) > TOL, 'LT_LOOP and MT_LOOP must differ'
        self.pick_time = self.expected_pick_rest(0) if pick_time is None else pick_time
        self.known_pick = known_pick
        self.car_of = {car.assigned_tjob: car for car in gtps.loop.cells if car.assigned_tjob is not None}
        # TJob -> 最初の未完了の作業（作業番号，セル番号，残り時間）．格納済みのものは除く
        self.locations = {tjob: self.locate(tjob) for tjob in gtps.unrestored_tjobs if not self.is_stored(tjob)}
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_stored(self, tjob: TJob) -> bool:  # 置場へ格納済みで，シャトルが復帰中か？
        return tjob.progress == Progress.TO_BW_SHTL and tjob.bucket.is_at_home()
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
    def tjobs_on(self, conveyor: Conveyor) -> list[TJob]:  # コンベヤ上のTJob（下流から順に）
        return [cell.bucket.current_tjob() for cell in reversed(conveyor.cells) if cell.bucket is not None]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_dropping(self, tjob: TJob) -> bool:  # ループから荷卸し中か？（コンベヤに入る順が確定している）
        no, _, rest = self.locations[tjob]
        return no in (15, 25) and rest is not None
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def shuttle_return_rest(self, shuttle: Shuttle) -> float | None:  # 入庫を終えたシャトルの残りの復帰時間（復帰中でなければNone）
        car = shuttle.cells[0]
        if car.assigned_tjob is None or car.assigned_tjob.progress == Progress.TO_FW_SHTL:
            return None
        if not car.assigned_tjob.bucket.is_at_home():  # 格納前
            return None
        return self.move_rest(car, self.env.ROW -car.node.idx, self.env.MT_SHTL)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def lift_return_rest(self, aisle: int) -> float | None:  # 荷卸しを終えた復路リフトの残りの復帰時間（復帰中でなければNone）
        car = self.gtps.lifts[aisle][BW].cells[0]
        if car.assigned_tjob is None or car.assigned_tjob.progress == Progress.TO_BW_LIFT:
            return None
        return self.move_rest(car, car.node.idx, self.env.MT_LIFT)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def forward_conv0_order(self, aisle: int, floor: int) -> list[TJob]:  # conv0^FW に入る順が確定したTJob
        tjobs = self.tjobs_on(self.gtps.conveyors[0][aisle][floor][FW])
        tjob = self.gtps.shuttles[aisle][floor].cells[0].assigned_tjob
        if tjob is not None and tjob.progress == Progress.TO_FW_SHTL:  # 出庫の割付け済み
            tjobs.append(tjob)
        return tjobs
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def forward_conv1_order(self, aisle: int) -> list[TJob]:  # conv1^FW に入る順が確定したTJob
        tjobs = self.tjobs_on(self.gtps.conveyors[1][aisle][0][FW])
        tjob = self.gtps.lifts[aisle][FW].cells[0].assigned_tjob
        if tjob is not None:
            tjobs.append(tjob)
        return tjobs
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def station_queue(self, picker: int) -> list[TJob]:  # 確定した作業場の順（conv2^BW 上 → 作業場 → conv2^FW 上 → 荷卸し中）
        forward, backward = self.gtps.conveyors[2][picker][0]
        tjobs = self.tjobs_on(backward)
        if not self.gtps.stations[picker].cells[0].is_open():
            tjobs.append(self.gtps.stations[picker].cells[0].bucket.current_tjob())
        tjobs += self.tjobs_on(forward)
        tjobs += [tjob for tjob in self.car_of if tjob.progress == Progress.ON_FW_LOOP
            and tjob.pjob.station.picker == picker and self.is_dropping(tjob)]
        return tjobs
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def backward_order(self, aisle: int) -> list[TJob]:  # conv1^BW に入る順が確定したTJob（conv0^BW 上 → リフト → conv1^BW 上 → 荷卸し中）
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
        return tjobs
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def hold_rest(self, cell: Cell, tjob: TJob, duration: float | None = None) -> float | None:
        # 当該TJobの作業が実行中なら残り時間（実行中でなければNone）
        if cell.hold_end is None or cell.hold_end < self.now -TOL:
            return None
        # 直前の記録より前に始まった作業は，当該TJobの今の作業ではない（releaseは他の機械の復帰の終わりなので除く）
        since = next((stamp.time for stamp in reversed(tjob.history) if stamp.kind != 'release'), 0)
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
