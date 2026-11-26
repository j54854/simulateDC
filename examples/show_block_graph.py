"""縮約グラフ（BlockGraph）を，横軸を時刻（最長経路，L0），縦軸を TJob として描画する"""
import argparse
import os
import matplotlib
import networkx as nx
from gtp import create_simulator
from gtp.block_graph import BlockGraph
from gtp.operation_graph import START

INK, MUTED, GRID, SURFACE = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
EDGE_STYLES = {  # 種類 -> (色, 線種, 凡例)
    'chain': ('#8a8984', 'solid', 'chain（同じTJob）'),
    'station': ('#2a78d6', 'solid', 'station（作業場・容量・順序）'),
    'precedence': ('#1baf7a', 'dashed', 'precedence（作業場順序）'),
    'aisle': ('#eb6834', 'solid', 'aisle（往路の資源）'),
    'backward': ('#4a3aa7', 'dashdot', 'backward（復路の資源．仮称）'),
    'bucket': ('#e34948', 'dotted', 'bucket（同じバケット）'),
    }
BLOCK_LABELS = {'arrival': 'A', 'return': 'R', 'rereturn': 'r'}

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def longest_times(graph):  # 各ノードの時刻（現在を0とする）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    g = nx.DiGraph()
    g.add_edges_from((term.src, term.dst) for term in graph.terms)
    incoming = {}
    for term in graph.terms:
        incoming.setdefault(term.dst, []).append(term)
    times = {START: 0.0}
    for node in nx.topological_sort(g):
        if node != START:
            times[node] = max(times[term.src] +term.value for term in incoming[node])
    return times

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def place_of(tjob):  # TJobの居場所（行の見出し用）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    unit = tjob.bucket.cell.unit
    name = unit.__class__.__name__.lower()
    if name == 'conveyor':
        return f"conv{unit.stage}{'FW' if unit.direction == 0 else 'BW'}"
    if tjob is not tjob.bucket.current_tjob():
        return 'waiting bucket'
    return name

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw(graph, tjobs, kinds, nodes, title, ax):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    times = longest_times(graph)
    row = {(tjob.pjob.idx, tjob.idx): i for i, tjob in enumerate(tjobs)}
    shown = {node for node in graph.nodes if node[1:3] in row and (node[4] if len(node) > 4 else node[3]) in nodes}
    pos = {node: (times[node], row[node[1:3]]) for node in shown}
    best = {}  # （始点，終点，種類）ごとに，最も長い項だけを描く
    for term in graph.terms:
        if term.kind in kinds and term.src in shown and term.dst in shown:
            key = (term.src, term.dst, term.kind)
            best[key] = max(best.get(key, float('-inf')), term.value)
    for (src, dst, kind), value in best.items():
        color, style, _ = EDGE_STYLES[kind]
        binding = abs(times[src] +value -times[dst]) < 1e-6  # 終点の時刻を決めている項
        same = src[1:3] == dst[1:3]
        ax.annotate('', xy=pos[dst], xytext=pos[src], zorder=3 if binding else 1, arrowprops=dict(
            arrowstyle='-|>', color=color, linestyle=style, lw=2.0 if binding else 0.8, alpha=0.95 if binding else 0.28,
            shrinkA=6, shrinkB=6, mutation_scale=9, connectionstyle=f"arc3,rad={0.0 if same else 0.12}"))
    for node, (x, y) in pos.items():
        label = node[4] if len(node) > 4 else BLOCK_LABELS[node[3]]
        ax.plot(x, y, 'o', ms=13, mfc=SURFACE, mec=INK, mew=1.0, zorder=4)
        ax.text(x, y, label, ha='center', va='center', fontsize=7, color=INK, zorder=5)
    ax.set_yticks(range(len(tjobs)), [f'{t.pjob.idx}-{t.idx}  {place_of(t)}' +('  retry' if t.retry_needed else '') for t in tjobs], fontsize=7, color=INK)
    ax.set_ylim(len(tjobs) -0.4, -0.6)
    ax.set_xlabel('現在からの時刻（秒，L0 の最長経路）', color=MUTED, fontsize=9)
    ax.set_title(title, loc='left', fontsize=11, color=INK)
    ax.grid(axis='x', color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, length=0)
    for tick in ax.get_yticklabels():
        tick.set_color(INK)
    for side in ax.spines.values():
        side.set_visible(False)
    handles = [matplotlib.lines.Line2D([], [], color=EDGE_STYLES[k][0], linestyle=EDGE_STYLES[k][1], lw=2, label=EDGE_STYLES[k][2])
        for k in EDGE_STYLES if any(key[2] == k for key in best)]
    ax.legend(handles=handles, loc='upper center', bbox_to_anchor=(0.5, -0.07 if len(tjobs) > 14 else -0.12), ncol=3, fontsize=8, frameon=False, labelcolor=INK)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    parser = argparse.ArgumentParser(description='縮約グラフを描画する')
    parser.add_argument('--view', choices=['all', 'station', 'aisle'], default='station')
    parser.add_argument('--index', type=int, default=0, help='作業場または系列の番号')
    parser.add_argument('--time', type=float, default=200)
    parser.add_argument('--pjob', type=int, default=100)
    parser.add_argument('--seed', type=int, default=1234)
    parser.add_argument('--releasable', type=int, default=50)
    parser.add_argument('--out', default=None)
    args = parser.parse_args()
    matplotlib.use('Agg')
    matplotlib.rcParams['font.family'] = ['Hiragino Sans', 'sans-serif']
    import matplotlib.pyplot as plt

    env = create_simulator({'SEED': args.seed, 'PJOB': args.pjob, 'RELEASABLE': args.releasable})
    env.run(until=args.time)
    graph = BlockGraph(env.gtps)
    tjobs = list(dict.fromkeys(block.tjob for block in graph.blocks))
    rank = {tjob: i for p in range(env.PICKER) for i, tjob in enumerate(graph.station_queue(p))}
    by_station = lambda t: (t.pjob.station.picker, rank.get(t, 999), t.pjob.idx, t.idx)
    nodes, kinds = set('YPQO') | {'arrival', 'return', 'rereturn'}, set(EDGE_STYLES)
    note = '太線：終点の時刻を決めている項'
    if args.view == 'station':
        working = {block.tjob for block in graph.ports}  # 作業ブロックが残っているもの
        tjobs = sorted((t for t in tjobs if t in working and t.pjob.station.picker == args.index), key=by_station)
        kinds = {'chain', 'station', 'precedence'}
        title = f'作業場 {args.index}（t = {args.time:g}）．上から確定した順 → PJob 順．{note}'
    elif args.view == 'aisle':
        tjobs = [t for t in tjobs if t.bucket.home_store.aisle == args.index]
        tjobs.sort(key=lambda t: (t.bucket.home_store.floor, -t.progress, t.pjob.idx, t.idx))
        kinds, nodes = {'aisle', 'backward', 'bucket'}, {'arrival', 'return', 'rereturn'}
        title = f'系列 {args.index}（t = {args.time:g}）．階ごとの並び．{note}'
    else:
        tjobs.sort(key=by_station)
        title = f'全体（t = {args.time:g}）．作業場ごとの並び．{note}'
    fig, ax = plt.subplots(figsize=(13, 1.8 +0.34 *len(tjobs)), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    draw(graph, tjobs, kinds, nodes, title, ax)
    out = args.out or f'data/block_graph_{args.view}{args.index}_t{args.time:g}.png'
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches='tight', facecolor=SURFACE)
    print(f'saved: {out}  ({len(tjobs)} TJobs)')

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
