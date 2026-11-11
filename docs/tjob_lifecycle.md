# 搬送ジョブ（TJob）のライフサイクル

搬送ジョブ `TJob` が生成されてから完了するまでの状態遷移と，各状態でのバケットの所在・ジョブを保持しているデータ構造をまとめる．シミュレーション途中の状態を把握したり，コントローラを実装したりする際の参照用．

---

## 基本的な考え方

- `TJob.progress`（`Progress` enum）は **「次に完了すべき工程」** を表す．値が進むのは各工程の荷卸し（または帰還）が**完了した時点**であり，バケットの物理的な位置とは一致しない
- バケットの物理的な所在は `tjob.bucket.cell.unit` で取得できる（`Store`・`Shuttle`・`Conveyor`・`Lift`・`Loop`・`Station` のいずれか）
- 各ユニットには待ち行列 `unit.tjobs` がある．ユニットがジョブに着手するとリストから外れる
- 着手後の処理中ジョブは，シャトル・リフト・作業場では `operate()` のローカル変数 `current_tjob` にのみ保持され，外部から参照できない．ループ台車のみ `car.tjob`（`Loop.cells[i].tjob`）で参照可能

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

「保持」列は，その状態の搬送ジョブを参照しているデータ構造．「ローカル」は `operate()` のローカル変数のみが参照している状態．

| progress | 細分状態 | バケット所在 | 保持 | 次の段階への遷移箇所 |
|---|---|---|---|---|
| `TO_FW_SHTL` | ① バケットの順番待ち（同じバケットの別ジョブが先行中） | 任意 | `shuttle.tjobs` | |
| | ② 着手可能待ち（置場にあり，`bucket.current_tjob()` が自身） | `Store` | `shuttle.tjobs` | |
| | ③ シャトル割付け済み，空車で置場前へ移動中 | `Store` | ローカル | |
| | ④ シャトルに積載し，待機ノードへ移動中 | `Shuttle` | ローカル | `Shuttle.operate()`：出庫コンベヤへの荷卸し |
| `TO_FW_LIFT` | stage 0 の出庫コンベヤ上を移動中 | `Conveyor` | なし | |
| | コンベヤ末尾でリフト待ち | `Conveyor` | `lift.tjobs` | |
| | リフト割付け済み（空車移動 → 積載して0階へ） | `Conveyor` → `Lift` | ローカル | `Lift.operate()`：stage 1 への荷卸し |
| `TO_FW_LOOP` | stage 1 の出庫コンベヤ上を移動中 | `Conveyor` | なし | |
| | コンベヤ末尾で台車割付け待ち | `Conveyor` | `loop.tjobs` | |
| | 台車割付け済み，`AISLE_SEG` へ移動中 | `Conveyor` | `car.tjob` | `Loop.drive()`：台車への積込み |
| `ON_FW_LOOP` | 台車に積載し，`PICKER_SEG` へ移動中 | `Loop` | `car.tjob` | `Loop.drive()`：stage 2 への荷卸し |
| | 荷卸し失敗（先行ジョブが遅れている）による周回中 | `Loop` | `car.tjob` | 失敗回数が `REPEATABLE` を超えると `Loop.handle_failure()` で `ON_BW_LOOP` へ |
| `TO_PICKED` | stage 2 の出庫コンベヤ上を移動中 | `Conveyor` | なし | |
| | コンベヤ末尾で作業場待ち | `Conveyor` | `station.tjobs` | |
| | 作業場へ搬入中 → ピッキング作業中 | `Conveyor` → `Station` | ローカル | `Station.operate()`：作業完了（`picked_time`・`work_time` を記録） |
| `TO_BW_LOOP` | バケットが空の場合は即座に `DONE` | — | — | `Bucket.pick()` |
| | stage 2 の入庫コンベヤ上を移動中 | `Conveyor` | なし | |
| | コンベヤ末尾で台車割付け待ち → 台車割付け済み | `Conveyor` | `loop.tjobs` → `car.tjob` | `Loop.drive()`：台車への積込み |
| `ON_BW_LOOP` | 台車に積載し，`AISLE_SEG` の入庫側へ移動中 | `Loop` | `car.tjob` | `Loop.drive()`：stage 1 への荷卸し |
| `TO_BW_LIFT` | stage 1 の入庫コンベヤ上 → 末尾でリフト待ち | `Conveyor` | なし → `lift.tjobs` | |
| | リフト割付け済み，積載して目的階へ | `Conveyor` → `Lift` | ローカル | `Lift.operate()`：stage 0 への荷卸し |
| `TO_BW_SHTL` | stage 0 の入庫コンベヤ上 → 末尾でシャトル待ち | `Conveyor` | なし → `shuttle.tjobs` | |
| | シャトル割付け済み，積載して置場前へ | `Conveyor` → `Shuttle` | ローカル | |
| | 置場へ格納後，シャトルが待機ノードへ帰還中 | `Store` | ローカル | `Shuttle.operate()`：待機ノードへの帰還 |
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

- **処理中ジョブは外部から見えない**：シャトル・リフト・作業場の `current_tjob` はローカル変数のため，「割付け済みで処理中」と「待ち行列で待機中」は，待ち行列への所属の有無から間接的に判断するしかない
- **DONE と置場への格納はずれる**：入庫時は，バケットが置場に格納された後，シャトルが待機ノードへ戻るまで `TO_BW_SHTL` のまま
- **`TO_FW_SHTL` の待ちは2種類**：1つのバケットに複数の搬送ジョブが割り付けられることがあり，`bucket.current_tjob()` でないジョブはバケットの帰還を待っている．着手可能でも先行関係制約（`Unit.precedence_filter()`）で後回しにされることがある
- **再搬送では同じ TJob が経路を2周以上する**：`retry_needed == True` の間（`ON_BW_LOOP` 〜 `TO_BW_SHTL`）はピッキングせずに置場へ戻る途中．`picked_time` は `None` のままで，`unpicked_tjobs` にも残る．回数は `retry_count` に記録される
- **空になったバケット**：ピッキングでバケットが空になると，入庫経路を通らず即座に置場へ戻され（新バケット扱い），`bucket.tjobs` はクリアされる．`tjob.bucket` の参照は残る
- **入庫リフトの参照**：入庫リフトは荷卸し後，0階へ戻るまで（既に `TO_BW_SHTL` になった）ジョブをローカル変数で保持し続ける
- **記録される時刻**：`released_time`・`picked_time`・`restored_time`・`work_time` のみで，工程ごとの到着・着手時刻は記録されない
