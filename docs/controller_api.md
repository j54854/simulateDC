# コントローラ API リファレンス

カスタムコントローラの実装方法と，コントローラ内で参照できる属性・メソッドの一覧。

---

## 基本パターン

`DefaultController` をサブクラス化し，必要なメソッドだけオーバーライドする。
`examples/run_gtpsim.py` の `MyController` が実装例として参照できる。

```python
from gtp import DefaultController, Progress

class MyController(DefaultController):

    def dispatch_shuttle(self, shuttle, candidate_tjobs):
        candidate_tjobs = self.echelon_filter(candidate_tjobs, Progress.TO_FW_LIFT)
        ...
        return chosen_tjob

    # オーバーライドしないメソッドは DefaultController のデフォルト実装が使われる
```

`create_simulator` の `controller_cls` 引数に渡す:

```python
env = create_simulator(params=params, controller_cls=MyController)
env.run(until=env.simulation_completed)
```

---

## オーバーライドするメソッド

### `dispatch_pjobs(waiting_pjobs, last_picker) -> tuple[PJob, int]`

**呼ばれるタイミング**: 新しいピッキングジョブを投入できるとき。

| 引数 | 型 | 説明 |
|------|----|------|
| `waiting_pjobs` | `list[PJob]` | 投入待ちジョブリスト。`pop(0)` して取り出す |
| `last_picker` | `int` | 直前に投入した作業場番号（0-indexed） |

戻り値: `(next_pjob, next_picker)` — 次に投入するジョブと作業場番号のタプル。

**デフォルト実装**: FIFO 投入 × ラウンドロビン作業場割当。

---

### `dispatch_shuttle(shuttle, candidate_tjobs) -> TJob`

**呼ばれるタイミング**: シャトルが空きになり，割り当て待ちジョブがあるとき。

| 引数 | 型 | 説明 |
|------|----|------|
| `shuttle` | `Shuttle` | 割り当て先シャトル。`shuttle.aisle`・`shuttle.floor` を参照可 |
| `candidate_tjobs` | `list[TJob]` | 割当候補（`progress == TO_FW_SHTL`）。空にはならない |

戻り値: リストから選択した1件の `TJob`。

**デフォルト実装**: 最古の PJob に絞りランダム選択。

---

### `dispatch_lift(lift, candidate_tjobs) -> TJob`

**呼ばれるタイミング**: リフトが空きになり，割り当て待ちジョブがあるとき。

| 引数 | 型 | 説明 |
|------|----|------|
| `lift` | `Lift` | 割り当て先リフト。`lift.aisle` を参照可 |
| `candidate_tjobs` | `list[TJob]` | 割当候補（`progress == TO_FW_LIFT`）。空にはならない |

戻り値: リストから選択した1件の `TJob`。

**デフォルト実装**: 最古の PJob に絞りランダム選択。

---

### `dispatch_loop(loop, candidate_fw_tjobs) -> TJob`

**呼ばれるタイミング**: ループ台車が空きになり，割り当て待ちジョブがあるとき。

| 引数 | 型 | 説明 |
|------|----|------|
| `loop` | `Loop` | ループユニット。`loop.tjobs`（到着順の待機ジョブリスト）を参照可 |
| `candidate_fw_tjobs` | `list[TJob]` | 出庫方向の割当候補（`progress == TO_FW_LOOP`） |

`loop.tjobs` には入庫ジョブ（`TO_BW_LOOP`）と出庫ジョブ（`TO_FW_LOOP`）が混在する。
出庫ジョブを選ぶ場合は `candidate_fw_tjobs` の中から選ぶこと。

戻り値: `loop.tjobs` または `candidate_fw_tjobs` から選択した1件の `TJob`。

**デフォルト実装**: 到着順に先頭のジョブを選択（入庫優先）。

---

## ユーティリティメソッド

### `echelon_filter(tjobs, base_progress) -> list[TJob]`

エシェロン在庫数が最小の作業場に向かうジョブに絞る。ロードバランシングに使う。

```python
# 使用例: リフト割当時に在庫の少ない作業場を優先
candidate_tjobs = self.echelon_filter(candidate_tjobs, Progress.TO_FW_LOOP)
```

| 引数 | 型 | 説明 |
|------|----|------|
| `tjobs` | `list[TJob]` | 絞り込み対象のジョブリスト |
| `base_progress` | `Progress` | エシェロン在庫の計上開始進捗 |

戻り値: 絞り込み後のジョブリスト。該当なしの場合は空リスト。

### `get_echelon_count(base_progress) -> list[int]`

作業場ごとのエシェロン在庫数をリストで返す（インデックス = 作業場番号）。

---

## コントローラ内で参照可能な属性

### `TJob`

| 属性・メソッド | 型 | 説明 |
|--------------|-----|------|
| `pjob` | `PJob` | 所属するピッキングジョブ |
| `bucket` | `Bucket` | 搬送対象のバケット |
| `vol` | `int` | バケットから取り出す量 |
| `progress` | `Progress` | 現在の進捗段階 |
| `retry_needed` | `bool` | 再搬送フラグ（荷卸し失敗時に `True`） |
| `released_time` | `float` | 投入時刻（シミュレーション時間） |
| `picked_time` | `float \| None` | ピッキング完了時刻（未完了なら `None`） |
| `is_picked()` | `bool` | ピッキング完了済みかどうか |

### `Bucket`

| 属性・メソッド | 型 | 説明 |
|--------------|-----|------|
| `item` | `int` | アイテム番号（0-indexed） |
| `home_store` | `Store` | 割り当てられた置場ユニット（`home_store.aisle`・`home_store.floor`・`home_store.row`） |
| `get_balance()` | `int` | 正味残量（割付け可能量）[0, 100] |
| `get_fullness()` | `int` | 実残量（ピッキング未取出し量）[0, 100] |
| `priority()` | `float` | 割付け優先度（小さいほど優先） |

### `PJob`

| 属性・メソッド | 型 | 説明 |
|--------------|-----|------|
| `idx` | `int` | ジョブのインデックス（投入順，0-indexed） |
| `station` | `Station` | 割り当てられた作業場（`station.picker` で作業場番号） |
| `tjobs` | `list[TJob]` | このジョブに紐づく搬送ジョブリスト |
| `reqs` | `dict[int, int]` | 要求内容（アイテム番号 → 必要量） |

---

## `Progress` の値と意味

```
TO_FW_SHTL (0)  往路シャトルへの乗車前
TO_FW_LIFT (1)  往路リフトへの乗車前
TO_FW_LOOP (2)  往路ループへの乗車前
ON_FW_LOOP (3)  往路ループからの降車前
TO_PICKED  (4)  ピッキング作業完了前
TO_BW_LOOP (5)  復路ループへの乗車前
ON_BW_LOOP (6)  復路ループからの降車前
TO_BW_LIFT (7)  復路リフトへの乗車前
TO_BW_SHTL (8)  復路シャトルへの乗車前
DONE       (9)  置場への帰還完了
```

`dispatch_shuttle` の候補は `TO_FW_SHTL`，`dispatch_lift` は `TO_FW_LIFT`，
`dispatch_loop` の出庫候補は `TO_FW_LOOP`，入庫は `TO_BW_LOOP`。

## `Direction` の値

```
FORWARD  (0)  出庫方向
BACKWARD (1)  入庫方向
```
