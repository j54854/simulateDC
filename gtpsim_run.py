import sys, random
import simpy
from gtp import *

SEED = 123

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class MyShuttle(Shuttle):  # シャトルユニット
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch(self, candidate_tjobs):
        # 搬送ジョブのエシェロン在庫が少ない作業場を優先
        candidate_tjobs = self.echelon_filter(candidate_tjobs, TO_FW_LIFT)
        pjob_idx = [tjob.pjob.idx for tjob in candidate_tjobs]  # ピッキングジョブのインデックス
        # が最小のものだけに絞る
        candidate_tjobs = [tjob for tjob in candidate_tjobs if tjob.pjob.idx == min(pjob_idx)]
        return random.choice(candidate_tjobs)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class MyLift(Lift):  # リフトユニット
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch(self, candidate_tjobs):
        candidate_tjobs = self.echelon_filter(candidate_tjobs, TO_FW_LOOP)
        pjob_idx = [tjob.pjob.idx for tjob in candidate_tjobs]  # ピッキングジョブのインデックス
        # が最小のものだけに絞る
        candidate_tjobs = [tjob for tjob in candidate_tjobs if tjob.pjob.idx == min(pjob_idx)]
        return random.choice(candidate_tjobs)

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class MyLoop(Loop):  # ループユニット
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch(self, candidate_fw_tjobs):
        candidate_fw_tjobs = self.echelon_filter(candidate_fw_tjobs, ON_FW_LOOP)
        for tjob in self.tjobs:  # 前から順に見ていく
            if tjob.progress == TO_BW_LOOP:  # 入庫ジョブなら
                return tjob  # それを実行
            else:  # 出庫ジョブなら
                if tjob in candidate_fw_tjobs:
                    return tjob
        assert False, 'No transfer job to dispatch'

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class MyGTPSystem(GTPSystem):  # GTPシステム
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch(self, waiting_pjobs, last_picker):  # ピッキングジョブの投入ルール
        next_pjob = waiting_pjobs.pop(0)  # デフォルトはFIFO
        unpicked = [
            len([tjob for tjob in self.unpicked_tjobs if tjob.pjob.station.picker == picker])
            for picker in range(PICKER)]
        next_picker = unpicked.index(min(unpicked))
        return next_pjob, next_picker

# ---------- * ---------- * ---------- * ---------- * ----------
def create_simulator():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    env = simpy.Environment()

    env.CONSOLE_OUT = True  # コンソールへの書き出し
    env.PJOB = 10 # ピッキングジョブ数
    env.VEHICLE = 30  # ループの台車数
    env.OPENABLE = 5  # 同時に開封可能なバケット数
    env.RELEASABLE = 50  # GTPシステムに投入可能な搬送ジョブ数
    env.FAILURE_LIMIT = 3  # 許容される荷卸し失敗回数（これを超えると置場に戻される）

    env.gtps = GTPSystem(env)
    # env.gtps = MyGTPSystem(env, shtl_cls=MyShuttle, lift_cls=MyLift, loop_cls=MyLoop)
    env.gameover = env.event()
    return env

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    random.seed(SEED)
    env = create_simulator()
    env.run(until=env.gameover)
    for station in env.gtps.stations:
        print(f'Staion {station.picker}: makespan {station.makespan:.2f} utilization {station.get_utilization():.4f}')

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
