# -*- coding: utf-8 -*-

import sys

# 基本設定
SEED = 123
WIDTH = 1150
HEIGHT = 880
FRAME_RATE = 60
TIME_PER_FRAME = 0.5
PRINT_CONSOLE = True  # コンソールへの書き出し

# 実験パラメータ
PJOB = 10 # ピッキングジョブ数
OPENABLE = 5  # 同時に開封可能なバケット数の上限
RELEASABLE_UNPICKED = sys.maxsize # GTPシステムに投入可能な未作業の搬送ジョブ数の上限
RELEASABLE_UNRESTORED = 50  # GTPシステムに投入可能な未帰還の搬送ジョブ数の上限
FAILURE_LIMIT = 3  # ループでの荷卸し失敗許容階数（これを超えると置場に戻される）

# 運用ルール
# デフォルト（従来法）：投入時の作業場への割付けはラウンドロビン，シャトル・リフトは，ピッキングジョブの発生順（同ピッキングジョブ内はアイテム番号順），ループは，コンベヤの先頭セルへの到着順（ただし，同作業場に届ける出庫ジョブには順序制約を課す）
DEFAULT = 0
PROPOSED = 1
RELEASE_RULE = 1  # ピッキングジョブ投入ルール
DISPATCH_RULE = 1  # 各ユニットのディスパッチングルール

# システムの構成と運用のパラメータ
ITEM = 100  # アイテム種類数
AISLE = 7  # 倉庫の列数
FLOOR = 6  # 倉庫の階数
ROW = 45  # 各列の置場数（上流から順に 0, 1, 2, ... で，ROW番目は台車の待機ノード）
# 左右の置場を明示的に考慮したい場合は，右左右左・・・に並んでいるとして，走行時間をt0t0t0tとすればよい
PICKER = 5  # ビッキング作業場数
VEHICLE = 30  # ループの台車数
LOOP = 100  # ループのノード数
CONV_LEN = [3, 5, 7]  # 各ステージ（上流から順に 0, 1, 2）のコンベヤ容量
CONV_DIM = [(AISLE, AISLE, PICKER), (FLOOR, 1, 1)]  # 1: AISLE/PICKER, 2: FLOOR/None

FORWARD = 0  # 出庫方向
BACKWARD = 1  # 入庫方向

# ループに自動倉庫を接続するノード (FORWARD, BACKWARD) --i-o--i-o--
AISLE_SEG = [(10, 8), (15, 13), (20, 18), (25, 23), (30, 28), (35, 33), (40, 38)]
# ループにビッキング作業場を接続するノード (FORWARD, BACKWARD) --i-o--i-o--
PICKER_SEG = [(63, 65), (68, 70), (73, 75), (78, 80), (83, 85)]

MT_SHTL = 0.16  # シャトル台車のノード間移動時間
LT_SHTL = 5  # シャトル台車との間の積替え時間
MT_LIFT = 0.25  # リフト台車のノード間移動時間
LT_LIFT = 5  # リフト台車との間の積替え時間
MT_LOOP = 0.28  # ループ台車のノード間移動時間
LT_LOOP = 1  # ループ台車との間の積替え時間
MT_CONV = 1.2  # コンベヤ上のバケットのノード間移動時間（作業場への出入りも）
T_PICK = [5.6, 8.4, 20, 30, 40, 90, 180]  # ピンキング作業時間
P_PICK = [0.4, 0.4, 0.04, 0.09, 0.04, 0.02, 0.01]  # とその確率分布

# 搬送ジョブの進捗
TO_FW_SHTL = 0  # 往路シャトル台車からの降車終了前（台車が空車なら全て置場で待機中）
TO_FW_LIFT = 1  # 往路リフト台車からの降車終了前（台車が空車なら全て往路リフト前コンベヤ上）
TO_FW_LOOP = 2  # ループ台車への乗車開始前（往路ループ前コンベヤ上）
ON_FW_LOOP = 3  # ループ台車からの降車終了前（台車を拘束している状態）
TO_PICKED = 4  # ピッキング作業終了前（作業場または作業場前コンベヤ上）
TO_BW_LOOP = 5  # ループ台車への乗車開始前（作業場または復路ループ前コンベヤ上）
ON_BW_LOOP = 6  # ループ台車からの降車終了前（台車を拘束している状態）
TO_BW_LIFT = 7  # 復路リフト台車からの降車終了前（台車が空車なら全て復路リフト前コンベヤ上）
TO_BW_SHTL = 8  # 復路シャトル台車からの降車終了前（台車が空車なら全て復路シャトル前コンベヤ上）
DONE = 9  # 置場への帰還以後（搬送ジョブ完了状態）

# 描画関連
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
BUCKET_COL = [(1, 1, 1), (0, 1, 1), (1, 0, 1), (1, 0, 1), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
NX = AISLE *FLOOR *2  # 84: 水平方向のセル数
NY = sum(CONV_LEN) +ROW +5  # 65: 垂直方向のセル数（5 = 待機ノード:1 +リフト:2列 +ループ:2列）
DX = WIDTH /(NX +1)  # ノード間の水平距離
DY = HEIGHT /(NY +1)  # ノード間の垂直距離

def get_store_node_position(aisle, floor, row):
    x = ((aisle *FLOOR +floor) *2 +1) *DX
    y = (row +1) *DY
    return x, y

def get_shuttle_node_position(aisle, floor, row):
    x, y = get_store_node_position(aisle, floor, row)  # 対応する置場ノードの位置
    if row != ROW:
        x += DX  # シャトルノードは置場ノードの右隣
    else:
        x += 0.5 *DX  # 待機ノードは出庫と入庫の中間に配置
    return x, y

def get_conveyor_node_position(stage, dim0, dim1, direction, seg):
    if stage == 0:  # シャトル・リフト間のコンベヤ
        # シャトル台車の待機ノードの位置
        aisle, floor = dim0, dim1
        x, y = get_shuttle_node_position(aisle, floor, ROW)
        if direction == FORWARD:
            x += 0.5 *DX
            y += (seg +1) *DY
        else:
            x -= 0.5 *DX
            y += (CONV_LEN[0] -seg) *DY
    elif stage == 1:  # リフト・ループ間のコンベヤ
        # 接続元のリフトの0階ノードの位置
        aisle = dim0
        lift_x, _ = get_lift_node_position(aisle, 0, direction)
        # 接続するループノードの位置
        loop_x, y = get_loop_node_position(AISLE_SEG[aisle][direction])
        if direction == FORWARD:
            x = (loop_x *seg +lift_x *(CONV_LEN[1] -seg -1)) /(CONV_LEN[1] -1)
            y -= (CONV_LEN[1] -seg) *DY
        else:
            x = (lift_x *seg +loop_x *(CONV_LEN[1] -seg -1)) /(CONV_LEN[1] -1)
            y -= (seg +1) *DY
    else:  # ループ・ステーション間のコンベヤ
        # 接続するループノードの位置
        picker = dim0
        x, y = get_loop_node_position(PICKER_SEG[picker][direction])
        if direction == FORWARD:
            y += (seg +1) *DY
        else:
            y += (CONV_LEN[2] -seg) *DY
    return x, y

def get_lift_node_position(aisle, floor, direction):
    # シャトル台車の待機ノードの位置
    x, y = get_shuttle_node_position(aisle, floor, ROW)
    y += (CONV_LEN[0] +1) *DY  # コンベヤ用のスペースを空ける
    if direction == FORWARD:
        x += 0.5 *DX  # 出庫用は右にずらす
    else:
        x -= 0.5 *DX  # 入庫用は左にずらす
        y += DY  # 入庫用は下にずらす
    return x, y

def get_loop_node_position(seg):
    if seg < LOOP /2:  # 上半分・右向き
        x = ((NX -LOOP /2 -1) /2 +seg +0.5) *DX
        y = (ROW +sum(CONV_LEN[:2]) +4) *DY
    else:  # 下半分・左向き
        x = ((NX -LOOP /2 -1) /2 +(LOOP -1 -seg) +0.5) *DX
        y = (ROW +sum(CONV_LEN[:2]) +5) *DY
    return x, y

def get_station_node_position(piker):
    x, y = get_loop_node_position(PICKER_SEG[piker][0]) # 出庫コンベヤの位置
    x -= DX  # ステーションノードは出庫コンベヤの左隣
    y += CONV_LEN[2] *DY  # コンベヤ用のスペースを空ける
    return x, y
