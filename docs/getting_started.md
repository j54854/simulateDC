# はじめに

このドキュメントは，新しくプロジェクトに加わったメンバーが，シミュレータの全体像を把握し，最初の実験を実行できるようになるまでを案内します．

---

## このシミュレータでできること

**simulateDC** は，GTP（Goods-to-Person）方式の物流センターを模擬する離散イベントシミュレータです．

GTPシステムは以下の設備で構成されています：

```
倉庫（Store）
  ↕ シャトル（Shuttle）  ← 各系列・各階層に1台
  ↕ コンベヤ（Conveyor）
  ↕ リフト（Lift）       ← 各系列に1基
  ↕ コンベヤ（Conveyor）
  ↔ ループ（Loop）       ← 複数台車が周回
  ↕ コンベヤ（Conveyor）
  ↕ 作業場（Station）    ← ピッキングが行われる
```

バケットと呼ばれるコンテナが自動倉庫と作業場の間を往復し，ピッキング作業が完了するまでの時間や作業場の稼働率を測定できます．

**研究での主な用途**:
- 作業時間の分布・機器の速度・制御ロジックなどのパラメータを変えたときの性能比較
- 独自の搬送制御ロジックを実装して，制御方策の性能評価

---

## セットアップ

リポジトリをクローンしてから，以下を実行します．

```bash
uv python pin 3.12
uv add -r requirements.txt
uv sync
```

`uv sync` で必要なパッケージ（SimPy・pygame）と `gtp` パッケージがインストールされます．

---

## 最初の実行

```bash
uv run examples/run_gtpsim.py
```

実行が完了すると，各作業場の結果が表示されます：

```
Station 0: makespan 775.60 utilization 0.5926
Station 1: makespan 951.56 utilization 0.4132
...
```

| 指標 | 意味 |
|------|------|
| `makespan` | 最初のジョブ投入から全ジョブ完了までの時間（秒） |
| `utilization` | 作業場の稼働率（0〜1） |

シミュレーションの様子を動画で確認したい場合は：

```bash
uv run examples/show_gtpsim.py
```

---

## パラメータを変えてみる

`examples/run_gtpsim.py` の `params` 辞書を編集することで，設備規模や制御パラメータを変更できます．

```python
params = {
    'SEED': 1234,
    'PJOB': 100,       # ピッキングジョブ数
    'VEHICLE': 30,     # ループの台車数
    'OPENABLE': 5,     # 同時開封可能バケット数
    'RELEASABLE': 50,  # システム内同時投入ジョブ数上限
    'REPEATABLE': 3,   # 許容する荷卸し失敗回数
}
```

指定しなかったパラメータはデフォルト値が使われます．パラメータの一覧と説明は [parameters.md](parameters.md) を参照してください．

---

## カスタムコントローラの実装

`DefaultController` をサブクラス化することで，独自の搬送制御ロジックを定義できます．

```python
from gtp import DefaultController, Progress

class MyController(DefaultController):

    def dispatch_shuttle(self, shuttle, candidate_tjobs):
        # エシェロン在庫の少ない作業場を優先する例
        candidate_tjobs = self.echelon_filter(candidate_tjobs, Progress.TO_FW_LIFT)
        return candidate_tjobs[0]

    # オーバーライドしないメソッドはデフォルト実装が使われる
```

実装したコントローラを `create_simulator` に渡して実行します：

```python
env = create_simulator(params=params, controller_cls=MyController)
env.run(until=env.simulation_completed)
```

`examples/run_gtpsim.py` の `MyController` が動く実装例です．オーバーライドするメソッドの仕様や，コントローラ内で参照できる属性の一覧は [controller_api.md](controller_api.md) を参照してください．

---

## 次のステップ

| ドキュメント | 内容 |
|------------|------|
| [parameters.md](parameters.md) | パラメータ一覧（デフォルト値・単位・説明） |
| [controller_api.md](controller_api.md) | コントローラの実装方法・参照可能な属性 |
