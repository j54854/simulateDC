import argparse
import logging
import os
import matplotlib
import networkx as nx
from gtp import *

# 上から順に並べる段（位置）と，その段の表示名
BANDS = [
    ('start', 'start'),
    ('station', 'station'),
    ('conv2', 'conv2'),
    ('car', 'loop car'),
    ('conv1', 'conv1'),
    ('lift', 'lift'),
    ('conv0', 'conv0'),
    ('shuttle', 'shuttle'),
    ('store', 'store'),
    ('beyond_store', 'beyond store'),
    ('off_route', 'off route'),
    ]
CONVEYORS = ('conv2', 'conv1', 'conv0')  # セル順で並べる位置
NODE_COLORS = {'start': 'darkgray', 'station': 'lightgray'}  # ダミーノードの色（TJobは行先作業場で色分け）
EDGE_STYLES = {  # 種類ごとの色・線種・曲がり具合（重なりを避ける）
    'start': ('black', 'solid', 0.0),
    'chain': ('tab:blue', 'solid', 0.0),
    'direct': ('tab:orange', 'dotted', 0.15),
    'precedence': ('tab:green', 'dashed', -0.2),
    'bucket': ('tab:purple', 'solid', 0.3),
    }
STACK_GAP = 0.6  # 同じ列に積み重ねるノードの縦の間隔

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def column_of(env, data) -> float:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # 横方向の位置（作業場側は作業場ごと，上流側は系列・階層ごと）
    width = env.AISLE *(env.FLOOR +1) -1
    position = data.get('position', data['node_type'])
    if position == 'start':
        return width /2
    if position in ('station', 'conv2', 'car'):
        return (data['picker'] +0.5) *width /env.PICKER
    if position in ('conv1', 'lift'):
        return data['aisle'] *(env.FLOOR +1) +(env.FLOOR -1) /2
    if position == 'off_route':
        return width +2
    return data['aisle'] *(env.FLOOR +1) +data['floor']

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def stack_order(data) -> tuple:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # 同じ列の中での並び（コンベヤは下流から，それ以外はPJob順）
    if data['node_type'] != 'tjob':
        return (0,)
    tjob = data['tjob']
    if data['position'] in CONVEYORS:
        return (-tjob.bucket.cell.idx,)
    return (data['pjob'], tjob.idx)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def layout(env, graph) -> tuple[dict, list]:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # 段ごと・列ごとにノードを積み重ねて座標を決める
    groups = {}  # (段, 列) -> ノードのリスト
    for node, data in graph.nodes(data=True):
        position = data.get('position', data['node_type'])
        groups.setdefault((position, column_of(env, data)), []).append(node)
    pos, band_tops, y = {}, [], 0.0
    for band, label in BANDS:
        band_groups = [(key[1], nodes) for key, nodes in groups.items() if key[0] == band]
        depth = max([len(nodes) for _, nodes in band_groups], default=1)
        band_tops.append((y, label))
        for x, nodes in band_groups:
            nodes.sort(key=lambda n: stack_order(graph.nodes[n]))
            for i, node in enumerate(nodes):
                pos[node] = (x, -(y +i *STACK_GAP))
        y += depth *STACK_GAP +1.0
    return pos, band_tops

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def label_of(node, data) -> str:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    if data['node_type'] == 'start':
        return 'S'
    if data['node_type'] == 'station':
        return f"idle\np{data['picker']}"
    return f"{node[1]}-{node[2]}{'*' if data['retry_needed'] else ''}"

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw(env, graph, kinds, highlight_cycles, ax) -> None:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    pos, band_tops = layout(env, graph)
    cmap = matplotlib.colormaps['Set2']  # 閉路の強調（赤枠）と紛れない淡い配色
    colors = [NODE_COLORS.get(d['node_type'], cmap(d.get('picker', 0) %cmap.N)) for _, d in graph.nodes(data=True)]
    # 閉路（サイズ2以上の強連結成分）に含まれるノードを赤枠で強調
    in_cycle = set()
    if highlight_cycles:
        for scc in nx.strongly_connected_components(graph):
            if len(scc) >= 2:
                in_cycle |= scc
    edgecolors = ['red' if n in in_cycle else 'white' for n in graph.nodes]
    nx.draw_networkx_nodes(graph, pos, ax=ax, node_color=colors, edgecolors=edgecolors, linewidths=2, node_size=260)
    nx.draw_networkx_labels(graph, pos, ax=ax, labels={n: label_of(n, d) for n, d in graph.nodes(data=True)}, font_size=5, font_color='black')
    for kind in kinds:  # 種類ごとに単純有向グラフとして描く（MultiDiGraphの重なりを避ける）
        color, style, rad = EDGE_STYLES[kind]
        edges = [(u, v) for u, v, k in graph.edges(keys=True) if k == kind]
        nx.draw_networkx_edges(
            nx.DiGraph(edges), pos, ax=ax, edge_color=color, style=style, width=0.8, alpha=0.7,
            arrowsize=6, node_size=260, connectionstyle=f'arc3,rad={rad}')
    for y, label in band_tops:  # 段の名前
        ax.text(-2, -y, label, ha='right', va='center', fontsize=7)
    handles = [  # 矢印には凡例用のラベルが付かないので，線で凡例を作る
        matplotlib.lines.Line2D([], [], color=EDGE_STYLES[kind][0], linestyle=EDGE_STYLES[kind][1], label=kind)
        for kind in kinds]
    ax.legend(handles=handles, loc='lower right', fontsize=7)
    ax.set_title(f'TJob graph at t = {env.now:.1f}  (nodes: {graph.number_of_nodes()}, edges: {graph.number_of_edges()}, red: in cycle, *: retry)', fontsize=9)
    ax.axis('off')

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    logging.basicConfig(level=logging.WARNING, format='%(message)s')
    parser = argparse.ArgumentParser(description='TJobグラフを段に並べて描画する')
    parser.add_argument('--time', type=float, default=50, help='グラフを構築する時刻')
    parser.add_argument('--pjob', type=int, default=100, help='ピッキングジョブ数')
    parser.add_argument('--seed', type=int, default=1234, help='乱数シード')
    parser.add_argument('--kinds', default='start,chain,direct,precedence,bucket', help='描画するエッジの種類（カンマ区切り）')
    parser.add_argument('--no-cycles', action='store_true', help='閉路の強調表示をしない')
    parser.add_argument('--out', default=None, help='保存先（省略時は data/tjob_graph_t<時刻>.png）')
    parser.add_argument('--show', action='store_true', help='ウィンドウに表示する')
    args = parser.parse_args()
    if not args.show:
        matplotlib.use('Agg')  # 画面表示なしで保存のみ
    import matplotlib.pyplot as plt

    env = create_simulator(params={'SEED': args.seed, 'PJOB': args.pjob})
    env.run(until=args.time)
    graph = TJobGraph(env.gtps).update()

    fig, ax = plt.subplots(figsize=(16, 10))
    draw(env, graph, args.kinds.split(','), not args.no_cycles, ax)
    out = args.out or f'data/tjob_graph_t{args.time:g}.png'
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    fig.savefig(out, dpi=200, bbox_inches='tight')
    print(f'saved: {out}')
    if args.show:
        plt.show()

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
