import pytest
from test_operation_graph import PARAMS, run_with_snapshots


@pytest.fixture(scope='session', params=PARAMS, ids=str)
def snapshot_run(request):
    """条件ごとに1回だけ実行し，スナップショットをテスト間で使い回す（テストは書き換えないこと）"""
    env, snapshots = run_with_snapshots(request.param)
    return request.param, env, snapshots
