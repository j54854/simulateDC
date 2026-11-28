"""ディスパッチの呼び出しごとの候補の数を数える（計算量の見積り用）

MyController で動かし，D0〜D3（dispatch_pjobs・dispatch_shuttle・dispatch_lift・dispatch_loop）の
呼び出しの回数と，候補の数の分布を表示する．D0 の候補は作業場の数，D3 の候補は往路と復路の合計．
結果と読み取りは docs/flow_graph.md の「実験の計画」の「計算量」．

使い方:
    uv run examples/count_candidates.py
"""
import argparse
import time
from collections import Counter

from gtp import create_simulator
from run_gtpsim import MyController

STATS: dict[str, Counter] = {}  # 判断点ごとの候補の数の分布


# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def record(name: str, n: int) -> None:
    STATS.setdefault(name, Counter())[n] += 1


# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class CountingController(MyController):
    """MyController の判断はそのままに，候補の数だけを記録する．"""

    def dispatch_pjobs(self, waiting_pjobs, last_picker):
        record('D0', self.env.PICKER)
        return super().dispatch_pjobs(waiting_pjobs, last_picker)

    def dispatch_shuttle(self, shuttle, candidate_tjobs):
        record('D1', len(candidate_tjobs))
        return super().dispatch_shuttle(shuttle, candidate_tjobs)

    def dispatch_lift(self, lift, candidate_tjobs):
        record('D2', len(candidate_tjobs))
        return super().dispatch_lift(lift, candidate_tjobs)

    def dispatch_loop(self, loop, candidate_fw_tjobs):
        n_bw = len([t for t in loop.tjobs if t not in candidate_fw_tjobs])  # 復路の候補
        record('D3', len(candidate_fw_tjobs) +n_bw)
        return super().dispatch_loop(loop, candidate_fw_tjobs)


# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main() -> None:
    parser = argparse.ArgumentParser(description = 'ディスパッチの候補の数を数える')
    parser.add_argument('--pjob', type = int, default = 200)
    parser.add_argument('--seed', type = int, default = 1)
    args = parser.parse_args()

    for releasable in (50, 100):
        STATS.clear()
        env = create_simulator(
            params = {'PJOB': args.pjob, 'SEED': args.seed, 'RELEASABLE': releasable},
            controller_cls = CountingController)
        t0 = time.time()
        env.run(until = env.simulation_completed | env.timeout(10 **6))
        n_tjobs = sum(len(p.tjobs) for p in env.gtps.pjobs)
        print(f'RELEASABLE={releasable} makespan={env.now:.0f} wall={time.time() -t0:.1f}s tjobs={n_tjobs}')
        for name in ('D0', 'D1', 'D2', 'D3'):
            c = STATS.get(name, Counter())
            calls = sum(c.values())
            evals = sum(k *v for k, v in c.items())
            print(f'  {name}: calls={calls} evals={evals} mean={evals /max(calls, 1):.2f} '
                  f'max={max(c) if c else 0} dist={sorted(c.items())}')


if __name__ == '__main__':
    main()
