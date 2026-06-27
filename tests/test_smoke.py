from gtp import create_simulator


def test_simulation_completes():
    """最小パラメータでシミュレーションが完走する"""
    env = create_simulator(params={'PJOB': 5, 'SEED': 42})
    env.run(until=env.simulation_completed)
    assert env.simulation_completed.processed


def test_all_stations_have_results():
    """全作業場に makespan と utilization が記録されている"""
    env = create_simulator(params={'PJOB': 5, 'SEED': 42})
    env.run(until=env.simulation_completed)
    for station in env.gtps.stations:
        assert station.makespan > 0
        assert 0 < station.get_utilization() <= 1.0
