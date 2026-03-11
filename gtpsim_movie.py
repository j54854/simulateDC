import sys, random
import simpy
import pygame as pg
from gtp import *

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    random.seed(util.SEED)

    # simpyの環境設定
    env = set_simpy()

    # pygameの初期設定
    pg.init()
    clock = pg.time.Clock()
    screen = pg.display.set_mode((util.WIDTH, util.HEIGHT))
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
            env.run(until=count *util.TIME_PER_FRAME)
        else:
            for station in env.gtps.stations:
                print(f'Staion {station.picker}: makespan {station.makespan:.2f} utilization {station.get_utilization():.4f}')
            break

        screen.fill((255, 255, 255))  # 白背景で画面全体を再初期化
        env.gtps.draw(screen)  # システムの状態を描画
        pg.display.flip()  # 画面更新
        clock.tick(util.FRAME_RATE)  # 実時間を描画1フレーム分経過させる

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
