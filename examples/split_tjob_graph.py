import logging
from collections import Counter
import networkx as nx
from gtp import *

# 【検討中の案の試作】ループより上流の TJob を到着ノードと作業ノードに分け，閉路がなくなるかを確かめる
# 案の内容は docs/tjob_graph_v1.md の「後回しにした項目」を参照．エッジの長さは考慮せず，構造だけを見る
UPSTREAM = {'conv1', 'lift', 'conv0', 'shuttle', 'store', 'beyond_store'}  # 分割する位置
PARAMS_LIST = [
    {'PJOB': 50, 'SEED': 42},
    {'PJOB': 50, 'SEED': 1, 'OPENABLE': 1},  # バケットの共有（off_route・bucket エッジ）を起こす
    {'PJOB': 100, 'SEED': 1234},
    ]

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def split(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # ループより上流の TJob を「到着」と「作業」に分けたグラフを作る
    def pick(n):  # 作業ノード（分割しないノードはこれだけを持つ）
        return ('pick', n)
    def arr(n):  # 到着ノード（ループより上流の TJob のみ）
        return ('arr', n) if graph.nodes[n].get('position') in UPSTREAM else pick(n)
    split_graph = nx.MultiDiGraph()
    for n in graph.nodes:
        split_graph.add_node(pick(n))
        if arr(n) != pick(n):
            split_graph.add_edge(arr(n), pick(n), key='arrive')  # 到着してから作業できる
    for u, v, kind in graph.edges(keys=True):
        if kind == 'aisle':  # 系列上の前後関係は到着どうしの関係（作業順は追い越しで入れ替わりうる）
            split_graph.add_edge(arr(u), arr(v), key=kind)
        else:  # station・precedence・bucket・start は作業どうしの関係
            split_graph.add_edge(pick(u), pick(v), key=kind)
    return split_graph

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def count_cycles(params: dict) -> Counter:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # 時間1ごとにグラフを構築し，分割前後で閉路のある時点を数える
    env = create_simulator(params=params)
    tjob_graph = TJobGraph(env.gtps)
    stats = Counter()

    def sampler(env):
        while True:
            yield env.timeout(1)
            graph = tjob_graph.update()
            stats['snapshots'] += 1
            stats['original cyclic'] += not nx.is_directed_acyclic_graph(graph)
            stats['split cyclic'] += not nx.is_directed_acyclic_graph(split(graph))

    env.process(sampler(env))
    env.run(until=env.simulation_completed)
    return stats

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    logging.basicConfig(level=logging.WARNING, format='%(message)s')
    for params in PARAMS_LIST:
        print(params, dict(count_cycles(params)))

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
