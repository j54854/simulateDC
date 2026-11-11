import sys
import logging
from collections import Counter
import networkx as nx
from gtp import *

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def describe(node, data):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # ノードの簡易表示（TJobなら位置・行先作業場・PJob番号）
    if data['node_type'] != 'tjob':
        return str(node)
    return f"{node[1]}-{node[2]}({data['position']}, p{data['picker']}{', retry' if data['retry_needed'] else ''})"

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    logging.basicConfig(level=logging.WARNING, format='%(message)s')
    until = float(sys.argv[1]) if len(sys.argv) > 1 else 50  # グラフを構築する時刻

    params = {
        'SEED': 1234,
        'PJOB': 100, # ピッキングジョブ数
        }
    env = create_simulator(params=params)
    env.run(until=until)
    graph = TJobGraph(env.gtps).update()

    print(f'time: {env.now}')
    print(f'nodes: {graph.number_of_nodes()}, edges: {graph.number_of_edges()}')
    positions = Counter(d.get('position', d['node_type']) for _, d in graph.nodes(data=True))
    print('nodes by position:', dict(positions))
    kinds = Counter(d['kind'] for _, _, d in graph.edges(data=True))
    print('edges by kind:', dict(kinds))

    # 閉路の現れ方（サイズ2以上の強連結成分）
    sccs = [scc for scc in nx.strongly_connected_components(graph) if len(scc) >= 2]
    print(f'strongly connected components (size >= 2): {len(sccs)}')
    for scc in sccs:
        print(' ', sorted(describe(n, graph.nodes[n]) for n in scc))
        sub = graph.subgraph(scc)
        print('    edges by kind:', dict(Counter(d['kind'] for _, _, d in sub.edges(data=True))))

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
