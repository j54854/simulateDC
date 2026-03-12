import random
from gtp import *

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    params = {
        'SEED': 1234,
        'CONSOLE_OUT': False,  # コンソールへの書き出し
        'PJOB': 10, # ピッキングジョブ数
        'VEHICLE': 30,  # ループの台車数
        'OPENABLE': 5,  # 同時に開封可能なバケット数
        'RELEASABLE': 50,  # GTPシステムに投入可能な搬送ジョブ数
        'REPEATABLE': 3,  # 許容される荷卸し失敗回数（これを超えると置場に戻される）
        }
    env = create_simulator(params=params)
    vis_params = {
        'WIDTH': 1150,
        'HEIGHT': 880,
        'FRAME_RATE': 60,
        'TIME_PER_FRAME': 0.1,
        }
    visualizer = Simulation_Visualizer(env, params=vis_params)
    visualizer.run()

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
