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
        candidate_tjobs = self.precedence_filter(candidate_tjobs)
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
| `candidate_tjobs` | `list[TJob]` | 着手可能な割当候補すべて（`progress == TO_FW_SHTL`）。空にはならない |

戻り値: リストから選択した1件の `TJob`。

**デフォルト実装**: `precedence_filter` で絞った後，最古の PJob に絞りランダム選択。

---

### `dispatch_lift(lift, candidate_tjobs) -> TJob`

**呼ばれるタイミング**: リフトが空きになり，割り当て待ちジョブがあるとき。

| 引数 | 型 | 説明 |
|------|----|------|
| `lift` | `Lift` | 割り当て先リフト。`lift.aisle` を参照可 |
| `candidate_tjobs` | `list[TJob]` | 着手可能な割当候補すべて（`progress == TO_FW_LIFT`）。空にはならない |

戻り値: リストから選択した1件の `TJob`。

**デフォルト実装**: `precedence_filter` で絞った後，最古の PJob に絞りランダム選択。

---

### `dispatch_loop(loop, candidate_fw_tjobs) -> TJob`

**呼ばれるタイミング**: ループ台車が空きになり，割り当て待ちジョブがあるとき。

| 引数 | 型 | 説明 |
|------|----|------|
| `loop` | `Loop` | ループユニット。`loop.tjobs`（到着順の待機ジョブリスト）を参照可 |
| `candidate_fw_tjobs` | `list[TJob]` | 出庫方向の着手可能な割当候補すべて（`progress == TO_FW_LOOP`）。空のこともある |

`loop.tjobs` には入庫ジョブ（`TO_BW_LOOP`）と出庫ジョブ（`TO_FW_LOOP`）が混在する。
`candidate_fw_tjobs` は `loop.tjobs` の出庫ジョブすべてで，到着順に並ぶ。

戻り値: `loop.tjobs` または `candidate_fw_tjobs` から選択した1件の `TJob`。

**デフォルト実装**: 到着順に見て，入庫ジョブ，または `precedence_filter` で絞った出庫ジョブの，最初のものを選択。

---

## ユーティリティメソッド

### `precedence_filter(tjobs) -> list[TJob]`

遅れている先行搬送ジョブ数（`TJob.late_preceding_tjob_count()`）が最小のジョブに絞る，ソフトな先行関係制約。並びは保たれ，空でないリストを渡せば結果も空にならない。

`dispatch_shuttle`・`dispatch_lift`・`dispatch_loop` に渡る候補は絞り込み前のものなので，使うかどうかは方策が選ぶ。デフォルト実装は先頭でこれを呼ぶ（作業場の順序制約そのものは物理的な制約で，ループからの荷卸しのときに別に確認される）。

> **API の変更（2026-11-25）**: 以前は，システム側がこの絞り込みをしてから候補を渡していた。`dispatch_*` を上書きしたコントローラで以前と同じ結果を得るには，各メソッドの先頭で `self.precedence_filter()` を呼ぶ。

使わない場合の注意:

- 荷卸しの失敗と再搬送が増えることがある
- 方策によっては，搬送がまったく進まなくなることがある（例: ループで常に出庫を優先し，絞り込みも使わない方策）。`env.run(until=env.simulation_completed | env.timeout(上限))` のように，実行時間の上限を設けるとよい

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

## 既知の性質: ライブロック

`precedence_filter` を使うデフォルトの方策でも，台車が少なく投入が多いと，動き続けているのに先へ進まない状態が長く続くことがある（例: `PJOB=100, SEED=3, VEHICLE=10, RELEASABLE=100` では，約 8 万秒かかった）。

- 系列の conv1 の末尾にいる TJob は，遅れている先行ジョブ数が大きいと，他の系列の末尾に候補がある限り選ばれない
- その後ろに，作業場の次に処理すべき TJob が並んでいても，追い越せないので候補に現れない
- 他の系列から選ばれた TJob は荷卸しに失敗して再搬送され，また候補に並ぶ

他の系列の候補がたまたま途切れると解消する。測った45回の実行（`VEHICLE` 10・20・30，`RELEASABLE` 25・50・100，5シード）はすべて終わったが，必ず終わる保証はない。

---

## 既知の不具合: PJob を ID 順以外で投入すると壊れる（要修正）

2026-11-29 に判明．**未修正**．修正するまで，`dispatch_pjobs` をオーバーライドするときは，PJob を `waiting_pjobs` の先頭から（ID 順に）投入し，選ぶのは作業場だけにすること．

`dispatch_pjobs` は `waiting_pjobs` から任意の PJob を選べる．一方，荷卸しの可否（`TJob.late_preceding_tjob_count()`）は，先行ジョブを投入の順ではなく `pjob.idx` の大小で決める（`TJob.get_preceding_tjobs()`．まだ投入されていない PJob は作業場が `None` なので含まれない）．このため，同じ作業場で番号の大きい PJob を先に投入すると，次のことが起きる．例は P5 を P3 より先に投入した場合．

- **作業場の順が入れ子になる**：P5 のジョブがいくつか荷卸しされた後で P3 が投入されると，P3 は P5 の残りより先になる（実際の順は「P5 の一部 → P3 → P5 の残り」）
- **行き詰まりうる**：バケットの割付けは投入の順に行われる（`PJob.release_pjob()`）．同じバケットを P5 のジョブ x が先に，P3 のジョブ y が後に使うとき，x が荷卸し前なら，x は先行ジョブ y の荷卸しを待ち，y は x がバケットを戻すのを待つので，搬送が止まる
- フローグラフの「閉路がないこと」の前提（バケットの割付けが PJob の順）も崩れる（[flow_graph.md](flow_graph.md)）

**対策の案**：投入した時点で PJob に投入の順の番号を振り（投入までは `None`），荷卸しの判定（`get_preceding_tjobs()` の「先行すべき PJob」）はその番号で行う．`pjob.idx` はログ・CSV 出力・グラフのキーなどで識別子として使っているので，番号は別の属性として持たせるのがよい．`models.py` の変更なので，修正するときは golden test（`tests/test_regression.py`）が変わらないこと（ID 順に投入する既定の方策では結果が同じになるはず）を確かめる．

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
