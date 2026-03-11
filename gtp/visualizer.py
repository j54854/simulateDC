# -*- coding: utf-8 -*-

import pygame as pg
from .models import *

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
BUCKET_COL = [(1, 1, 1), (0, 1, 1), (1, 0, 1), (1, 0, 1), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
NX = AISLE *FLOOR *2  # 84: 水平方向のセル数
NY = sum(CONV_LEN) +ROW +5  # 65: 垂直方向のセル数（5 = 待機ノード:1 +リフト:2列 +ループ:2列）

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def locate_store_node(node, aisle, floor, row):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    x = ((aisle *FLOOR +floor) *2 +1) *DX
    y = (row +1) *DY
    node.pos = (x, y)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def locate_shuttle_node(node, aisle, floor, row):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    x = ((aisle *FLOOR +floor) *2 +1) *DX  # 対応する置場ノードの位置
    y = (row +1) *DY
    if row != ROW:
        x += DX  # シャトルノードは置場ノードの右隣
    else:
        x += 0.5 *DX  # 待機ノードは出庫と入庫の中間に配置
    node.pos = (x, y)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def locate_lift_node(node, aisle, floor, direction):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # シャトル台車の待機ノードの位置
    x, y = node.env.gtps.shuttles[aisle][floor].nodes[ROW].pos
    y += (CONV_LEN[0] +1) *DY  # コンベヤ用のスペースを空ける
    if direction == FORWARD:
        x += 0.5 *DX  # 出庫用は右にずらす
    else:
        x -= 0.5 *DX  # 入庫用は左にずらす
        y += DY  # 入庫用は下にずらす
    node.pos = (x, y)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def locate_loop_node(node, seg):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    if seg < LOOP /2:  # 上半分・右向き
        x = ((NX -LOOP /2 -1) /2 +seg +0.5) *DX
        y = (ROW +sum(CONV_LEN[:2]) +4) *DY
    else:  # 下半分・左向き
        x = ((NX -LOOP /2 -1) /2 +(LOOP -1 -seg) +0.5) *DX
        y = (ROW +sum(CONV_LEN[:2]) +5) *DY
    node.pos = (x, y)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def locate_station_node(node, picker):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    x, y = node.env.gtps.loop.nodes[PICKER_SEG[picker][0]].pos # 出庫コンベヤの位置
    x -= DX  # ステーションノードは出庫コンベヤの左隣
    y += CONV_LEN[2] *DY  # コンベヤ用のスペースを空ける
    node.pos = (x, y)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def locate_conveyor_node(node, stage, dim0, dim1, direction, seg):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    if stage == 0:  # シャトル・リフト間のコンベヤ
        # シャトル台車の待機ノードの位置
        aisle, floor = dim0, dim1
        x, y = node.env.gtps.shuttles[aisle][floor].nodes[ROW].pos
        if direction == FORWARD:
            x += 0.5 *DX
            y += (seg +1) *DY
        else:
            x -= 0.5 *DX
            y += (CONV_LEN[0] -seg) *DY
    elif stage == 1:  # リフト・ループ間のコンベヤ
        # 接続元のリフトの0階ノードの位置
        aisle = dim0
        lift_x, _ = node.env.gtps.lifts[aisle][direction].nodes[0].pos
        # 接続するループノードの位置
        loop_x, y = node.env.gtps.loop.nodes[AISLE_SEG[aisle][direction]].pos
        if direction == FORWARD:
            x = (loop_x *seg +lift_x *(CONV_LEN[1] -seg -1)) /(CONV_LEN[1] -1)
            y -= (CONV_LEN[1] -seg) *DY
        else:
            x = (lift_x *seg +loop_x *(CONV_LEN[1] -seg -1)) /(CONV_LEN[1] -1)
            y -= (seg +1) *DY
    else:  # ループ・ステーション間のコンベヤ
        # 接続するループノードの位置
        picker = dim0
        x, y = node.env.gtps.loop.nodes[PICKER_SEG[picker][direction]].pos
        if direction == FORWARD:
            y += (seg +1) *DY
        else:
            y += (CONV_LEN[2] -seg) *DY
    node.pos = (x, y)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def set_node_positions(gtps, width, height):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    global W, H, DX, DY
    W = width
    H = height
    DX = W /(NX +1)  # ノード間の水平距離
    DY = H /(NY +1)  # ノード間の垂直距離
    for seg in range(LOOP):
        locate_loop_node(gtps.loop.nodes[seg], seg)
    for aisle in range(AISLE):
        for floor in range(FLOOR):
            for row in range(ROW):
                locate_store_node(gtps.stores[aisle][floor][row].nodes[0], aisle, floor, row)
                locate_shuttle_node(gtps.shuttles[aisle][floor].nodes[row], aisle, floor, row)
            locate_shuttle_node(gtps.shuttles[aisle][floor].nodes[ROW], aisle, floor, ROW)
            for direction in range(2):
                locate_lift_node(gtps.lifts[aisle][direction].nodes[floor], aisle, floor, direction)
                for seg in range(CONV_LEN[0]):
                    locate_conveyor_node(gtps.conveyors[0][aisle][floor][direction].nodes[seg], 0, aisle, floor, direction, seg)
        for direction in range(2):
            for seg in range(CONV_LEN[1]):
                locate_conveyor_node(gtps.conveyors[1][aisle][0][direction].nodes[seg], 1, aisle, 0, direction, seg)
    for picker in range(PICKER):
        locate_station_node(gtps.stations[picker].nodes[0], picker)
        for direction in range(2):
            for seg in range(CONV_LEN[2]):
                locate_conveyor_node(gtps.conveyors[2][picker][0][direction].nodes[seg], 2, picker, 0, direction, seg)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_bucket(screen, bucket):  # Bucketの描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    pos = bucket.cell.node.pos
    bucket_col = BUCKET_COL[bucket.item %7]
    pg.draw.circle(screen, [col *bucket.item /ITEM *223 for col in bucket_col], pos, DX *0.4)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_cell(screen, cell):  # Cellの描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    pos = cell.node.pos
    screen.fill(WHITE, (pos[0] -DX *0.45, pos[1] -DY *0.45, DX *0.9, DY *0.9))
    pg.draw.rect(screen, BLACK, (pos[0] -DX *0.45, pos[1] -DY *0.45, DX *0.9, DY *0.9), 1)
    if cell.bucket is not None:
        draw_bucket(screen, cell.bucket)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_node(screen, node):  # Nodeの描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    pg.draw.circle(screen, BLACK, node.pos, 2)
    if self.to is not None:
        pg.draw.aaline(screen, BLACK, node.pos, node.to.pos)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_unit(screen, unit):  # Unitの描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    # for node in unit.nodes:
    #     node.draw(screen)
    for cell in unit.cells:
        draw_cell(screen, cell)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_shuttle(screen, shuttle):  # Shuttleの描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    pg.draw.aaline(screen, BLACK, shuttle.nodes[0].pos, shuttle.nodes[-2].pos)
    pg.draw.aaline(screen, BLACK, shuttle.nodes[-2].pos, shuttle.nodes[-1].pos)
    draw_unit(screen, shuttle)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_conveyor(screen, conveyor):  # Conveyorの描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    pg.draw.aaline(screen, BLACK, conveyor.nodes[0].pos, conveyor.nodes[-1].pos)
    draw_unit(screen, conveyor)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_lift(screen, lift):  # Liftの描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    pg.draw.aaline(screen, BLACK, lift.nodes[0].pos, lift.nodes[-1].pos)
    draw_unit(screen, lift)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_loop(screen, loop):  # Loopの描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    prev = loop.nodes[-1]
    for node in loop.nodes:
        pg.draw.aaline(screen, BLACK, prev.pos, node.pos)
        prev = node
    draw_unit(screen, loop)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def draw_gtps(screen, gtps):  # GTPシステム全体の描画
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    for aisle in range(AISLE):
        for floor in range(FLOOR):
            for row in range(ROW):
                draw_unit(screen, gtps.stores[aisle][floor][row])
            draw_shuttle(screen, gtps.shuttles[aisle][floor])
            draw_conveyor(screen, gtps.conveyors[0][aisle][floor][0])
            draw_conveyor(screen, gtps.conveyors[0][aisle][floor][1])
        draw_lift(screen, gtps.lifts[aisle][0])
        draw_lift(screen, gtps.lifts[aisle][1])
        draw_conveyor(screen, gtps.conveyors[1][aisle][0][0])
        draw_conveyor(screen, gtps.conveyors[1][aisle][0][1])
    draw_loop(screen, gtps.loop)
    for picker in range(PICKER):
        draw_conveyor(screen, gtps.conveyors[2][picker][0][0])
        draw_conveyor(screen, gtps.conveyors[2][picker][0][1])
        draw_unit(screen, gtps.stations[picker])

