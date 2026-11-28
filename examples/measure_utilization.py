"""設備ごとの稼働率と流量を測る（状態・履歴を読むだけで，models.py は変えない）

稼働の区間は，TJob の履歴の 'assign' から 'release' まで（主体のセルごと）．
リフト・シャトル・台車の稼働率は，迎えの空走と荷卸しに失敗した周回を含む．
作業場は，あわせてピッキングの作業時間だけの稼働率も求める．
立ち上がりと終わりの影響を除くため，ピッキング済みの TJob が 10% から 90% になる区間で測る（定常の流量）．
結果と読み取りは docs/flow_graph.md の「設備の能力と稼働率」と「物差し」．

使い方:
    uv run examples/measure_utilization.py --mode releasable   # RELEASABLE と台車の台数を振る
    uv run examples/measure_utilization.py --mode tpick        # T_PICK の倍率・台車・RELEASABLE を振る
    uv run examples/measure_utilization.py --mode seeds        # シードごとのばらつき
"""
import argparse
import statistics
import time

from gtp import create_simulator
from run_gtpsim import MyController

T_PICK = [6, 9, 12, 24, 36, 48, 60]  # 既定のピッキング作業時間（models.py と同じ）
TIME_LIMIT = 300000  # シミュレーション時間の上限（崩れて終わらない場合の打ち切り）


# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def clip(a: float, b: float, lo: float, hi: float) -> float:
    """区間 [a, b] のうち [lo, hi] に入る長さを返す．"""
    return max(0.0, min(b, hi) -max(a, lo))


# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def run(params: dict, label: str = '') -> dict:
    """1回実行して，流量・稼働率などを表示し，辞書で返す．"""
    env = create_simulator(params = params, controller_cls = MyController)
    t0 = time.time()
    env.run(until = env.simulation_completed | env.timeout(TIME_LIMIT))
    wall = time.time() -t0
    tjobs = [t for p in env.gtps.pjobs for t in p.tjobs]
    picked = sorted(t.picked_time for t in tjobs if t.picked_time is not None)
    done = len(picked) == len(tjobs)
    lo, hi = picked[int(len(picked) *0.1)], picked[int(len(picked) *0.9)]  # 定常の区間
    win = hi -lo
    n_win = sum(1 for x in picked if lo < x <= hi)
    busy: dict[str, float] = {}  # ユニットの種類ごとの稼働時間の合計
    for t in tjobs:
        opened: dict[int, tuple[float, object]] = {}
        for s in t.history:
            if s.kind == 'assign':
                opened[id(s.dst)] = (s.time, s.dst)
            elif s.kind == 'release' and id(s.dst) in opened:
                a, cell = opened.pop(id(s.dst))
                name = type(cell.unit).__name__
                busy[name] = busy.get(name, 0.0) +clip(a, s.time, lo, hi)
    pick_busy = sum(
        clip(t.picked_time -t.work_time, t.picked_time, lo, hi) for t in tjobs if t.picked_time is not None)
    counts = {'Station': env.PICKER, 'Shuttle': env.AISLE *env.FLOOR, 'Lift': env.AISLE *2, 'Loop': env.VEHICLE}
    util = {k: busy.get(k, 0.0) /(n *win) for k, n in counts.items()}
    result = {
        'done': done, 'makespan': env.now, 'wall': wall, 'flow': n_win /win,
        'pick': pick_busy /(env.PICKER *win), 'retries': sum(t.retry_count for t in tjobs), **util}
    util_text = ' '.join(f'{k}={v:.2f}' for k, v in util.items())
    print(f"{label}VEH={env.VEHICLE:>2} REL={env.RELEASABLE:>3} done={done} makespan={env.now:>7.0f} "
          f"wall={wall:5.1f}s flow={result['flow']:.3f}/s pick={result['pick']:.2f} {util_text} "
          f"retries={result['retries']}", flush = True)
    return result


# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main() -> None:
    parser = argparse.ArgumentParser(description = '設備ごとの稼働率と流量を測る')
    parser.add_argument('--mode', choices = ['releasable', 'tpick', 'seeds'], default = 'releasable')
    parser.add_argument('--pjob', type = int, default = 1000)
    args = parser.parse_args()

    if args.mode == 'releasable':  # RELEASABLE と台車の台数を振る
        for veh in (30, 10):
            for rel in ((15, 25, 50, 75, 100, 150) if veh == 30 else (25, 50, 100)):
                run({'PJOB': args.pjob, 'SEED': 1, 'VEHICLE': veh, 'RELEASABLE': rel})
    elif args.mode == 'tpick':  # T_PICK の倍率・台車・RELEASABLE を振る
        for scale in (1.0, 0.75, 0.5):
            for veh in (20, 30, 40):
                for rel in (25, 50, 75):
                    run({'PJOB': args.pjob, 'SEED': 1, 'VEHICLE': veh, 'RELEASABLE': rel,
                         'T_PICK': [t *scale for t in T_PICK]}, label = f'scale={scale} ')
    else:  # シードごとのばらつき
        for rel in (25, 50):
            flows = [run({'PJOB': args.pjob, 'SEED': seed, 'VEHICLE': 30, 'RELEASABLE': rel},
                         label = f'seed={seed:>2} ')['flow'] for seed in range(1, 11)]
            mean, sd = statistics.mean(flows), statistics.stdev(flows)
            print(f'REL={rel}: flow mean={mean:.3f} sd={sd:.3f} cv={sd /mean:.3f}', flush = True)


if __name__ == '__main__':
    main()
