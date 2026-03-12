# -*- coding: utf-8 -*-

import pygame as pg
from .models import *

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Simulation_Visualizer:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    WIDTH = 1150
    HEIGHT = 880
    FRAME_RATE = 60
    TIME_PER_FRAME = 0.5
    BLACK = (0, 0, 0)
    WHITE = (255, 255, 255)
    BUCKET_COL = [(1, 1, 1), (0, 1, 1), (1, 0, 1), (1, 0, 1), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, params):
        self.env = env
        self.NX = env.AISLE *env.FLOOR *2  # 84: 水平方向のセル数
        self.NY = sum(env.CONV_LEN) +env.ROW +5  # 65: 垂直方向のセル数（5 = 待機ノード:1 +リフト:2列 +ループ:2列）
        self.DX = self.WIDTH /(self.NX +1)  # ノード間の水平距離
        self.DY = self.HEIGHT /(self.NY +1)  # ノード間の垂直距離
        for key, value in params.items():
            setattr(self, key, value)
        self.locate_loop_nodes(env.gtps.loop)
        self.locate_store_nodes(env.gtps.stores)
        self.locate_shuttle_nodes(env.gtps.shuttles)
        self.locate_lift_nodes(env.gtps.lifts)
        self.locate_station_nodes(env.gtps.stations)
        self.locate_conveyor_nodes(env.gtps.conveyors)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def locate_loop_nodes(self, loop):
        for seg in range(self.env.LOOP):
            if seg < self.env.LOOP /2:  # 上半分・右向き
                x = ((self.NX -self.env.LOOP /2 -1) /2 +seg +0.5) *self.DX
                y = (self.env.ROW +sum(self.env.CONV_LEN[:2]) +4) *self.DY
            else:  # 下半分・左向き
                x = ((self.NX -self.env.LOOP /2 -1) /2 +(self.env.LOOP -1 -seg) +0.5) *self.DX
                y = (self.env.ROW +sum(self.env.CONV_LEN[:2]) +5) *self.DY
            loop.nodes[seg].pos = (x, y)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def locate_store_nodes(self, stores):
        for aisle in range(self.env.AISLE):
            for floor in range(self.env.FLOOR):
                for row in range(self.env.ROW):
                    x = ((aisle *self.env.FLOOR +floor) *2 +1) *self.DX
                    y = (row +1) *self.DY
                    stores[aisle][floor][row].nodes[0].pos = (x, y)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def locate_shuttle_nodes(self, shuttles):
        for aisle in range(self.env.AISLE):
            for floor in range(self.env.FLOOR):
                for row in range(self.env.ROW +1):
                    x = ((aisle *self.env.FLOOR +floor) *2 +2) *self.DX
                    y = (row +1) *self.DY
                    # 待機ノードのみ，置場とシャトルレールの中間に配置
                    if row == self.env.ROW:
                        x -= 0.5 *self.DX
                    shuttles[aisle][floor].nodes[row].pos = (x, y)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def locate_lift_nodes(self, lifts):
        for aisle in range(self.env.AISLE):
            for floor in range(self.env.FLOOR):
                for direction in range(2):
                    # シャトル台車の待機ノードの位置
                    x, y = self.env.gtps.shuttles[aisle][floor].nodes[self.env.ROW].pos
                    y += (self.env.CONV_LEN[0] +1) *self.DY  # コンベヤ用のスペースを空ける
                    if direction == self.env.FORWARD:
                        x += 0.5 *self.DX  # 出庫用は右にずらす
                    else:
                        x -= 0.5 *self.DX  # 入庫用は左にずらす
                        y += self.DY  # 入庫用は下にずらす
                    lifts[aisle][direction].nodes[floor].pos = (x, y)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def locate_station_nodes(self, stations):
        for picker in range(self.env.PICKER):
            x, y = self.env.gtps.loop.nodes[self.env.PICKER_SEG[picker][0]].pos # 出庫コンベヤの位置
            x -= self.DX  # ステーションノードは出庫コンベヤの左隣
            y += self.env.CONV_LEN[2] *self.DY  # コンベヤ用のスペースを空ける
            stations[picker].nodes[0].pos = (x, y)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def locate_conveyor_nodes(self, conveyors):
        for aisle in range(self.env.AISLE):
            for direction in range(2):
                for floor in range(self.env.FLOOR):
                    for seg in range(self.env.CONV_LEN[0]):
                        # 接続するシャトルの待機ノードの位置
                        x, y = self.env.gtps.shuttles[aisle][floor].nodes[self.env.ROW].pos
                        if direction == self.env.FORWARD:
                            x += 0.5 *self.DX
                            y += (seg +1) *self.DY
                        else:
                            x -= 0.5 *self.DX
                            y += (self.env.CONV_LEN[0] -seg) *self.DY
                        conveyors[0][aisle][floor][direction].nodes[seg].pos = (x, y)
                for seg in range(self.env.CONV_LEN[1]):
                    # 接続するリフトノードの位置
                    lift_x, _ = self.env.gtps.lifts[aisle][direction].nodes[0].pos
                    # 接続するループノードの位置
                    loop_x, y = self.env.gtps.loop.nodes[self.env.AISLE_SEG[aisle][direction]].pos
                    if direction == self.env.FORWARD:
                        x = (loop_x *seg +lift_x *(self.env.CONV_LEN[1] -seg -1)) /(self.env.CONV_LEN[1] -1)
                        y -= (self.env.CONV_LEN[1] -seg) *self.DY
                    else:
                        x = (lift_x *seg +loop_x *(self.env.CONV_LEN[1] -seg -1)) /(self.env.CONV_LEN[1] -1)
                        y -= (seg +1) *self.DY
                    conveyors[1][aisle][0][direction].nodes[seg].pos = (x, y)
        for picker in range(self.env.PICKER):
            for direction in range(2):
                for seg in range(self.env.CONV_LEN[2]):
                    x, y = self.env.gtps.loop.nodes[self.env.PICKER_SEG[picker][direction]].pos
                    if direction == self.env.FORWARD:
                        y += (seg +1) *self.DY
                    else:
                        y += (self.env.CONV_LEN[2] -seg) *self.DY
                    conveyors[2][picker][0][direction].nodes[seg].pos = (x, y)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw_bucket(self, screen, bucket):  # Bucketの描画
        pos = bucket.cell.node.pos
        bucket_col = self.BUCKET_COL[bucket.item %7]
        pg.draw.circle(screen, [col *bucket.item /self.env.ITEM *223 for col in bucket_col], pos, self.DX *0.4)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw_cell(self, screen, cell):  # Cellの描画
        pos = cell.node.pos
        screen.fill(self.WHITE, (pos[0] -self.DX *0.45, pos[1] -self.DY *0.45, self.DX *0.9, self.DY *0.9))
        pg.draw.rect(screen, self.BLACK, (pos[0] -self.DX *0.45, pos[1] -self.DY *0.45, self.DX *0.9, self.DY *0.9), 1)
        if cell.bucket is not None:
            self.draw_bucket(screen, cell.bucket)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw_node(self, screen, node):  # Nodeの描画
        pg.draw.circle(screen, self.BLACK, node.pos, 2)
        if self.to is not None:
            pg.draw.aaline(screen, self.BLACK, node.pos, node.to.pos)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw_unit(self, screen, unit):  # Unitの描画
        # for node in unit.nodes:
        #     self.draw_node(screen, node)
        for cell in unit.cells:
            self.draw_cell(screen, cell)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw_shuttle(self, screen, shuttle):  # Shuttleの描画
        pg.draw.aaline(screen, self.BLACK, shuttle.nodes[0].pos, shuttle.nodes[-2].pos)
        pg.draw.aaline(screen, self.BLACK, shuttle.nodes[-2].pos, shuttle.nodes[-1].pos)
        self.draw_unit(screen, shuttle)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw_conveyor(self, screen, conveyor):  # Conveyorの描画
        pg.draw.aaline(screen, self.BLACK, conveyor.nodes[0].pos, conveyor.nodes[-1].pos)
        self.draw_unit(screen, conveyor)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw_lift(self, screen, lift):  # Liftの描画
        pg.draw.aaline(screen, self.BLACK, lift.nodes[0].pos, lift.nodes[-1].pos)
        self.draw_unit(screen, lift)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw_loop(self, screen, loop):  # Loopの描画
        prev = loop.nodes[-1]
        for node in loop.nodes:
            pg.draw.aaline(screen, self.BLACK, prev.pos, node.pos)
            prev = node
        self.draw_unit(screen, loop)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def draw(self, screen):  # システム全体の描画
        gtps = self.env.gtps
        for aisle in range(self.env.AISLE):
            for floor in range(self.env.FLOOR):
                for row in range(self.env.ROW):
                    self.draw_unit(screen, gtps.stores[aisle][floor][row])
                self.draw_shuttle(screen, gtps.shuttles[aisle][floor])
                for direction in range(2):
                    self.draw_conveyor(screen, gtps.conveyors[0][aisle][floor][direction])
            for direction in range(2):
                self.draw_lift(screen, gtps.lifts[aisle][direction])
                self.draw_conveyor(screen, gtps.conveyors[1][aisle][0][direction])
        self.draw_loop(screen, gtps.loop)
        for picker in range(self.env.PICKER):
            for direction in range(2):
                self.draw_conveyor(screen, gtps.conveyors[2][picker][0][direction])
            self.draw_unit(screen, gtps.stations[picker])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def run(self):  # シミュレーションを動画付きで実行
        # pygameの初期設定
        pg.init()
        clock = pg.time.Clock()
        screen = pg.display.set_mode((self.WIDTH, self.HEIGHT))
        pg.display.set_caption("GTP System Simulator")
        # font = pg.font.SysFont('Arial', 16)  # 文字のフォントを設定

        count = 0  # ループカウンタ
        while True:
            count += 1
            for event in pg.event.get():
                if event.type == pg.QUIT:  # 閉じるボタン押下
                    pg.quit()  # Pygameの終了
                    sys.exit()

            if not self.env.simulation_completed.processed:
                self.env.run(until=count *self.TIME_PER_FRAME)
            else:
                for station in self.env.gtps.stations:
                    print(f'Staion {station.picker}: makespan {station.makespan:.2f} utilization {station.get_utilization():.4f}')
                break

            screen.fill((255, 255, 255))  # 白背景で画面全体を再初期化
            self.draw(screen)  # システムの状態を描画
            pg.display.flip()  # 画面更新
            clock.tick(self.FRAME_RATE)  # 実時間を描画1フレーム分経過させる

