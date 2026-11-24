import csv
import pytest
from gtp import create_simulator
from gtp.models import Progress, Station


PARAMS = [
    {'PJOB': 20, 'SEED': 42},
    {'PJOB': 50, 'SEED': 1, 'REPEATABLE': 0},  # 荷卸しの失敗と再搬送を起こす
]


def actor_cells(gtps):
    """搬送ジョブを割り付けるセル（シャトル・リフト・作業場・台車）"""
    cells = [shuttle.cells[0] for row in gtps.shuttles for shuttle in row]
    cells += [lift.cells[0] for row in gtps.lifts for lift in row]
    cells += [station.cells[0] for station in gtps.stations]
    cells += list(gtps.loop.cells)
    return cells


def passive_cells(gtps):
    """搬送ジョブを割り付けないセル（コンベヤ・置場）"""
    cells = [cell for stage in gtps.conveyors for dim0 in stage for dim1 in dim0 for conveyor in dim1
             for cell in conveyor.cells]
    cells += [store.cells[0] for aisle in gtps.stores for floor in aisle for store in floor]
    return cells


def run_with_checks(params):
    """時間1ごとにセルの状態の不変条件を確かめながら，最後まで実行する"""
    env = create_simulator(params=params)
    gtps = env.gtps
    actors = actor_cells(gtps)
    passives = passive_cells(gtps)

    def monitor(env):
        while True:
            yield env.timeout(1)
            for cell in actors:
                if cell.bucket is not None:  # 積んでいるバケットの搬送ジョブが割付け中のもの
                    assert cell.assigned_tjob is cell.bucket.current_tjob(), cell
                if cell.hold_end is not None:
                    assert cell.hold_start <= env.now
                    assert cell.hold_start <= cell.hold_end
            for cell in passives:
                assert cell.assigned_tjob is None, cell
                if cell.hold_end is not None:
                    assert cell.hold_start <= env.now

    env.process(monitor(env))
    env.run(until=env.simulation_completed)
    return env


@pytest.mark.parametrize('params', PARAMS)
def test_cell_state_invariants(params):
    env = run_with_checks(params)
    for cell in actor_cells(env.gtps):  # 完了時にはどの機械も搬送ジョブを占有していない
        assert cell.assigned_tjob is None, cell


@pytest.mark.parametrize('params', PARAMS)
def test_history(params):
    env = run_with_checks(params)
    tjobs = [tjob for pjob in env.gtps.pjobs for tjob in pjob.tjobs]
    for tjob in tjobs:
        times = [stamp.time for stamp in tjob.history]
        assert times == sorted(times), tjob
        # 先頭の記録は投入で，1回だけ
        creates = [stamp for stamp in tjob.history if stamp.kind == 'create']
        assert creates == tjob.history[:1], tjob
        assert creates[0].time == tjob.released_time
        assert creates[0].progress == Progress.TO_FW_SHTL
        proceeds = [stamp for stamp in tjob.history if stamp.kind == 'proceed']
        assert proceeds[-1].progress == Progress.DONE, tjob
        assert proceeds[-1].time == tjob.restored_time
        # 作業場への搬入の次の記録はピッキングの完了で，その間隔が作業時間に等しい
        idx = [i for i, stamp in enumerate(tjob.history)
               if stamp.kind == 'transfer' and isinstance(stamp.dst.unit, Station)]
        assert len(idx) == 1, tjob
        picked = tjob.history[idx[0] +1]
        assert picked.kind == 'proceed'
        assert picked.time == pytest.approx(tjob.picked_time)
        assert picked.time -tjob.history[idx[0]].time == pytest.approx(tjob.work_time)
        # 割付けと解除は対になる
        assigns = [stamp for stamp in tjob.history if stamp.kind == 'assign']
        releases = [stamp for stamp in tjob.history if stamp.kind == 'release']
        assert [s.dst for s in assigns] == [s.dst for s in releases], tjob
        assert tjob.failure_count <= env.REPEATABLE +1


def test_failures_and_retries_are_recorded():
    env = run_with_checks(PARAMS[1])
    tjobs = [tjob for pjob in env.gtps.pjobs for tjob in pjob.tjobs]
    fails = sum(1 for tjob in tjobs for stamp in tjob.history if stamp.kind == 'fail')
    retries = sum(tjob.retry_count for tjob in tjobs)
    assert fails > 0
    assert retries > 0
    for tjob in tjobs:  # 再搬送の回数だけ，往路のやり直し（TO_FW_SHTL への proceed）が記録される
        restarts = [stamp for stamp in tjob.history
                    if stamp.kind == 'proceed' and stamp.progress == Progress.TO_FW_SHTL]
        assert len(restarts) == tjob.retry_count, tjob


@pytest.mark.parametrize('params', PARAMS)
def test_dump_history(params, tmp_path):
    env = run_with_checks(params)
    env.gtps.dump_history(dir=str(tmp_path), fname='history')
    with open(tmp_path / 'history.csv', newline='') as f:
        rows = list(csv.DictReader(f))
    expected = [(pjob, tjob, seq, stamp) for pjob in env.gtps.pjobs for tjob in pjob.tjobs
                for seq, stamp in enumerate(tjob.history)]
    assert len(rows) == len(expected)
    for row, (pjob, tjob, seq, stamp) in zip(rows, expected):
        assert (int(row['pjob']), int(row['tjob']), int(row['seq'])) == (pjob.idx, tjob.idx, seq)
        assert float(row['time']) == stamp.time
        assert (row['kind'], row['progress']) == (stamp.kind, stamp.progress.name)
        for side, cell in (('src', stamp.src), ('dst', stamp.dst)):
            columns = (row[f'{side}_type'], row[f'{side}_unit'], row[f'{side}_cell'])
            if cell is None:
                assert columns == ('', '', '')
            else:
                assert columns == (cell.unit.__class__.__name__, cell.unit.get_label(), str(cell.idx))
    # ユニットの名称は重複しない
    units = {cell.unit for cell in actor_cells(env.gtps) +passive_cells(env.gtps)}
    assert len({unit.get_label() for unit in units}) == len(units)
