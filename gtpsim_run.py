import sys, random
import simpy
from gtp import *

SEED = 123

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class MyConfig(DefaultConfig):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    CONSOLE_OUT = True  # コンソールへの書き出し

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    random.seed(SEED)
    env = create_simulator(config=MyConfig)
    env.run(until=env.gameover)
    for station in env.gtps.stations:
        print(f'Staion {station.picker}: makespan {station.makespan:.2f} utilization {station.get_utilization():.4f}')

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
