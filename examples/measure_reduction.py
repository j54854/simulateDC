"""オンライングラフと縮約グラフの時刻を，実績と比べる（参照実装の段階4）

一定間隔でグラフを作り，各 TJob のブロックの開始とポートの時刻を，完走後の履歴と比較する．
周回の規則と直列化は使わない（最長経路だけで計算する）．
"""
import argparse
import csv
import itertools
import os
import time
from collections import Counter, defaultdict
import networkx as nx
from gtp import create_simulator
from gtp.models import Conveyor, Direction, Loop, Progress, Shuttle, Station
from gtp.operation_graph import START, OnlineGraph
from gtp.reduction import Reduction

# 比べる時刻（A: 出庫の迎車の開始，Y・P・Q・O: 作業ブロックのポート（仮称），R: 帰還ブロックの開始）
QUANTITIES = ('A', 'Y', 'P', 'Q', 'O', 'R')
HORIZONS = ((0, 60), (60, 120), (120, float('inf')))  # 実績までの時間（秒）の区分

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def target_ops(chain):  # 比べる時刻 -> (作業，終了時刻か)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    last = {op.no: op for op in chain}  # 再搬送中の25〜33は，最後のもの
    targets = {}
    if chain[0].no == 1 and not chain[0].running:
        targets['A'] = (chain[0], False)
    for name, no, at_end in (('Y', 15, False), ('P', 17, False), ('Q', 19 if 19 in last or chain[-1].no > 19 else 18, True),
            ('O', 22, True), ('R', 25, False)):
        op = last.get(no)
        if op is not None and not (op.running and not at_end):  # 開始を比べるものは，未着手に限る
            targets[name] = (op, at_end)
    if any(op.round == 1 for op in chain) and not any(op.round == 1 and op.no == 25 for op in chain):
        targets.pop('R', None)  # 再帰還の25しかない（空になるバケット）
    return targets

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def online_times(online):  # オンライングラフの最早開始時刻
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    g, starts = online.graph, {START: online.now}
    for node in nx.topological_sort(g):
        if node == START:
            continue
        starts[node] = max(starts[u] +(0 if u == START else g.nodes[u]['op'].duration) +data['lag']
            for u, _, data in g.in_edges(node, data=True))
    return starts

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def predict(online, models):  # モデル -> {(TJob, 比べる時刻): 見積り}
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    targets = {(tjob, name): target for tjob, chain in online.chains.items() if chain
        for name, target in target_ops(chain).items()}
    result = {}
    if 'online' in models:
        starts = online_times(online)
        result['online'] = {key: starts[op.key] +(op.duration if at_end else 0) for key, (op, at_end) in targets.items()}
    for model, ports, length in (('L0', False, 'L0'), ('L0p', True, 'L0'), ('L3p', True, 'L3')):
        if model not in models:
            continue
        reduction = Reduction(online, ports, length)
        times = reduction.times()
        values = {}
        for key, (op, at_end) in targets.items():
            block = reduction.block_of[op.key]
            if ports and block.kind == 'work':
                value = times[block.port_key(key[1])]
            else:
                value = times[block.key]
                if length == 'L0':
                    value += reduction.anchor[op.key][1] +(op.duration if at_end else 0)
            values[key] = value if length == 'L0' else online.now +value *reduction.unit
        result[model] = values
    return result

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def actual_times(tjob, n_history, env):  # 比べる時刻の実績
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    actual = {}
    for stamp in tjob.history[n_history:]:
        if stamp.kind == 'assign' and isinstance(stamp.dst.unit, Shuttle) and stamp.progress == Progress.TO_FW_SHTL:
            actual.setdefault('A', stamp.time)  # 最初の出庫
        if stamp.kind != 'transfer':
            continue
        src, dst = stamp.src.unit, stamp.dst.unit
        if isinstance(src, Loop) and isinstance(dst, Conveyor):  # 最後のものが残る
            actual['Y' if dst.stage == 2 else 'R'] = stamp.time -env.LT_LOOP
        elif isinstance(dst, Station):
            actual['P'] = stamp.time -env.MT_CONV
        elif isinstance(src, Station):
            actual['Q'] = stamp.time
        elif isinstance(src, Conveyor) and isinstance(dst, Loop) and src.stage == 2 and src.direction == Direction.BACKWARD:
            actual['O'] = stamp.time
    if 'Q' not in actual and tjob.picked_time is not None:
        actual['Q'] = tjob.picked_time  # 空になるバケット
    return actual

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def bottleneck_sides(online, length):  # TJob -> Pを決めるのが作業場側か供給側か
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    reduction = Reduction(online, True, length)
    times = reduction.times()
    sides = defaultdict(lambda: [None, None])
    blocks = {block.port_key('P'): block for block, cuts in reduction.port_cuts.items() if 'P' in cuts}
    for term in reduction.terms:
        if term.dst in blocks and term.kind in ('station', 'chain'):
            i = 0 if term.kind == 'station' else 1
            value = times[term.src] +term.value
            side = sides[blocks[term.dst].tjob]
            side[i] = value if side[i] is None else max(side[i], value)
    return {tjob: 'tie' if abs(s -c) < 1e-6 else 'station' if s > c else 'supply'
        for tjob, (s, c) in sides.items() if s is not None and c is not None}

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def run_condition(params, interval):  # 1条件の実行（誤差のリストと，判定の集計を返す）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    env = create_simulator(params=params)
    snapshots, sides = [], Counter()

    def monitor(env):
        while True:
            yield env.timeout(interval)
            expected = OnlineGraph(env.gtps)
            predicted = predict(expected, ('online', 'L0', 'L0p', 'L3p'))
            l0, l3 = bottleneck_sides(expected, 'L0'), bottleneck_sides(expected, 'L3')
            assert l0.keys() == l3.keys()
            sides.update((l0[tjob], l3[tjob]) for tjob in l0)
            known = OnlineGraph(env.gtps, known_pick=True)
            snapshots.append((known, predicted, {tjob: len(tjob.history) for tjob in known.chains}))

    env.process(monitor(env))
    env.run(until=env.simulation_completed)
    errors = []  # (比べる時刻, 実績までの時間, 再搬送が起きたか, {(ピッキング, モデル): 誤差})
    for known, predicted, n_history in snapshots:
        for tjob, chain in known.chains.items():
            for op in chain:
                if op.no == 18 and not op.running:
                    op.duration = tjob.work_time  # 実現値
        realized = predict(known, ('online', 'L0', 'L0p'))
        merged = {('expected', model): values for model, values in predicted.items()}
        merged.update({('realized', model): values for model, values in realized.items()})
        actuals = {tjob: actual_times(tjob, n_history[tjob], env) for tjob in known.chains}
        retried = {tjob: any(stamp.kind == 'proceed' and stamp.progress == Progress.ON_BW_LOOP
            and prev.progress == Progress.ON_FW_LOOP
            for prev, stamp in zip(tjob.history[n_history[tjob] -1:], tjob.history[n_history[tjob]:]))
            for tjob in known.chains}
        for tjob, name in predicted['online']:
            actual = actuals[tjob][name]
            errors.append((name, actual -known.now, retried[tjob],
                {label: values[tjob, name] -actual for label, values in merged.items()}))
    return env, len(snapshots), errors, sides

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def summarize(errors):  # 集計（比べる時刻・区分・ピッキング・モデルごと）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    groups = defaultdict(list)
    for name, horizon, retried, values in errors:
        for low, high in HORIZONS:
            if low <= horizon < high:
                for (pick, model), error in values.items():
                    for subset in ('all',) if retried else ('all', 'no_retry'):
                        groups[name, (low, high), subset, pick, model].append(error)
    rows = []
    for (name, (low, high), subset, pick, model), values in sorted(groups.items(), key=lambda item: (QUANTITIES.index(item[0][0]), item[0][1:])):
        n = len(values)
        rows.append({'quantity': name, 'horizon': f'{low}-{high}', 'subset': subset, 'pick': pick, 'model': model, 'n': n,
            'bias': sum(values) /n, 'mae': sum(abs(v) for v in values) /n, 'late_ratio': sum(v > 1e-6 for v in values) /n})
    return rows

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    parser = argparse.ArgumentParser()
    parser.add_argument('--pjob', type=int, default=100)
    parser.add_argument('--seeds', type=int, nargs='+', default=[1, 2, 3, 4, 5])
    parser.add_argument('--vehicles', type=int, nargs='+', default=[10, 20, 30])
    parser.add_argument('--releasables', type=int, nargs='+', default=[25, 50, 100])
    parser.add_argument('--interval', type=float, default=19.37)
    parser.add_argument('--out', default='data/reduction_error.csv')
    args = parser.parse_args()
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', newline='') as f:
        writer = None
        for vehicle, releasable in itertools.product(args.vehicles, args.releasables):
            errors, per_seed, sides, began, n_snapshots = [], {}, Counter(), time.time(), 0
            for seed in args.seeds:
                params = {'PJOB': args.pjob, 'SEED': seed, 'VEHICLE': vehicle, 'RELEASABLE': releasable}
                _, n, errs, side = run_condition(params, args.interval)
                errors += errs
                per_seed[seed] = errs
                sides += side
                n_snapshots += n
            for seed, errs in (*per_seed.items(), ('all', errors)):
                for row in summarize(errs):
                    row = {'vehicle': vehicle, 'releasable': releasable, 'seed': seed, **row}
                    if writer is None:
                        writer = csv.DictWriter(f, fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow(row)
            total = sum(sides.values())
            print(f'VEHICLE={vehicle} RELEASABLE={releasable}: {n_snapshots} snapshots, {len(errors)} targets, {time.time() -began:.0f} s')
            print('  bottleneck (L0p, L3p):', ', '.join(f'{a}/{b} {n /total:.1%}' for (a, b), n in sorted(sides.items(), key=str)))

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
