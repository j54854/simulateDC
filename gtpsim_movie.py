import sys, random
import simpy
import pygame as pg
from gtp import *

SEED = 123
WIDTH = 1150
HEIGHT = 880
FRAME_RATE = 60
TIME_PER_FRAME = 0.5

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class MyConfig(DefaultConfig):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    CONSOLE_OUT = False  # コンソールへの書き出し

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    random.seed(SEED)

    # simpyの環境設定
    env = create_simulator(config=MyConfig)
    set_node_positions(env.gtps, WIDTH, HEIGHT)

    # pygameの初期設定
    pg.init()
    clock = pg.time.Clock()
    screen = pg.display.set_mode((WIDTH, HEIGHT))
    pg.display.set_caption("GTP System Simulator")
    # font = pg.font.SysFont('Arial', 16)  # 文字のフォントを設定

    count = 0  # ループカウンタ
    while True:
        count += 1
        for event in pg.event.get():
            if event.type == pg.QUIT:  # 閉じるボタン押下
                pg.quit()  # Pygameの終了
                sys.exit()

        if not env.gameover.processed:
            env.run(until=count *TIME_PER_FRAME)
        else:
            for station in env.gtps.stations:
                print(f'Staion {station.picker}: makespan {station.makespan:.2f} utilization {station.get_utilization():.4f}')
            break

        screen.fill((255, 255, 255))  # 白背景で画面全体を再初期化
        draw_gtps(screen, env.gtps)  # システムの状態を描画
        pg.display.flip()  # 画面更新
        clock.tick(FRAME_RATE)  # 実時間を描画1フレーム分経過させる

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
