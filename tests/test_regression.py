from gtp import create_simulator


def test_seed_reproducibility():
    """同一シードで2回実行した結果が一致する"""
    params = {'PJOB': 20, 'SEED': 42}

    env1 = create_simulator(params=params)
    env1.run(until=env1.simulation_completed)

    env2 = create_simulator(params=params)
    env2.run(until=env2.simulation_completed)

    makespans1 = [s.makespan for s in env1.gtps.stations]
    makespans2 = [s.makespan for s in env2.gtps.stations]
    assert makespans1 == makespans2


def test_golden_output():
    """既知の正解値と一致する（この値が変わったらコアロジックの変更を要確認）"""
    env = create_simulator(params={'PJOB': 20, 'SEED': 42})
    env.run(until=env.simulation_completed)
    makespans = [round(s.makespan, 2) for s in env.gtps.stations]
    assert makespans == [361.22, 148.02, 132.42, 189.7, 146.14]
