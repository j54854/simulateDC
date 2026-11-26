"""評価（直列化）の精度を，作業場の列での位置ごとに実績と比べる

一定間隔で縮約グラフを作って評価し，作業場の列で先頭から k 番目の TJob について，
(1) ピッキングの開始 P の誤差，(2) その TJob の前に作業場が遊ぶかの判定，を完走後の実績と比べる（ジョブごと）．
さらに，作業場が今から k 回目に始めるピッキング（スロット）までの累積の遊びを，どの TJob かを問わずに比べる（スロットごと）．
物差しは docs/tjob_graph.md の「評価の目的と物差し」．k はファジイに捉え，打ち切らずに位置ごとの曲線で見る．
"""
import argparse
import itertools
import math
import time
from collections import defaultdict
from gtp import create_simulator
from gtp.block_graph import BlockGraph
from gtp.evaluation import Evaluation
from gtp.models import Station

MAX_K = 15  # 集計する列での位置の上限
IDLE = 0.5  # 実際に遊んだとみなす間（秒）

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def visits(env):  # 作業場 -> [(搬入の開始，搬出の終わり，TJob)]（P の順．再搬送では訪問が2回ありうる）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    result = defaultdict(list)
    for pjob in env.gtps.pjobs:
        for tjob in pjob.tjobs:
            start = None
            for stamp in tjob.history:
                if stamp.kind != 'transfer':
                    continue
                if isinstance(stamp.dst.unit, Station):
                    start = stamp.time -env.MT_CONV  # 搬入（17）の開始
                elif isinstance(stamp.src.unit, Station):
                    result[pjob.station.picker].append((start, stamp.time, tjob))
                    start = None
            if start is not None:  # 空になるバケット（搬出しない）
                result[pjob.station.picker].append((start, tjob.picked_time, tjob))
    return {picker: sorted(items, key=lambda item: item[0]) for picker, items in result.items()}

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def snapshot(env):  # 作業場の列の各 TJob について，モデルごとの (P, 遊ぶか) の見積り
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    rows = []  # (作業場, k, TJob, モデル, P の見積り（絶対時刻），遊ぶか)
    slots = {}  # (モデル, 作業場) -> 累積の遊びの見積り（スロット 1, 2, … の順．秒）
    for length in ('L0', 'L3'):
        graph = BlockGraph(env.gtps, length=length)
        scale = 1 if length == 'L0' else graph.unit
        serial = Evaluation(graph)
        plain = Evaluation(graph, serialize=False).times
        for picker, order in serial.orders.items():
            blocks = [graph.block_of[tjob, 'work'] for tjob in order]
            # 作業場が空く時刻（使っている TJob の搬出の終わり．なければ今）から，スロットごとの遊びを累積する
            end, total, cumulative = 0, 0, []
            for block in blocks:
                ports = graph.ports[block]
                if 'P' not in ports:
                    if 'Q' in ports:  # 作業場を使っている
                        end = max(end, serial.times[block.port_key('Q')])
                    continue
                total += max(serial.times[block.port_key('P')] -end, 0)
                end = serial.times[block.port_key('Q')]
                cumulative.append(total *scale)
            slots[length +'s', picker] = cumulative
            for k, block in enumerate(blocks):
                node = graph.pick_node(block)
                if node is None or k > MAX_K:
                    continue
                idle = None
                if k > 0:
                    prev = blocks[k -1]
                    q = serial.times[prev.port_key('Q')] if 'Q' in graph.ports[prev] else 0
                    idle = serial.times[node] -q > 1e-6
                if length == 'L0':
                    rows.append((picker, k, block.tjob, length +'s', env.now +serial.times[node], idle))
                    rows.append((picker, k, block.tjob, length +'p', env.now +plain[node], None))
                else:
                    rows.append((picker, k, block.tjob, length +'s', env.now +serial.times[node] *scale, idle))
                    rows.append((picker, k, block.tjob, length +'p', env.now +plain[node] *scale, None))
    return rows, slots

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def run_condition(params, interval):  # 1条件の実行（(k, モデル, 誤差, 見積りの遊び, 実際の遊び) のリスト）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    env = create_simulator(params=params)
    snapshots = []

    def monitor(env):
        while True:
            yield env.timeout(interval)
            snapshots.append((env.now, *snapshot(env)))

    env.process(monitor(env))
    env.run(until=env.simulation_completed)
    actual = visits(env)
    records, slot_records = [], []
    for now, rows, slots in snapshots:
        for (model, picker), cumulative in slots.items():  # 実績：今より後に始めた訪問を順に（どの TJob かは問わない）
            items = actual.get(picker, [])
            end = max([item[1] for item in items if item[0] < now <= item[1]], default=now)
            total = 0
            following = [item for item in items if item[0] >= now]
            for k, (predicted, item) in enumerate(zip(cumulative, following), 1):
                if k > MAX_K:
                    break
                total += max(item[0] -end, 0)
                end = item[1]
                slot_records.append((k, model, predicted, total))
        for picker, k, tjob, model, predicted, idle in rows:
            items = actual[picker]
            # スナップショットより後の，その TJob の最初の訪問
            index = next((i for i, item in enumerate(items) if item[2] is tjob and item[0] >= now), None)
            if index is None:
                continue
            start = items[index][0]
            real_idle = index > 0 and start -items[index -1][1] > IDLE
            records.append((k, model, predicted -start, idle, real_idle))
    return records, slot_records

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def summarize(records):  # k ごとの集計
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    acc = defaultdict(lambda: [0, 0.0, 0.0])
    judge = defaultdict(lambda: [0, 0, 0, 0])  # (見積り, 実際) = (遊ぶ, 遊ぶ), (遊ぶ, 遊ばない), (遊ばない, 遊ぶ), (遊ばない, 遊ばない)
    for k, model, error, idle, real_idle in records:
        a = acc[k, model]
        a[0] += 1
        a[1] += error
        a[2] += error *error
        if idle is not None and k > 0:
            judge[k, model][(0 if idle else 2) +(0 if real_idle else 1)] += 1
    print('| k | 件数 | L0p | L0s | L3p | L3s | 一致率 L0s | 一致率 L3s | 遊び（実際） | 遊び（見積り L0s／L3s） |')
    print('|---|---|---|---|---|---|---|---|---|---|')
    for k in range(MAX_K +1):
        cells = []
        for model in ('L0p', 'L0s', 'L3p', 'L3s'):
            n, s, q = acc[k, model]
            mean = s /n if n else 0
            cells.append(f'{mean:+.1f}／{math.sqrt(max(q /n -mean *mean, 0)) if n else 0:.1f}')
        rates = []
        for model in ('L0s', 'L3s'):
            j = judge[k, model]
            total = sum(j)
            rates.append(f'{(j[0] +j[3]) /total:.0%}' if total else '-')
        j0, j3 = judge[k, 'L0s'], judge[k, 'L3s']
        real = f'{(j0[0] +j0[2]) /sum(j0):.0%}' if sum(j0) else '-'
        pred = f'{(j0[0] +j0[1]) /sum(j0):.0%}／{(j3[0] +j3[1]) /sum(j3):.0%}' if sum(j0) and sum(j3) else '-'
        print(f'| {k} | {acc[k, "L0s"][0]} | ' +' | '.join(cells) +f' | {rates[0]} | {rates[1]} | {real} | {pred} |')

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def summarize_slots(slot_records):  # スロットごとの累積の遊びの集計
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    acc = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    moments = defaultdict(lambda: [0.0, 0.0, 0.0])  # 見積りの2乗，実績の2乗，積の和（相関係数用）
    for k, model, predicted, real in slot_records:
        m = moments[k, model]
        m[0] += predicted *predicted
        m[1] += real *real
        m[2] += predicted *real
        a = acc[k, model]
        error = predicted -real
        a[0] += 1
        a[1] += error
        a[2] += error *error
        a[3] += predicted
        a[4] += real
    print('| k | 件数 | 実績の平均 | L0s 見積りの平均 | L0s 誤差 | L0s 相関 | L3s 見積りの平均 | L3s 誤差 | L3s 相関 |')
    print('|---|---|---|---|---|---|---|---|---|')
    for k in range(1, MAX_K +1):
        n0 = acc[k, 'L0s'][0]
        if not n0:
            continue
        cells = [str(n0), f'{acc[k, "L0s"][4] /n0:.1f}']
        for model in ('L0s', 'L3s'):
            n, s, q, p, _ = acc[k, model]
            mean = s /n
            pp, rr, pr = moments[k, model]
            mp, mr = p /n, acc[k, model][4] /n
            cov, vp, vr = pr /n -mp *mr, pp /n -mp *mp, rr /n -mr *mr
            corr = cov /math.sqrt(vp *vr) if vp > 0 and vr > 0 else float('nan')
            cells += [f'{p /n:.1f}', f'{mean:+.1f}／{math.sqrt(max(q /n -mean *mean, 0)):.1f}', f'{corr:.2f}']
        print(f'| {k} | ' +' | '.join(cells) +' |')

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    parser = argparse.ArgumentParser()
    parser.add_argument('--pjob', type=int, default=100)
    parser.add_argument('--seeds', type=int, nargs='+', default=[1, 2, 3, 4, 5])
    parser.add_argument('--vehicles', type=int, nargs='+', default=[10, 20, 30])
    parser.add_argument('--releasables', type=int, nargs='+', default=[25, 50, 100])
    parser.add_argument('--skip', default='10,100', help='除く条件（VEHICLE,RELEASABLE）')
    parser.add_argument('--interval', type=float, default=19.37)
    args = parser.parse_args()
    skip = tuple(int(x) for x in args.skip.split(',')) if args.skip else None
    groups, slot_groups = defaultdict(list), defaultdict(list)
    for vehicle, releasable in itertools.product(args.vehicles, args.releasables):
        if (vehicle, releasable) == skip:
            continue
        began = time.time()
        for seed in args.seeds:
            params = {'PJOB': args.pjob, 'SEED': seed, 'VEHICLE': vehicle, 'RELEASABLE': releasable}
            records, slot_records = run_condition(params, args.interval)
            for name in ('all', 'light' if releasable == 25 else 'congested'):
                groups[name] += records
                slot_groups[name] += slot_records
        print(f'VEHICLE={vehicle} RELEASABLE={releasable}: {time.time() -began:.0f} s')
    for name, title in (('all', '全条件'), ('light', 'RELEASABLE=25'), ('congested', 'RELEASABLE=50・100')):
        print(f'\n## {title}：ジョブごと（P の誤差はバイアス／標準偏差，秒）')
        summarize(groups[name])
        print(f'\n## {title}：スロットごとの累積の遊び（秒．誤差はバイアス／標準偏差）')
        summarize_slots(slot_groups[name])

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
