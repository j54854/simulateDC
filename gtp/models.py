# -*- coding: utf-8 -*-

# TODO
# ログをcsvに書き出すメソッドを追加

import sys, random
# import simpy

ITEM = 100  # アイテム種類数
AISLE = 7  # 倉庫の列数
FLOOR = 6  # 倉庫の階数
ROW = 45  # 各列の置場数（上流から順に 0, 1, 2, ... で，ROW番目は台車の待機ノード）
PICKER = 5  # ビッキング作業場数
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

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class PJob:  # ピッキングジョブ
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, idx):
        self.env = env
        self.idx = idx
        # アイテムの種類数（1〜3個から指定の確率分布に従ってランダムに選択）
        self.num = random.choices((1, 2, 3), weights=(0.7, 0.2, 0.1))[0]
        # 要求内容（アイテム番号をキー，必要量を値とした辞書として保持する）
        self.reqs = self.get_requirements()
        self.station = None  # ピッキング作業場
        self.tjobs = []  # 搬送ジョブのリスト
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __repr__(self):
        return f'Pjob {self.idx}: {{station: {self.station.picker if self.station is not None else 'unassigned'}, tjobs: {len(self.tjobs)}, reqs: {self.reqs}}}'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_requirements(self):  # 要求の辞書を作成
        # 所定の数のアイテム番号をランダムに決定
        keys = random.sample(range(ITEM), self.num)
        # アイテム数に応じて必要量を決定（どのアイテムも同量としている）
        if self.num == 1:
            vol = random.choice((10, 20, 30, 40, 50, 60, 70, 80))
        elif self.num == 2:
            vol = random.choice((10, 20, 30, 40))
        elif self.num == 3:
            vol = random.choice((10, 20, 30))
        else:
            assert False, 'Number of items should be in {1, 2, 3}'
        return {key: value for key, value in zip(keys, [vol] *self.num)}
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def release_tjob(self, tjob):
        self.tjobs.append(tjob)  # ピッキングジョブの搬送ジョブリストに追加
        tjob.bucket.assign(tjob)  # バケットの搬送ジョブリストリストに追加
        tjob.bucket.home_store.get_shuttle().register(tjob)  # シャトルの搬送ジョブリストに追加
        if self.env.CONSOLE_OUT:
            print(f'Tjob {self.idx}-{tjob.idx} {{item: {tjob.bucket.item}, vol: {tjob.vol}, picker: {self.station.picker}, bucket: {tjob.bucket.get_store_address()}}} is released at {self.env.now:.2f}')
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def release_pjob(self, station):
        self.station = station  # （外部から）指定された作業場を登録
        idx = 0  # 搬送ジョブのインデックス
        for item, vol in self.reqs.items():  # 全ての要求について
            while vol > 0:  # 必要量が未達なら
                # リストの先頭から順に開封可能な数のバケットを調べる
                candidates = self.env.gtps.buckets[item][:self.env.OPENABLE]
                target = candidates[0]
                for candidate in candidates:  # 最も優先すべきバケットを選択
                    if candidate.priority() < target.priority():
                        target = candidate
                supplied = min(vol, target.get_balance())  # 取得可能量を計算
                vol -= supplied  # 取得後の必要量の残量を計算
                tjob = TJob(self.env, self, idx, target, supplied)  # 搬送ジョブを作成
                self.release_tjob(tjob)
                idx += 1

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class TJob:  # 搬送ジョブ
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, pjob, idx, bucket, vol):
        self.env = env
        self.pjob = pjob
        self.idx = idx
        self.bucket = bucket
        self.vol = vol
        # 状態変数
        self.progress = TO_FW_SHTL  # 初期値（往路シャトル台車からの降車前）
        self.retry_needed = False  # （作業場への搬送に失敗し）再搬送が必要か？
        # 出力変数
        self.released_time = env.now  # 投入時刻
        self.work_time = None  # ピッキング作業時間
        self.picked_time = None  # ピッキング完了時刻
        self.restored_time = None  # 搬送ジョブ完了（置場への帰還）時刻
        self.retry_count = 0  # 再搬送回数
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __repr__(self):
        return f"Tjob {self.pjob.idx}-{self.idx}: {{item: {self.bucket.item}, vol: {self.vol}, store: {self.bucket.get_store_address()}, progress: {self.progress}, picked: {self.is_picked()}}}"
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_picked(self):
        return self.picked_time is not None  # ピッキングは完了済みか？
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_preceding_tjobs(self):
        its_pjob_idx = self.pjob.idx  # ピッキングジョブのインデックス
        its_station = self.pjob.station  # ピッキングジョブの作業場ユニット
        its_aisle = self.bucket.home_store.aisle  # バケットの置場の系列番号
        its_floor = self.bucket.home_store.floor  # バケットの階層の階層番号
        # 作業場が同じで，先行すべきピッキングジョブを抽出
        pjobs_ahead = [pjob for pjob in self.env.gtps.pjobs if pjob.idx < its_pjob_idx and pjob.station == its_station]
        # それらに対応する搬送ジョブを抽出
        tjobs_ahead = [tjob for pjob in pjobs_ahead for tjob in pjob.tjobs]
        # シャトルの手前なら，同じ系列，同じ階層のものに絞る
        if self.progress == TO_FW_SHTL:
            tjobs_ahead = [tjob for tjob in tjobs_ahead if tjob.bucket.home_store.aisle == its_aisle and tjob.bucket.home_store.floor == its_floor]
        # リフトの手前なら，同じ系列，異なる階層のものに絞る（同階層のものはこの時点ではどうしようもない）
        if self.progress == TO_FW_LIFT:
            tjobs_ahead = [tjob for tjob in tjobs_ahead if tjob.bucket.home_store.aisle == its_aisle and tjob.bucket.home_store.floor != its_floor]
        return tjobs_ahead  # （先行関係チェックの対象となる）先行搬送ジョブのリスト
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def late_preceding_tjob_count(self):
        preceding_tjobs = self.get_preceding_tjobs()  # 先行搬送ジョブリスト
        # 先行の判定（再搬送に向かうものは前にいても先行しているとは判断しないこと）
        is_ahead = [tjob.progress > self.progress and not tjob.retry_needed for tjob in preceding_tjobs]
        return len(preceding_tjobs) -sum(is_ahead)  # 先行していない先行搬送ジョブ数
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_all_restored(self):  # 全ピッキングジョブが完了したか？
        return self.env.gtps.is_all_released and len(self.env.gtps.unrestored_tjobs) <= 0
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def proceed(self, to=None, work_time=None):  # 進捗段階を進める
        if self.progress == TO_PICKED:  # ピッキング作業前から進捗が進むと
            self.picked_time = self.env.now
            self.work_time = work_time
        if to is not None:  # 次の段階が指定されている場合
            self.progress = to
        else:  # 通常は1段階ずつ進める
            self.progress += 1
        if self.progress == DONE:  # 搬送ジョブが完了したら
            if self.retry_needed:  # 再搬送が必要なら
                self.retry_count += 1  # 再搬送回数を増やす
                self.retry_needed = False  # 再搬送フラグを初期値に戻す
                self.progress = TO_FW_SHTL  # 進捗を初期値に戻す
                self.bucket.home_store.get_shuttle().register(self)  # シャトルの搬送ジョブリストに再追加
            else:
                self.restored_time = self.env.now  # 搬送ジョブ完了時刻を登録
                self.env.tjob_restored.succeed()  # 置場への帰還イベントを発火
                self.env.tjob_restored = self.env.event()
                self.env.gtps.unrestored_tjobs.remove(self)
                if self.is_all_restored():  # 全ピッキングジョブが完了したらシミュレーション終了
                    self.env.gameover.succeed()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_goal_node_idx(self):  # ループ台車の行先ノード
        aisle = self.bucket.home_store.aisle  # 系列番号
        picker = self.pjob.station.picker  # 作業場番号
        if self.progress == TO_FW_LOOP:  # 出庫でループに入る
            return AISLE_SEG[aisle][FORWARD]
        elif self.progress == ON_FW_LOOP:  # 出庫でループから出る
            return PICKER_SEG[picker][FORWARD]
        elif self.progress == TO_BW_LOOP:  # 入庫でループに入る
            return PICKER_SEG[picker][BACKWARD]
        elif self.progress == ON_BW_LOOP:  # 入庫でループから出る
            return AISLE_SEG[aisle][BACKWARD]
        else:
            assert False, 'Loop job has inconsistent progress status'

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Bucket:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, idx, item):
        self.env = env
        self.idx = idx
        self.item = item  # アイテム番号
        self.home_store = None  # 割り当てられた置場ユニット
        self.cell = None  # 所在セル（双方向参照）
        self.tjobs = []  # 搬送ジョブリスト
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __repr__(self):
        return f'Bucket {self.idx}: {{item: {self.item}, store: {self.get_store_address()}, tjob: {self.current_tjob()}, cell: {self.cell if self.cell is not None else 'unattached'}}}'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_store_address(self):  # 置場の系列・階層・段階を返す（__repr__用）
        if self.home_store is not None:
            return self.home_store.aisle, self.home_store.floor, self.home_store.row
        else:
            return 'unassigned'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def undone_tjobs(self):  # 未完了の搬送ジョブリスト
        return [tjob for tjob in self.tjobs if tjob.progress < DONE]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def picked_tjobs(self):  # ピッキング済みの搬送ジョブリスト
        return [tjob for tjob in self.tjobs if tjob.progress > TO_PICKED and not tjob.retry_needed]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def current_tjob(self):  # 実行（しようと）している搬送ジョブ
        return self.undone_tjobs()[0] if len(self.undone_tjobs()) > 0 else None
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_fullness(self):  # 実残量 [0, 100]
        fullness = 100
        for tjob in self.picked_tjobs():
            fullness -= tjob.vol
        return fullness
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_balance(self):  # 正味残量（割付け可能量） [0, 100]
        balance = 100
        for tjob in self.tjobs:
            balance -= tjob.vol
        return balance
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_at_home(self):  # 置場に格納されているか？
        return self.cell.unit == self.home_store
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def priority(self):  # 搬送ジョブ割付けの優先度（小さいほど優先）
        # 未完了の各搬送ジョブ数に比例したペナルティ
        priority = DONE *len(self.undone_tjobs())
        # 実行中の搬送ジョブがある場合，その進捗に応じてペナルティを調整
        if self.current_tjob() is not None:
            if self.is_at_home():  # 置場で出発を待っている場合
                priority += 0.5
            elif self.current_tjob().retry_needed:  # 再搬送に向けて帰還中の場合
                priority += 1
            else:  # 途中まで進んでいる場合
                priority -= self.current_tjob().progress
        return priority
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_assignable(self, tjob):  # 搬送ジョブの割付け可能性
        return self.get_balance() >= tjob.vol
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def assign(self, tjob):  # 搬送ジョブの割付け
        assert self.is_assignable(tjob), 'This transfer job is too large'
        self.tjobs.append(tjob)  # 搬送ジョブリストに追加
        # 正味残量が0になったら，割付け可能なバケットリストから削除
        if self.get_balance() <= 0:
            self.env.gtps.buckets[self.item].remove(self)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def pick(self, tjob):  # ピッキング処理と入庫の必要性確認
        assert tjob == self.current_tjob(), 'Not current tjob'
        if self.get_fullness() <= 0:  # バケットが空なら
            return_needed = False  # 新しいバケットに置き換えるので，入庫は不要
            tjob.proceed(to=DONE)  # 搬送ジョブはここで完了（TO_BW_LOOP > DONE）
            self.tjobs = []  # 登録されていた搬送ジョブを全てクリア
            self.cell.bucket = None  # 出口コンベヤに搬出せずに作業場から取り除く
            self.home_store.cells[0].receive(self)  # 新バケットとして置場に格納
            self.env.gtps.buckets[self.item].append(self)  # 割付け可能なバケットリストに復帰
        else:
            return_needed = True
        self.env.tjob_picked.succeed()  # ピッキング作業終了イベントを発火
        self.env.tjob_picked = self.env.event()
        self.env.gtps.unpicked_tjobs.remove(tjob)
        return return_needed

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Cell:  # バケットを保持するセル（台車，置場，作業場，コンベヤセグメント）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, unit, idx):
        self.env = env
        self.unit = unit  # ユニットへのポインタ
        self.idx = idx # セル番号
        self.node = None  # 対応するノード（双方向参照）
        self.bucket = None  # 保持しているバケット（双方向参照）
        self.bucket_receipt = env.event()
        self.bucket_sent = env.event()
        if isinstance(self.unit, Loop):  # ループ（を継承しているクラス）の台車なら
            self.goal = None  # 行き先ノード
            self.tjob = None  # 実行中の搬送ジョブ
            self.is_pushed = False  # 後続台車からの押出しフラグ
            self.goal_assigned = env.event()  # 行き先ノード設定イベント
            self.tjob_completed = env.event()  # 搬送ジョブ完了イベント
            self.pushed = env.event()  # 後続台車からの押出しイベント
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __repr__(self):
        if isinstance(self.unit, Loop):
            return f'{self.unit.__class__.__name__}-cell {self.idx}: {{node: {self.node.idx}, goal: {'unset' if self.goal is None else self.goal.node.idx}, tjob: {'unassigned' if self.tjob is None else self.tjob}, bucket: {self.bucket if self.bucket is not None else 'none'}}}'
        else:
            return f'{self.unit.__class__.__name__}-cell {self.idx}: {{node: {self.node.idx}, bucket: {self.bucket if self.bucket is not None else 'none'}}}'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def attach(self, node):  # 指定されたノードに貼り付く
        assert node.is_open(), 'Cannot attach 2 or more cells'
        self.node = node
        self.node.cell = self  # ノードとセルは双方向参照可能
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def move_to(self, next_node):  # 指定されたノードに移る
        assert next_node is not None, 'Invalid move destination: None'
        node_to_free = self.node  # 解放するノードを退避させる
        self.node.cell = None  # 現ノードから当該セルを取り外す
        self.attach(next_node)  # 次ノードを当該セルに取り付ける
        if isinstance(self.unit, Loop):  # ループの台車なら
            node_to_free.unblocked.succeed()  # ノード解放イベントの発火
            node_to_free.unblocked = self.env.event()
            self.is_pushed = False  # 移動したら押出しフラグはもとに戻す
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def move_up(self):  # 次のインデックスのノードに移動
        self.move_to(self.node.get_upper())
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def move_down(self):  # 前のインデックスのノードに移動
        self.move_to(self.node.get_lower())
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_open(self):  # 当該セルが空いているか？
        return self.bucket is None
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def receive(self, bucket):  # バケットを受け取る
        assert self.is_open(), 'Cannot receive 2 or more buckets'
        assert bucket is not None, 'No bucket to receive'
        self.bucket = bucket
        bucket.cell = self  # バケットとセルは双方向参照
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def transfer(self):  # バケットを次のユニットに積み替える
        assert self.node.to is not None, 'No connected node'
        receiver_cell = self.node.to.cell
        assert receiver_cell is not None, 'No receiver cell to transfer'
        receiver_cell.receive(self.bucket)  # 受け手側の処理
        self.bucket = None  # 送り手側の処理
        # 受領イベントと送出イベントを発火させる
        self.bucket_sent.succeed()
        self.bucket_sent = self.env.event()
        receiver_cell.bucket_receipt.succeed()
        receiver_cell.bucket_receipt = self.env.event()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def assign_goal(self, goal_node):  # 行き先ノードの設定（ループ台車のみ）
        self.goal = goal_node
        self.goal_assigned.succeed()  # 行き先ノード設定イベントの発火
        self.goal_assigned = self.env.event()
        self.is_pushed = False
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def assign_tjob(self, tjob):  # 搬送ジョブの割付け（ループ台車のみ）
        self.tjob = tjob
        # 新しく搬送ジョブが割り付けられたら，それに応じて行き先ノードを設定
        self.assign_goal(self.unit.nodes[tjob.get_goal_node_idx()])

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Node:  # ユニットの経路グラフのノード
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, unit, idx):
        self.env = env
        self.unit = unit  # ユニットへのポインタ
        self.idx = idx  # ノード番号
        self.to = None  # バケットを送り出せる接続先ノード
        self.ot = None  # toの逆参照
        self.cell = None  # 対応するセル（なければNone）
        if isinstance(self.unit, Loop):  # ループ（を継承しているクラス）のノードなら
            self.unblocked = env.event()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __repr__(self):
        return f'{self.unit.__class__.__name__}-node {self.idx}: {{to: {self.to.unit.__class__.__name__ if self.to is not None else 'unconnected'}, from: {self.ot.unit.__class__.__name__ if self.ot is not None else 'unconnected'}}}'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def connect_to(self, node):  # 他ノードに接続する
        self.to = node
        node.ot = self  # 逆参照をfromではなくotと書く
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_open(self):  # 当該ノードが空いているか
        return self.cell is None
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_upper(self):  # 次のインデックスのノードを返す
        return self.unit.nodes[self.idx +1] if (self.idx +1) < len(self.unit.nodes) else None
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_lower(self):  # 前のインデックスのノードを返す
        return self.unit.nodes[self.idx -1] if self.idx > 0 else None

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Unit:  # GTPシステムを構成するユニットの抽象クラス
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env):
        self.env = env
        self.nodes = []  # ユニットを構成するノードのリスト
        self.cells = []  # ユニットを構成するセルのリスト
        self.tjobs = []  # 搬送ジョブのリスト
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def is_blocked(self):  # 末尾セルからのバケットの送出しがブロックされているか？
        assert self.nodes[-1].to is not None, 'No connected unit'
        if self.nodes[-1].to.cell is None:  # 台車が未着なら
            return True
        else:  # 受取り側のセルが埋まっているかどうか
            return not self.nodes[-1].to.cell.is_open()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def precedence_filter(self, tjobs):  # ソフトな先行関係制約
        if len(tjobs) <= 0:  # 空リストが渡されたら
            return []  # 空リストを返す
        # 遅れている先行搬送ジョブ数の最小値
        min_late_count = min([tjob.late_preceding_tjob_count() for tjob in tjobs])
        # 遅れている先行搬送ジョブ数が最小のものだけに絞る
        filtered_tjobs = [tjob for tjob in tjobs if tjob.late_preceding_tjob_count() <= min_late_count]
        return filtered_tjobs
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def echelon_filter(self, tjobs, base_progress):  # エシェロン在庫数最小の作業場に向かう搬送ジョブに絞る
        echelon_count = self.env.gtps.get_echelon_count(base_progress)  # 作業場ごとのエシェロン在庫数
        for count in sorted(set(echelon_count)):  # 重複を削除して昇順に在庫数を取り出す
            # 指定されたエシェロン在庫数の作業場番号のリスト
            target_pickers = [picker for picker in range(PICKER) if echelon_count[picker] == count]
            # 上のリスト内の作業場に向かう搬送ジョブのリスト
            filtered_tjobs = [tjob for tjob in tjobs if tjob.pjob.station.picker in target_pickers]
            if len(filtered_tjobs) > 0:  # が空でなければ
                return filtered_tjobs  # それを返す
        return []  # 全て空なら，空リストを返す

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Store(Unit):  # 置場ユニット
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, aisle, floor, row):
        super().__init__(env)
        self.aisle = aisle
        self.floor = floor
        self.row = row
        self.nodes.append(Node(env, self, 0))
        self.cells.append(Cell(env, self, 0))
        # ノードとセルを対応付ける
        self.cells[0].attach(self.nodes[0])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_shuttle(self):  # 接続されているシャトルユニット
        return self.nodes[0].to.unit

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Shuttle(Unit):  # シャトルユニット
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, aisle, floor, stores, conveyors):
        super().__init__(env)
        self.aisle = aisle
        self.floor = floor
        self.nodes = [Node(env, self, row) for row in range(ROW +1)]  # 最後は台車の待機ノード
        # セル（台車）を作成し，待機ノードに配置
        self.cells.append(Cell(env, self, 0))
        self.cells[0].attach(self.nodes[ROW])
        # レールノードと置場ノードを相互に接続
        for row in range(ROW):
            self.nodes[row].connect_to(stores[row].nodes[0])
            stores[row].nodes[0].connect_to(self.nodes[row])
        # 待機ノードを出庫コンベヤに接続
        self.nodes[ROW].connect_to(conveyors[FORWARD].nodes[0])
        # 入庫コンベヤに待機ノードを接続
        conveyors[BACKWARD].nodes[-1].connect_to(self.nodes[ROW])
        # 出庫ジョブ到着イベントを初期化
        self.tjob_registered = env.event()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def register(self, tjob):  # 搬送ジョブを登録して，出庫ジョブ到着イベントを発火
        self.tjobs.append(tjob)
        self.tjob_registered.succeed()
        self.tjob_registered = self.env.event()
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch(self, candidate_tjobs):
        # ピッキングジョブのインデックスが最小のものだけに絞る
        pjob_idx = [tjob.pjob.idx for tjob in candidate_tjobs]
        candidate_tjobs = [tjob for tjob in candidate_tjobs if tjob.pjob.idx == min(pjob_idx)]
        return random.choice(candidate_tjobs)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def operate(self, env):  # シャトルのプロセス関数
        current_tjob = None  # 実行中の搬送ジョブ
        direction = BACKWARD  # （前回の）搬送方向
        while True:
            if current_tjob is None:  # 待機中なら
                fw_tjobs = [tjob for tjob in self.tjobs  # 着手可能な出庫ジョブリスト
                    if tjob.progress == TO_FW_SHTL  # 進捗が出庫シャトル前である
                    and tjob.bucket.is_at_home()  # バケットが置場にある
                    and tjob == tjob.bucket.current_tjob()  # そのバケットの次にやるべき搬送ジョブである
                    ]
                fw_tjobs = self.precedence_filter(fw_tjobs)  # 先行関係制約でフィルタリング
                bw_tjobs = [  # 入庫ジョブリスト
                    tjob for tjob in self.tjobs if tjob.progress == TO_BW_SHTL
                    ]
                if len(fw_tjobs) +len(bw_tjobs) <= 0:  # 着手可能な搬送ジョブがなければ
                    # 出庫ジョブの発生，もしくは，入庫ジョブの発生を待ち受ける
                    yield (self.tjob_registered | self.nodes[ROW].ot.cell.bucket_receipt)
                else:
                    # 出庫ジョブがない，もしくは，前回出庫で入庫ジョブがあれば
                    if len(fw_tjobs) <= 0 or (direction == FORWARD and len(bw_tjobs) > 0):
                        current_tjob = bw_tjobs[0]  # 先頭の入庫ジョブを選択（コンベヤの先頭のもの）
                        direction = BACKWARD  # 搬送方向を入庫に設定
                    else:
                        current_tjob = self.dispatch(fw_tjobs)  # 出庫ジョブのディスパッチング
                        direction = FORWARD  # 搬送方向を出庫に設定
                    self.tjobs.remove(current_tjob)  # 選択した搬送ジョブをリストから削除
                    if env.CONSOLE_OUT:
                        print(f'Shuttle {self.aisle}-{self.floor} is assigned with Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
            elif current_tjob.progress == TO_FW_SHTL:  # 出庫ジョブの処理中なら
                if self.cells[0].is_open():  # 空車なら
                    if self.cells[0].node.idx > current_tjob.bucket.home_store.row:  # 置場前に未到着なら
                        yield env.timeout(MT_SHTL)  # 移動時間分の時間遅れ
                        self.cells[0].move_down()  # 前のセルに移動
                    else:  # 置場前に到着済みなら
                        assert current_tjob.bucket.is_at_home(), 'Bucket is not in store'
                        yield env.timeout(LT_SHTL)  # 積替え時間分の時間遅れ
                        current_tjob.bucket.cell.transfer()  # バケットを受け取る
                        if env.CONSOLE_OUT:
                            print(f'Shuttle {self.aisle}-{self.floor} picks up at row {self.cells[0].node.idx} forward Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                else:  # 運搬中なら
                    if self.cells[0].node.idx < ROW:  # 待機ノードに未到着なら
                        yield env.timeout(MT_SHTL)  # 移動時間分の時間遅れ
                        self.cells[0].move_up()  # 次のセルに移動
                    else:  # 待機ノードに到着済みなら
                        if self.is_blocked():  # 接続先コンベヤの先頭セルが埋まっているなら
                            yield self.nodes[-1].to.cell.bucket_sent
                        yield env.timeout(LT_SHTL)  # 積替え時間分の時間遅れ
                        self.cells[0].transfer()  # バケットを送り出す
                        if env.CONSOLE_OUT:
                            print(f'Shuttle {self.aisle}-{self.floor} drops forward Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                        current_tjob.proceed()  # 搬送ジョブの進捗を進める（TO_FW_SHTL > TO_FW_LIFT）
                        current_tjob = None  # 実行中の搬送ジョブを削除
            elif current_tjob.progress == TO_BW_SHTL:  # 入庫ジョブの処理中なら
                if not self.cells[0].is_open():  # 運搬中なら
                    if self.cells[0].node.idx > current_tjob.bucket.home_store.row:  # 置場前に未到着なら
                        yield env.timeout(MT_SHTL)  # 移動時間分の時間遅れ
                        self.cells[0].move_down()  # 前のセルに移動
                    else:  # 置場前に到着済みなら
                        yield env.timeout(LT_SHTL)  # 積替え時間分の時間遅れ
                        self.cells[0].transfer()  # バケットを送り出す
                        if env.CONSOLE_OUT:
                            print(f'Shuttle {self.aisle}-{self.floor} drops at row {self.cells[0].node.idx} backward Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                else:  # 空車なら
                    if self.cells[0].node.idx == ROW:  # 待機ノードにいる（出発前）なら
                        yield env.timeout(LT_SHTL)  # 積込み時間分の時間遅れ
                        self.cells[0].node.ot.cell.transfer()  # バケットを受け取る
                        if env.CONSOLE_OUT:
                            print(f'Shuttle {self.aisle}-{self.floor} picks up backward Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                    else:  # 待機ノードへの帰路にいるなら
                        yield env.timeout(MT_SHTL)  # 移動時間分の時間遅れ
                        self.cells[0].move_up()  # 前のセルに移動
                        if self.cells[0].node.idx == ROW:  # 待機ノードに帰還したなら
                            current_tjob.proceed()  # 搬送ジョブの進捗を進める（TO_BW_SHTL > DONE）
                            current_tjob = None  # 実行中の搬送ジョブを削除
            else:
                assert False, 'Shuttle job has inconsistent progress status'

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Conveyor(Unit):  # コンベヤ（上流から順に stage = 0, 1, 2）
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, stage, dim0, dim1, direction):
        super().__init__(env)
        self.stage = stage
        self.dim0 = dim0  # aisle or picker
        self.dim1 = dim1  # floor or none
        self.direction = direction
        # ノードを作成し，インデックス順に接続する
        self.nodes = [Node(env, self, seg) for seg in range(CONV_LEN[stage])]
        self.connect_nodes()
        # セルを作成し，ノードに対応付ける
        self.cells = [Cell(env, self, seg) for seg in range(CONV_LEN[stage])]
        for seg in range(CONV_LEN[stage]):
            self.cells[seg].attach(self.nodes[seg])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def connect_nodes(self):  # コンベヤ上でセルからセルへバケットを送り出せるようにする
        for seg in range(len(self.nodes) -1):
            self.nodes[seg].connect_to(self.nodes[seg +1])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def operate(self, env, seg):  # コンベヤ各セルのプロセス関数
        this_cell = self.cells[seg]
        next_cell = self.cells[seg +1]
        while True:
            if this_cell.is_open():  # セルが空ならバケットが届くまで待つ
                yield this_cell.bucket_receipt
            if not next_cell.is_open():  # 次のセルが埋まっていれば空くまで待つ
                yield next_cell.bucket_sent
            yield env.timeout(MT_CONV)  # 移動時間分の時間遅れ
            this_cell.transfer()  # バケットを送り出す
            # 送出し先が末尾セルなら，搬送ジョブを次ユニットのジョブリストに追加
            if next_cell == self.cells[-1]:
                next_cell.node.to.unit.tjobs.append(next_cell.bucket.current_tjob())
                if env.CONSOLE_OUT:
                    if self.direction == FORWARD:
                        print(f'Forward Conveyor {self.stage}-{self.dim0}-{self.dim1} brings Tjob {next_cell.bucket.current_tjob().pjob.idx}-{next_cell.bucket.current_tjob().idx} to {next_cell.node.to.unit.__class__.__name__} at {env.now:.2f}')
                    else:
                        print(f'Backward Conveyor {self.stage}-{self.dim0}-{self.dim1} brings Tjob {next_cell.bucket.current_tjob().pjob.idx}-{next_cell.bucket.current_tjob().idx} to {next_cell.node.to.unit.__class__.__name__} at {env.now:.2f}')

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Lift(Unit):  # リフトユニット
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, aisle, direction, upward_cvs, downward_cvs):
        super().__init__(env)
        self.aisle = aisle
        self.direction = direction
        self.nodes = [Node(env, self, floor) for floor in range(FLOOR)]
        # セル（台車）を作成し，0階に配置
        self.cells.append(Cell(env, self, 0))
        self.cells[0].attach(self.nodes[0])
        # 上流コンベヤと接続
        for floor in range(FLOOR):
            if direction == FORWARD:
                upward_cvs[floor][FORWARD].nodes[-1].connect_to(self.nodes[floor])
            else:
                self.nodes[floor].connect_to(upward_cvs[floor][BACKWARD].nodes[0])
        # 下流コンベヤと接続
        if direction == FORWARD:
            self.nodes[0].connect_to(downward_cvs[FORWARD].nodes[0])
        else:
            downward_cvs[BACKWARD].nodes[-1].connect_to(self.nodes[0])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch(self, candidate_tjobs):
        # ピッキングジョブのインデックスが最小のものだけに絞る
        pjob_idx = [tjob.pjob.idx for tjob in candidate_tjobs]
        candidate_tjobs = [tjob for tjob in candidate_tjobs if tjob.pjob.idx == min(pjob_idx)]
        return random.choice(candidate_tjobs)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def operate(self, env):  # リフトのプロセス関数
        current_tjob = None  # 実行中の搬送ジョブ
        if self.direction == FORWARD:  # 出庫リフト
            while True:
                if current_tjob is None:  # 待機中なら
                    candidate_tjobs = self.precedence_filter(self.tjobs)  # 先行関係制約でフィルタリング
                    if len(candidate_tjobs) <= 0:  # 着手可能な搬送ジョブがなければ
                        yield env.any_of([self.nodes[floor].ot.cell.bucket_receipt for floor in range(FLOOR)])
                    else:
                        current_tjob = self.dispatch(candidate_tjobs)  # 搬送ジョブのディスパッチング
                        assert current_tjob.progress == TO_FW_LIFT, 'Wrong progress at forward lift'
                        self.tjobs.remove(current_tjob)  # 選択した搬送ジョブをリストから削除
                        if env.CONSOLE_OUT:
                            print(f'Forward Lift {self.aisle} is assigned with Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                else:  # 搬送ジョブの処理中なら
                    if self.cells[0].is_open():  # 空車なら
                        if self.cells[0].node.idx < current_tjob.bucket.home_store.floor:  # 階層に未到着なら
                            yield env.timeout(MT_LIFT)  # 移動時間分の時間遅れ
                            self.cells[0].move_up()  # 上階のセルに移動
                        else:  # 階層に到着したら
                            yield env.timeout(LT_LIFT)  # 積替え時間分の時間遅れ
                            self.cells[0].node.ot.cell.transfer()  # バケットを受け取る
                            if env.CONSOLE_OUT:
                                print(f'Forward Lift {self.aisle}-{self.direction} picks up on floor {current_tjob.bucket.home_store.floor} Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                    else:  # 運搬中なら
                        if self.cells[0].node.idx > 0:  # 0階に未到達なら
                            yield env.timeout(MT_LIFT)  # 移動時間分の時間遅れ
                            self.cells[0].move_down()  # 下階のセルに移動
                        else:  # 0階に到達したら
                            if not self.cells[0].node.to.cell.is_open():
                                yield self.cells[0].node.to.cell.bucket_sent
                            yield env.timeout(LT_LIFT)  # 積込み時間分の時間遅れ
                            self.cells[0].transfer()  # バケットを送り出す
                            if env.CONSOLE_OUT:
                                print(f'Forward Lift {self.aisle}-{self.direction} drops Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                            current_tjob.proceed()  # 搬送ジョブの進捗を進める（TO_FW_LIFT > TO_FW_LOOP）
                            current_tjob = None  # 実行中の搬送ジョブを削除
        else:  # 入庫リフト
            while True:
                if current_tjob is None:  # 待機中なら
                    if len(self.tjobs) <= 0:  # 着手可能な搬送ジョブがなければ
                        yield self.nodes[0].ot.cell.bucket_receipt
                    else:
                        current_tjob = self.tjobs[0]  # （コンベヤの先頭にある）搬送ジョブの割付け
                        assert current_tjob.progress == TO_BW_LIFT, 'Wrong progress at backward lift'
                        self.tjobs.remove(current_tjob)  # 選択した搬送ジョブをリストから削除
                        if env.CONSOLE_OUT:
                            print(f'Backward Lift {self.aisle}-{self.direction} is assigned with Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                else:  # 搬送ジョブの処理中なら
                    if self.cells[0].is_open():  # 空車なら
                        if self.cells[0].node.idx == 0:  # 0階にいるなら（出発前）
                            yield env.timeout(LT_LIFT)  # 積替え時間分の時間遅れ
                            self.cells[0].node.ot.cell.transfer()  # バケットを受け取る
                            if env.CONSOLE_OUT:
                                print(f'Backward Lift {self.aisle}-{self.direction} picks up Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                        else:  # 0階への帰路にいるなら
                            yield env.timeout(MT_LIFT)  # 移動時間分の時間遅れ
                            self.cells[0].move_down()  # 下階のセルに移動
                            if self.cells[0].node.idx == 0:  # 0階に帰還したなら
                                current_tjob = None  # 実行中の搬送ジョブを削除（0階に帰還してから削除する）
                    else:  # 運搬中なら
                        if self.cells[0].node.idx < current_tjob.bucket.home_store.floor:  # 階層に未到達なら
                            yield env.timeout(MT_LIFT)  # 移動時間分の時間遅れ
                            self.cells[0].move_up()  # 上階のセルに移動
                        else:  # 階層に到達したら
                            if not self.cells[0].node.to.cell.is_open():
                                yield self.cells[0].node.to.cell.bucket_sent
                            yield env.timeout(LT_LIFT)  # 積込み時間分の時間遅れ
                            self.cells[0].transfer()  # バケットを送り出す
                            if env.CONSOLE_OUT:
                                print(f'Backward Lift {self.aisle}-{self.direction} drops on floor {current_tjob.bucket.home_store.floor} Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                            current_tjob.proceed()  # 搬送ジョブの進捗を進める（TO_BW_LIFT > TO_BW_SHTL）
                            if self.cells[0].node.idx == 0:  # 0階が目的階層だった場合の例外処理
                                current_tjob = None  # 実行中の搬送ジョブを削除

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Loop(Unit):  # ループユニット
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, upward_cvs, downward_cvs):
        super().__init__(env)
        self.nodes = [Node(env, self, seg) for seg in range(LOOP)]
        self.cells = [Cell(env, self, vehicle) for vehicle in range(env.VEHICLE)]
        # セル（台車）をノード0から順に配置していく
        for vehicle in range(env.VEHICLE):
            self.cells[vehicle].attach(self.nodes[env.VEHICLE -vehicle -1])
        # 上流コンベヤと接続
        for aisle in range(AISLE):
            upward_cvs[aisle][0][FORWARD].nodes[-1].connect_to(self.nodes[AISLE_SEG[aisle][FORWARD]])
            self.nodes[AISLE_SEG[aisle][BACKWARD]].connect_to(upward_cvs[aisle][0][BACKWARD].nodes[0])
        # 下流コンベヤと接続
        for picker in range(PICKER):
            self.nodes[PICKER_SEG[picker][FORWARD]].connect_to(downward_cvs[picker][0][FORWARD].nodes[0])
            downward_cvs[picker][0][BACKWARD].nodes[-1].connect_to(self.nodes[PICKER_SEG[picker][BACKWARD]])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_distance(self, from_node, to_node):
        return (to_node.idx +len(self.nodes) -from_node.idx) %len(self.nodes)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_nearest_car(self, cars, destination_node):
        dist = [self.get_distance(car.node, destination_node) for car in cars]
        if min(dist) <= 0:  # 眼の前のセルに台車があれば
            candidate_car = cars[dist.index(min(dist))]  # それが候補になる
            if candidate_car.goal is None:  # 待機状態なら
                return candidate_car  # それを選択すればよい
            else:  # しかし，移動中なら
                dist[dist.index(min(dist))] = LOOP  # 移動後にその台車は周回遅れになる
        nearest_idx = dist.index(min(dist))  # 距離が最短の台車のインデックス
        return cars[nearest_idx]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch(self, candidate_fw_tjobs):
        candidate_fw_tjobs = self.echelon_filter(candidate_fw_tjobs, ON_FW_LOOP)
        for tjob in self.tjobs:  # 前から順に見ていく
            if tjob.progress == TO_BW_LOOP:  # 入庫ジョブなら
                return tjob  # それを実行
            else:  # 出庫ジョブなら
                if tjob in candidate_fw_tjobs:
                    return tjob
        assert False, 'No transfer job to dispatch'
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def operate(self, env):  # 搬送ジョブの台車への割付け
        while True:
            free_cars = [car for car in self.cells if car.tjob is None]  # 搬送ジョブ未割付けの台車
            if len(self.tjobs) > 0 and len(free_cars) > 0:  # どちらも存在するなら
                fw_tjobs = [tjob for tjob in self.tjobs if tjob.progress == TO_FW_LOOP]  # 出庫ジョブリスト
                fw_tjobs = self.precedence_filter(fw_tjobs)  # 先行関係制約でフィルタリング
                current_tjob = self.dispatch(fw_tjobs)  # 搬送ジョブのディスパッチング
                if len(free_cars) == 1:  # 空き台車が1台ならそれを選ぶ
                    the_car = free_cars[0]
                else:  # 空き台車が複数ある場合は，一番近いものに割り付ける
                    the_car = self.get_nearest_car(free_cars, current_tjob.bucket.cell.node.to)
                the_car.assign_tjob(current_tjob)  # 選択した台車に選択したジョブを割り付ける
                if env.CONSOLE_OUT:
                    print(f'Loop assignes {'forward' if current_tjob.progress == TO_FW_LOOP else 'backward'} Tjob {current_tjob.pjob.idx}-{current_tjob.idx} to car {the_car.idx}')
                self.tjobs.remove(current_tjob)  # 割り付けた搬送ジョブをリストから削除
            elif len(self.tjobs) <= 0:  # 着手可能な搬送ジョブがなければ，次の到着まで待ち受け
                entrance_cells = [node.ot.cell for node in self.nodes if node.ot is not None]
                yield env.any_of([cell.bucket_receipt for cell in entrance_cells])
            elif len(free_cars) <= 0:  # 空き台車がなければ，いずれかの台車が搬送ジョブを完了するまで待ち受け
                yield env.any_of([car.tjob_completed for car in self.cells])
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_escape_node(self, pusher):
        escape_node_idx = pusher.goal.idx +1
        if pusher.goal == pusher.node:  # 後続台車が荷降ろしブロッキング中に押し出された場合
            escape_node_idx += 1
        escape_node_idx = escape_node_idx %len(self.nodes)
        return self.nodes[escape_node_idx]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def move_ahead(self, env, vehicle):  # 次のセルに進む
        this_car = self.cells[vehicle]
        car_ahead = self.cells[(vehicle -1 +self.env.VEHICLE) %self.env.VEHICLE]  # 先行台車
        car_behind = self.cells[(vehicle +1) %self.env.VEHICLE]  # 後続台車
        next_idx = (this_car.node.idx +1) %len(self.nodes)
        next_node = self.nodes[next_idx]
        if not next_node.is_open():
            car_ahead.is_pushed = True
            car_ahead.pushed.succeed()
            car_ahead.pushed = env.event()
            yield next_node.unblocked  # ノード解放イベントを待ち受け
        yield env.timeout(MT_LOOP)  # 移動時間分の時間遅れ
        this_car.move_to(next_node)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def handle_failure(self, env, vehicle, failure_count):  # 荷卸し失敗の処理プロセス
        if failure_count > env.FAILURE_LIMIT:
            this_car = self.cells[vehicle]
            this_car.tjob.retry_needed = True
            this_car.tjob.proceed(ON_BW_LOOP)  # 進捗を出庫側から入庫側に変更
            this_car.assign_goal(self.nodes[this_car.tjob.get_goal_node_idx()])  # 行き先を更新
        else:
            yield env.process(self.move_ahead(env, vehicle))  # 前進プロセスを駆動
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def drive(self, env, vehicle):  # 各台車のプロセス関数
        this_car = self.cells[vehicle]
        car_ahead = self.cells[(vehicle -1 +self.env.VEHICLE) %self.env.VEHICLE]  # 先行台車
        car_behind = self.cells[(vehicle +1) %self.env.VEHICLE]  # 後続台車
        failure_count = 0  # 荷卸し失敗回数
        while True:
            if this_car.tjob is None:  # 搬送ジョブが割り付けられていないなら
                if this_car.goal is None:  # 行き先が未定で停止中（待機状態）
                    pushed = this_car.pushed
                    goal_assigned = this_car.goal_assigned
                    result = yield (goal_assigned | pushed)  # 行き先が割り付けられるまで待機
                    if not goal_assigned in result:  # 搬送ジョブは割り当てられず，後続台車にプッシュされたら
                        escape_node = self.get_escape_node(car_behind)
                        this_car.assign_goal(escape_node)
                elif this_car.goal != this_car.node:  # （後続台車にプッシュされ）退避の途上なら
                    yield env.process(self.move_ahead(env, vehicle))  # 前進プロセスを駆動
                else:
                    this_car.goal = None  # 待機状態に戻る
            else:  # 搬送ジョブが割り付けられていれば
                if this_car.goal != this_car.node:  # 行き先への移動の途上なら
                    yield env.process(self.move_ahead(env, vehicle))  # 前進プロセスを駆動
                else:  # 行き先に到着したら
                    if this_car.tjob.progress in [TO_FW_LOOP, TO_BW_LOOP]:  # 荷積み前
                        yield env.timeout(LT_LOOP)  # 積込み時間分の時間遅れ
                        this_car.node.ot.cell.transfer()  # バケットを受け取る
                        if env.CONSOLE_OUT:
                            print(f'Car {this_car.idx} picks up at cell {this_car.node.idx} {'forward' if this_car.tjob.progress == TO_FW_LOOP else 'backward'} Tjob {this_car.tjob.pjob.idx}-{this_car.tjob.idx} at {env.now:.2f}')
                        this_car.tjob.proceed()  # 搬送ジョブの進捗を進める
                        this_car.assign_goal(self.nodes[this_car.tjob.get_goal_node_idx()])  # 行き先を更新
                    elif this_car.tjob.progress in [ON_FW_LOOP, ON_BW_LOOP]:  # 荷降ろし前
                        if this_car.tjob.progress == ON_FW_LOOP and this_car.tjob.late_preceding_tjob_count() > 0:  # 先行関係制約が満たされていないなら（まだ降ろせない）
                            failure_count += 1  # 荷降し失敗回数を増やす
                            if env.CONSOLE_OUT:
                                print(f'Car {this_car.idx} fails at cell {this_car.node.idx} and starts to retry Tjob {this_car.tjob.pjob.idx}-{this_car.tjob.idx} at {env.now:.2f}')
                            yield env.process(self.handle_failure(env, vehicle, failure_count))
                        else:  # 先行関係チェックOK
                            if not this_car.node.to.cell.is_open():  # 荷降ろしがブロックされたら
                                result = yield (pushed | this_car.node.to.cell.bucket_sent)
                                if pushed in result:  # 後続台車にプッシュされたら
                                    yield env.process(self.move_ahead(env, vehicle))  # 前進プロセスを駆動
                                    if env.CONSOLE_OUT:
                                        print(f'Car {this_car.idx} is pushed out from cell {this_car.node.idx} with Tjob {this_car.tjob.pjob.idx}-{this_car.tjob.idx} at {env.now:.2f}')
                            else:
                                yield env.timeout(LT_LOOP)  # 積込み時間分の時間遅れ
                                this_car.transfer()  # バケットを送り出す
                                if env.CONSOLE_OUT:
                                    print(f'Car {this_car.idx} drops at cell {this_car.node.idx} {'forward' if this_car.tjob.progress == ON_FW_LOOP else 'backward'} Tjob {this_car.tjob.pjob.idx}-{this_car.tjob.idx} at {env.now:.2f}')
                                failure_count = 0  # 荷降し失敗回数を0に戻す
                                this_car.tjob.proceed()  # 搬送ジョブの進捗を進める
                                this_car.tjob_completed.succeed()  # 搬送ジョブ完了イベントの発火
                                this_car.tjob_completed = env.event()
                                this_car.tjob = None  # 搬送ジョブを削除
                                if this_car.is_pushed:  # 後続台車をブロックしていたら
                                    escape_node = self.get_escape_node(car_behind)
                                    this_car.assign_goal(escape_node)
                                else:
                                    this_car.goal = None  # 待機状態に戻る
                    else:
                        assert False, 'Loop job has inconsistent progress status'

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class Station(Unit):  # 作業場ユニット
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, picker, conveyors):
        super().__init__(env)
        self.picker = picker
        self.nodes.append(Node(env, self, 0))
        self.cells.append(Cell(env, self, 0))
        # ノードとセルを対応付ける
        self.cells[0].attach(self.nodes[0])
        # 出庫コンベヤを作業場に接続
        conveyors[FORWARD].nodes[-1].connect_to(self.nodes[0])
        # 作業場を入庫コンベヤに接続
        self.nodes[0].connect_to(conveyors[BACKWARD].nodes[0])
        # 稼働率計算用
        self.total_tjobs = 0  # 処理した搬送ジョブの総数
        self.total_work_time = 0  # 実作業時間の総和（作業場への搬入・搬出時間を除く）
        self.makespan = 0  # 最後の搬送ジョブを搬出し終えた時刻
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_utilization(self):  # 稼働率の計算
        utilized = self.total_tjobs *MT_CONV *2 +self.total_work_time  # 搬出・搬入時間を加算
        return utilized /self.makespan
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_work_time(self):
        work_time = random.choices(T_PICK, weights=P_PICK)[0]
        self.total_work_time += work_time
        self.total_tjobs += 1
        return work_time
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def operate(self, env):  # ピッキング作業場のプロセス関数
        current_tjob = None  # 実行中の搬送ジョブ
        while True:
            if current_tjob is None:  # 待機中なら
                if len(self.tjobs) <= 0:  # 着手可能な搬送ジョブがなければ
                    yield self.cells[0].node.ot.cell.bucket_receipt
                else:
                    current_tjob = self.tjobs[0]  # 入口コンベヤの先頭ジョブを選択し
                    self.tjobs.remove(current_tjob)  # それをリストから削除
                    assert current_tjob.progress == TO_PICKED, 'Wrong progress at station'
                    assert current_tjob.late_preceding_tjob_count() <= 0, 'Wrong picking order'
            else:  # 搬送ジョブが割り付けられているなら
                if self.cells[0].is_open():  # 作業場が空いているなら
                    yield env.timeout(MT_CONV)  # 移動時間分の時間遅れ
                    self.cells[0].node.ot.cell.transfer()  # バケットを受け取る
                else:  # 作業場にバケットがあるなら
                    work_time = self.get_work_time()  # ピッキング作業時間を取得
                    yield env.timeout(work_time)
                    # 搬送ジョブの進捗を進め（TO_PICKED -> TO_BW_LOOP），作業時間を記録
                    current_tjob.proceed(work_time=work_time)
                    if env.CONSOLE_OUT:
                        print(f'Station {self.picker} completed processing Tjob {current_tjob.pjob.idx}-{current_tjob.idx} at {env.now:.2f}')
                    return_needed = current_tjob.bucket.pick(current_tjob)  # ピッキング処理と入庫の必要性確認
                    if return_needed:  # バケットがまだ空でなければ入庫ジョブに進む
                        if not self.cells[0].node.to.cell.is_open():  # 出口コンベヤが埋まっているなら
                            yield self.cells[0].node.to.cell.bucket_sent  # それが空くのを待ち受ける
                        yield env.timeout(MT_CONV)  # 移動時間分の時間遅れ
                        self.cells[0].transfer()  # バケットを送り出す
                    current_tjob = None  # 実行中の搬送ジョブを削除
                    self.makespan = env.now  # メイクスパンに現在時刻を登録

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class GTPSystem:
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def __init__(self, env, shtl_cls=Shuttle, lift_cls=Lift, loop_cls=Loop):
        self.env = env
        self.pjobs = [PJob(env, idx) for idx in range(env.PJOB)]
        self.stores = [[[  # 置場ユニットの作成
            Store(env, aisle, floor, row) for row in range(ROW)
            ] for floor in range(FLOOR)] for aisle in range(AISLE)]
        self.create_buckets()  # バケットの作成と（上で作成した）置場への格納
        self.conveyors = [[[[  # コンベヤユニットの作成
            Conveyor(env, stage, dim0, dim1, direction) for direction in range(2)
            ] for dim1 in range(CONV_DIM[1][stage])
            ] for dim0 in range(CONV_DIM[0][stage])] for stage in range(3)]
        self.shuttles = [[  # シャトルユニットの作成
            shtl_cls(env, aisle, floor, self.stores[aisle][floor], self.conveyors[0][aisle][floor])
            for floor in range(FLOOR)
            ] for aisle in range(AISLE)]
        self.lifts = [[  # リフトユニットの作成
            lift_cls(env, aisle, direction, self.conveyors[0][aisle], self.conveyors[1][aisle][0])
            for direction in range(2)
            ] for aisle in range(AISLE)]
        self.loop = loop_cls(env, self.conveyors[1], self.conveyors[2])  # ループユニットの作成
        self.stations = [  # 作業場ユニットの作成
            Station(env, picker, self.conveyors[2][picker][0])
            for picker in range(PICKER)]
        self.is_all_released = False  # 全ピッキングジョブを投入し終えたかのフラグ
        self.unpicked_tjobs = []  # 投入済み未作業の搬送ジョブリスト
        self.unrestored_tjobs = []  # 投入済み未帰還の搬送ジョブリスト
        self.register_processes()  # プロセス関数の登録
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def register_processes(self):
        self.env.process(self.operate(self.env))
        for aisle in range(AISLE):
            for floor in range(FLOOR):
                self.env.process(self.shuttles[aisle][floor].operate(self.env))
        for aisle in range(AISLE):
            for direction in range(2):
                self.env.process(self.lifts[aisle][direction].operate(self.env))
        self.env.process(self.loop.operate(self.env))
        for vehicle in range(self.env.VEHICLE):
            self.env.process(self.loop.drive(self.env, vehicle))
        for stage in range(3):
            for dim0 in range(CONV_DIM[0][stage]):
                for dim1 in range(CONV_DIM[1][stage]):
                    for direction in range(2):
                        for seg in range(CONV_LEN[stage] -1):
                            self.env.process(self.conveyors[stage][dim0][dim1][direction].operate(self.env, seg))
        for picker in range(PICKER):
            self.env.process(self.stations[picker].operate(self.env))
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def create_buckets(self):
        self.buckets = [[] for item in range(ITEM)]  # アイテムごとの割付け可能バケットのリスト
        self.sku = [[  # [aisle][floor][row] に対応するバケットを格納するリスト
            [] for floor in range(FLOOR)] for aisle in range(AISLE)
            ]
        all_buckets = []  # 全バケットのリスト
        for idx in range(AISLE *FLOOR *ROW):
            item = idx %ITEM
            bucket = Bucket(self.env, idx, item)
            self.buckets[item].append(bucket)
            all_buckets.append(bucket)
        random.shuffle(all_buckets)  # ランダムに並べ替えてから
        for aisle in range(AISLE):  # 置場に順に格納していく
            for floor in range(FLOOR):
                for row in range(ROW):
                    self.sku[aisle][floor].append(all_buckets.pop(-1))
                    self.stores[aisle][floor][row].cells[0].receive(self.sku[aisle][floor][-1])
                    self.sku[aisle][floor][-1].home_store = self.stores[aisle][floor][row]  # バケットのホームを設定
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def get_echelon_count(self, base_progress):  # ピッキング作業場までのエシェロン在庫量を返す
        echelons = [[tjob for tjob in self.unpicked_tjobs
            if tjob.pjob.station.picker == picker and base_progress <= tjob.progress <= TO_PICKED
            ] for picker in range(PICKER)]
        return [len(echelon) for echelon in echelons]
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch(self, waiting_pjobs, last_picker):  # ピッキングジョブの投入ルール
        next_pjob = waiting_pjobs.pop(0)  # デフォルトはFIFO
        next_picker = (last_picker +1) %PICKER  # デフォルトはラウンドロビン
        return next_pjob, next_picker
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def operate(self, env):  # ピッキングジョブ投入のプロセス関数
        env.tjob_picked = env.event()  # 搬送ジョブのピッキング完了イベント
        env.tjob_restored = env.event()  # 搬送ジョブの置場への帰還イベント
        waiting_pjobs = self.pjobs.copy()  # 未投入ピッキングジョブのリストを初期化
        last_picker = -1  # 割り付けた作業場の番号を初期化（ラウンドロビンルールで使用）
        while len(waiting_pjobs) > 0:  # 投入するピッキングジョブがなくなるまで
            if len(self.unpicked_tjobs) >= env.RELEASABLE:  # 未作業の搬送ジョブ数の上限チェック
                yield env.tjob_picked  # ピッキング完了を待機
            # if len(self.unrestored_tjobs) >= env.RELEASABLE:  # 未帰還の搬送ジョブ数の上限チェック
            #     yield env.tjob_restored  # 置場への帰還を待機
            else:
                # 次に投入するピッキングジョブとそれを割り付ける作業場の番号を選択
                next_pjob, last_picker = self.dispatch(waiting_pjobs, last_picker)
                next_pjob.release_pjob(self.stations[last_picker])  # 投入処理
                self.unpicked_tjobs += next_pjob.tjobs  # 投入済み未作業搬送ジョブリスト
                self.unrestored_tjobs += next_pjob.tjobs  # 投入済み未帰還搬送ジョブリスト
        self.is_all_released = True  # 全ピッキングジョブの投入完了
