# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

GTP（Goods-to-Person）方式の物流センターを模擬する SimPy ベースの離散イベントシミュレータ．パッケージは `gtp/` のみで，コアロジックはほぼすべて `gtp/models.py` に集約されている．可視化は pygame（`gtp/visualizer.py`）．

## コマンド

パッケージ管理は uv（Python 3.12）．`requirements.txt` は存在しないため，セットアップは `uv sync` のみでよい．

```bash
uv sync                                   # 依存関係（simpy, pygame, pytest）と gtp パッケージのインストール
uv run pytest tests/ -v                   # 全テスト
uv run pytest tests/test_regression.py::test_golden_output -v   # 単一テスト
uv run examples/run_gtpsim.py             # カスタムコントローラで実行し，作業場ごとの結果を表示
uv run examples/show_gtpsim.py            # pygame ウィンドウでアニメーション表示
uv run examples/show_tjob_graph.py --time 200   # 時刻200の TJob グラフを data/ に PNG で保存
```

CSV ログ（`data/gtpsim_log.csv`）は `env.gtps.dump_log()` で出力する（`run_gtpsim.py` ではコメントアウトされている）．

## アーキテクチャ

### 構築と実行の流れ

- `create_simulator(params, controller_cls)` が `Simulation_Environment` を生成し，`random.seed(env.SEED)` を設定してから `env.gtps = GTPSystem(...)` を構築する
- `Simulation_Environment` は `simpy.Environment` のサブクラスで，**全パラメータをクラス変数としてデフォルト定義**し，`params` 辞書の値を `setattr` でインスタンス変数として上書きする．各オブジェクトは `self.env.XXX` でパラメータを参照する
- 実行は `env.run(until=env.simulation_completed)`．全搬送ジョブが置場へ帰還した時点で `TJob.proceed()` 内から `simulation_completed` が発火する
- 他に大域イベント `tjob_picked`・`tjob_restored` があり，発火後に新しいイベントへ差し替えるパターン（`succeed()` → `self.env.xxx = self.env.event()`）を使う

### 設備トポロジ（`GTPSystem.__init__`）

`Store[aisle][floor][row]` → `Shuttle[aisle][floor]` → `Conveyor` stage 0 → `Lift[aisle][direction]` → stage 1 → `Loop`（`VEHICLE` 台の台車が周回）→ stage 2 → `Station[picker]`

- コンベヤは `conveyors[stage][dim0][dim1][direction]` の4次元リスト．次元は `CONV_DIM` で決まる
- リフトとコンベヤは出庫（`Direction.FORWARD`）・入庫（`BACKWARD`）別，シャトルは兼用
- ループとの接続ノードは `AISLE_SEG`・`PICKER_SEG` で固定指定されている
- 全ユニットは `Unit` を継承し，`Cell`（バケットを保持）と `Node`（経路グラフ）で構成される．各ユニットの `operate()`（ループ台車は `drive()`）が SimPy プロセスとして `register_processes()` で登録される

### ジョブモデル

- `PJob`（ピッキングジョブ）は複数の `TJob`（搬送ジョブ，1バケット分）を持つ
- `TJob.progress`（`Progress` enum，`TO_FW_SHTL`〜`DONE`）が搬送の進捗段階を表し，`proceed()` で1段階ずつ進む．荷卸し失敗時は `retry_needed` が立ち，`DONE` 到達時に `TO_FW_SHTL` に戻って再搬送される
- `GTPSystem.operate()` が `RELEASABLE` を上限に PJob を投入する

### コントローラ（拡張ポイント）

搬送制御は `DefaultController` に集約されており，各ユニットのプロセスから `dispatch_pjobs`・`dispatch_shuttle`・`dispatch_lift`・`dispatch_loop` が呼ばれる．研究用の制御方策はこれをサブクラス化して `controller_cls` に渡す（例: `examples/run_gtpsim.py` の `MyController`）．仕様の詳細は `docs/controller_api.md`．

### TJob グラフ（`gtp/graph.py`）

`TJobGraph(gtps).update()` が，システム内の TJob をノードとしてピッキング可能順序を表す `networkx.MultiDiGraph` を返す．`models.py` の状態を読むだけで書き換えず，`models.py` からは参照しない（コントローラ側から使う）．仕様は `docs/tjob_graph.md`．

## 注意点

- **乱数はグローバルな `random` モジュールを共有**している（ジョブ生成・作業時間・バケット配置・デフォルトコントローラ）．`random` の呼び出し順・回数を変えると結果が変わる
- `tests/test_regression.py::test_golden_output` は `PJOB=20, SEED=42` での makespan の期待値を固定している．`gtp/models.py` の変更でこれが落ちた場合，意図した挙動変更かどうかを確認してから期待値を更新すること
- `CONV_DIM` はクラス定義時のデフォルト `AISLE`・`PICKER` から計算されるため，`params` で `AISLE`・`PICKER`・`FLOOR` を変えても自動追従しない．設備規模を変える場合は `CONV_DIM`・`AISLE_SEG`・`PICKER_SEG`・`LOOP` も合わせて渡す必要がある
- コードスタイル: クラス・関数の区切りに `# ----- ...` の罫線コメントを入れ，コメントは行末に日本語で書く慣習．二項演算子は `a -b`・`a *b` のように右側のみ空ける書き方が多い

## ドキュメント

- `docs/getting_started.md` — 全体像とセットアップ
- `docs/parameters.md` — パラメータのデフォルト値・単位・説明
- `docs/controller_api.md` — コントローラ API リファレンス
- `docs/tjob_lifecycle.md` — TJob の状態遷移と，各状態でのバケット所在・保持データ構造
- `docs/tjob_graph.md` — TJob グラフ（`gtp/graph.py`）の仕様．グラフ関連の作業前に必ず参照
