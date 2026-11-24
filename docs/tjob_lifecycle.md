# 搬送ジョブ（TJob）のライフサイクル

搬送ジョブ `TJob` が生成されてから完了するまでの状態遷移と，各状態でのバケットの所在・ジョブを保持しているデータ構造をまとめる．シミュレーション途中の状態を把握したり，コントローラを実装したりする際の参照用．

---

## 基本的な考え方

- `TJob.progress`（`Progress` enum）は **「次に完了すべき工程」** を表す．値が進むのは各工程の荷卸し（または帰還）が**完了した時点**であり，バケットの物理的な位置とは一致しない
- バケットの物理的な所在は `tjob.bucket.cell.unit` で取得できる（`Store`・`Shuttle`・`Conveyor`・`Lift`・`Loop`・`Station` のいずれか）
- 各ユニットには待ち行列 `unit.tjobs` がある．ユニットがジョブに着手するとリストから外れる
- 着手後の処理中ジョブは，シャトル・リフト・作業場・ループ台車のセルの `cell.assigned_tjob` で参照できる（例：`shuttle.cells[0].assigned_tjob`，`car.assigned_tjob`）．設定は割付け（迎車の開始）の時点，解除は荷卸し（または搬出）と復帰が終わった時点で，その機械を占有している搬送ジョブを表す．コンベヤと置場のセルでは常に `None` で，コンベヤ上のジョブは `cell.bucket.current_tjob()` で読む

---

## 生成

1. `GTPSystem.operate()` が，未ピッキングの搬送ジョブ数（`len(gtps.unpicked_tjobs)`）が `RELEASABLE` 未満のときに，`controller.dispatch_pjobs()` で選んだ `PJob` を投入する
2. `PJob.release_pjob()` が要求アイテムごとにバケットを選び，`TJob` を生成する（`progress = TO_FW_SHTL`，`released_time = env.now`）
3. `PJob.release_tjob()` が `bucket.tjobs` と，置場を担当するシャトルの `shuttle.tjobs` に登録する
4. `GTPSystem.operate()` が `gtps.unpicked_tjobs`・`gtps.unrestored_tjobs` に追加する

未投入の `PJob` の `tjobs` は空なので，存在する全搬送ジョブは `[t for p in gtps.pjobs for t in p.tjobs]` で列挙できる．

---

## 状態遷移の全体像

```
【往路】
TO_FW_SHTL ──(シャトル荷卸し)──▶ TO_FW_LIFT ──(リフト荷卸し)──▶ TO_FW_LOOP
    ──(台車積込み)──▶ ON_FW_LOOP ──(台車荷卸し)──▶ TO_PICKED ──(ピッキング完了)──▶ TO_BW_LOOP

【復路】
TO_BW_LOOP ──(台車積込み)──▶ ON_BW_LOOP ──(台車荷卸し)──▶ TO_BW_LIFT
    ──(リフト荷卸し)──▶ TO_BW_SHTL ──(シャトル帰還)──▶ DONE

【分岐】
ON_FW_LOOP ──(荷卸し失敗が REPEATABLE 回を超過，retry_needed = True)──▶ ON_BW_LOOP
TO_BW_LOOP ──(バケットが空)──▶ DONE
DONE       ──(retry_needed = True)──▶ TO_FW_SHTL（再搬送）
```

---

## 段階ごとの詳細

「保持」列は，その状態の搬送ジョブを参照しているデータ構造．`assigned_tjob` は，そのユニットのセルの `cell.assigned_tjob`．

| progress | 細分状態 | バケット所在 | 保持 | 次の段階への遷移箇所 |
|---|---|---|---|---|
| `TO_FW_SHTL` | ① バケットの順番待ち（同じバケットの別ジョブが先行中） | 任意 | `shuttle.tjobs` | |
| | ② 着手可能待ち（置場にあり，`bucket.current_tjob()` が自身） | `Store` | `shuttle.tjobs` | |
| | ③ シャトル割付け済み，空車で置場前へ移動中 | `Store` | `assigned_tjob` | |
| | ④ シャトルに積載し，待機ノードへ移動中 | `Shuttle` | `assigned_tjob` | `Shuttle.operate()`：出庫コンベヤへの荷卸し |
| `TO_FW_LIFT` | stage 0 の出庫コンベヤ上を移動中 | `Conveyor` | なし | |
| | コンベヤ末尾でリフト待ち | `Conveyor` | `lift.tjobs` | |
| | リフト割付け済み（空車移動 → 積載して0階へ） | `Conveyor` → `Lift` | `assigned_tjob` | `Lift.operate()`：stage 1 への荷卸し |
| `TO_FW_LOOP` | stage 1 の出庫コンベヤ上を移動中 | `Conveyor` | なし | |
| | コンベヤ末尾で台車割付け待ち | `Conveyor` | `loop.tjobs` | |
| | 台車割付け済み，`AISLE_SEG` へ移動中 | `Conveyor` | `car.assigned_tjob` | `Loop.drive()`：台車への積込み |
| `ON_FW_LOOP` | 台車に積載し，`PICKER_SEG` へ移動中 | `Loop` | `car.assigned_tjob` | `Loop.drive()`：stage 2 への荷卸し |
| | 荷卸し失敗（先行ジョブが遅れている）による周回中 | `Loop` | `car.assigned_tjob` | 失敗回数（`tjob.failure_count`）が `REPEATABLE` を超えると `Loop.handle_failure()` で `ON_BW_LOOP` へ |
| `TO_PICKED` | stage 2 の出庫コンベヤ上を移動中 | `Conveyor` | なし | |
| | コンベヤ末尾で作業場待ち | `Conveyor` | `station.tjobs` | |
| | 作業場へ搬入中 → ピッキング作業中 | `Conveyor` → `Station` | `assigned_tjob` | `Station.operate()`：作業完了（`picked_time`・`work_time` を記録） |
| `TO_BW_LOOP` | バケットが空の場合は即座に `DONE` | — | — | `Bucket.pick()` |
| | stage 2 の入庫コンベヤ上を移動中 | `Conveyor` | なし | |
| | コンベヤ末尾で台車割付け待ち → 台車割付け済み | `Conveyor` | `loop.tjobs` → `car.assigned_tjob` | `Loop.drive()`：台車への積込み |
| `ON_BW_LOOP` | 台車に積載し，`AISLE_SEG` の入庫側へ移動中 | `Loop` | `car.assigned_tjob` | `Loop.drive()`：stage 1 への荷卸し |
| `TO_BW_LIFT` | stage 1 の入庫コンベヤ上 → 末尾でリフト待ち | `Conveyor` | なし → `lift.tjobs` | |
| | リフト割付け済み，積載して目的階へ | `Conveyor` → `Lift` | `assigned_tjob` | `Lift.operate()`：stage 0 への荷卸し |
| `TO_BW_SHTL` | stage 0 の入庫コンベヤ上 → 末尾でシャトル待ち | `Conveyor` | なし → `shuttle.tjobs` | |
| | シャトル割付け済み，積載して置場前へ | `Conveyor` → `Shuttle` | `assigned_tjob` | |
| | 置場へ格納後，シャトルが待機ノードへ帰還中 | `Store` | `assigned_tjob` | `Shuttle.operate()`：待機ノードへの帰還 |
| `DONE` | 再搬送が必要なら `TO_FW_SHTL` に戻り，`shuttle.tjobs` に再登録 | `Store` | `shuttle.tjobs` | `TJob.proceed()` |
| | 完了（`restored_time` を記録，`unrestored_tjobs` から削除） | `Store` | なし | |

コンベヤ上のジョブはどのユニットの `tjobs` にも属さない（`Conveyor.tjobs` は使われない）．`Conveyor.operate()` は，バケットが末尾セルに到着した時点で，接続先ユニットの `tjobs` に `bucket.current_tjob()` を追加する．

---

## システム全体の集合

| データ構造 | 追加 | 削除 |
|---|---|---|
| `gtps.unpicked_tjobs` | 投入時 | ピッキング完了時（`Bucket.pick()`） |
| `gtps.unrestored_tjobs` | 投入時 | 再搬送なしで `DONE` になった時（`TJob.proceed()`） |
| `bucket.tjobs` | 投入時 | バケットが空になった時に全削除（`Bucket.pick()`） |

全搬送ジョブが `unrestored_tjobs` から外れ，かつ全ピッキングジョブが投入済みになると `simulation_completed` が発火する．

---

## 注意点

- **処理中ジョブは `assigned_tjob` で読む**：迎車中（バケットはまだ前のセルにある）や復帰中（入庫シャトル・入庫リフトの帰還中）も設定されている．「割付け済みで処理中」と「待ち行列で待機中」は，`assigned_tjob` と `unit.tjobs` で区別できる
- **DONE と置場への格納はずれる**：入庫時は，バケットが置場に格納された後，シャトルが待機ノードへ戻るまで `TO_BW_SHTL` のまま
- **`TO_FW_SHTL` の待ちは2種類**：1つのバケットに複数の搬送ジョブが割り付けられることがあり，`bucket.current_tjob()` でないジョブはバケットの帰還を待っている．着手可能でも先行関係制約（`Unit.precedence_filter()`）で後回しにされることがある
- **再搬送では同じ TJob が経路を2周以上する**：`retry_needed == True` の間（`ON_BW_LOOP` 〜 `TO_BW_SHTL`）はピッキングせずに置場へ戻る途中．`picked_time` は `None` のままで，`unpicked_tjobs` にも残る．回数は `retry_count` に記録される
- **空になったバケット**：ピッキングでバケットが空になると，入庫経路を通らず即座に置場へ戻され（新バケット扱い），`bucket.tjobs` はクリアされる．`tjob.bucket` の参照は残る
- **入庫リフトの参照**：入庫リフトは荷卸し後，0階へ戻るまで（既に `TO_BW_SHTL` になった）ジョブを `assigned_tjob` に保持し続ける（復帰中の占有）
- **記録される時刻**：`released_time`・`picked_time`・`restored_time`・`work_time` に加えて，次の記録がある（「時刻の記録」）

---

## 時刻の記録

### 実行中の作業の残り時間（`cell.hold_start`・`cell.hold_end`）

各ユニットの時間遅れ（`env.timeout`）は，すべて作業の主体のセルの `Cell.hold(delay)` を通す．`hold` は開始時刻 `hold_start` と終了時刻 `hold_end` を記録してから `env.timeout(delay)` を返す．主体のセルは，シャトル・リフト・作業場では `unit.cells[0]`，コンベヤでは送り出すセル，ループでは台車である．

- 実行中の作業の残り時間は `max(0, cell.hold_end - env.now)` で読める．`hold_end < env.now` なら，そのセルは作業をしておらず，待っている（または待機中）
- ピッキング中は，作業場のセルの `hold_end` が実現値のピッキング終了時刻になる

### 荷卸し失敗の回数（`tjob.failure_count`）

今回の往路での，ループからの荷卸しの失敗回数．`Loop.drive()` の荷卸し失敗で増え，`REPEATABLE` を超えると再搬送になる．再搬送で往路をやり直すとき（`TJob.proceed()` で `TO_FW_SHTL` に戻るとき）に 0 に戻る．ピッキングできた後は，記録として値が残る．

### 履歴（`tjob.history`）

`Stamp(time, kind, src, dst, progress)` のリスト．`progress` は記録した時点の進捗である．

| kind | 記録する場所 | src | dst |
|---|---|---|---|
| `assign` | `Cell.assign_tjob()`（割付け＝迎車の開始．作業場では搬入の開始） | — | 主体のセル |
| `release` | `Cell.release_tjob()`（荷卸し・搬出と復帰の終わり） | — | 主体のセル |
| `transfer` | `Cell.transfer()`（移載の終了） | 移載元のセル | 移載先のセル |
| `fail` | 荷卸し失敗 | — | 台車 |
| `proceed` | `TJob.proceed()`（進捗の変化．再搬送では `TO_FW_SHTL` に戻った後の進捗） | — | — |

- 移載の直前の作業（積替え・セル間移動・搬入・搬出）は処理時間が固定なので，開始時刻は「移載の終了 − 処理時間」で逆算できる．移載の前に受け手の空きを待つ場合も，待ちは処理時間の前に入るので，この逆算は正しい
- 作業ブロックのポート（[tjob_graph.md](tjob_graph.md) の「作業ブロックのポート」）は次のように読める
  - Y（荷卸しの開始）：台車 → conv2^FW[0] の `transfer` の時刻 − `LT_LOOP`
  - P（搬入の開始）：conv2^FW の末尾 → 作業場の `transfer` の時刻 − `MT_CONV`
  - Q（搬出の完了）：作業場 → conv2^BW[0] の `transfer` の時刻（空になるバケットは，ピッキング完了の `proceed` の時刻）
  - O（積み出しの完了）：conv2^BW の末尾 → 台車の `transfer` の時刻
- 空になったバケットを置場へ戻す処理（`Bucket.pick()`）は移載ではないので，`transfer` は記録されない（`proceed` で `DONE` が残る）
