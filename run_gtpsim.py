import random
from gtp import *

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
class MyController(DefaultController):
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch_pjobs(self, waiting_pjobs, last_picker):
        next_pjob = waiting_pjobs.pop(0)  # デフォルトはFIFO
        unpicked = [
            len([tjob for tjob in self.gtps.unpicked_tjobs if tjob.pjob.station.picker == picker])
            for picker in range(self.env.PICKER)]
        next_picker = unpicked.index(min(unpicked))
        return next_pjob, next_picker
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch_shuttle(self, shuttle, candidate_tjobs):
        # 搬送ジョブのエシェロン在庫が少ない作業場を優先
        candidate_tjobs = self.echelon_filter(candidate_tjobs, self.env.TO_FW_LIFT)
        # ピッキングジョブのインデックスが最小のものだけに絞る
        pjob_idx = [tjob.pjob.idx for tjob in candidate_tjobs]
        candidate_tjobs = [tjob for tjob in candidate_tjobs if tjob.pjob.idx == min(pjob_idx)]
        return random.choice(candidate_tjobs)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch_lift(self, lift, candidate_tjobs):
        # 搬送ジョブのエシェロン在庫が少ない作業場を優先
        candidate_tjobs = self.echelon_filter(candidate_tjobs, self.env.TO_FW_LOOP)
        # ピッキングジョブのインデックスが最小のものだけに絞る
        pjob_idx = [tjob.pjob.idx for tjob in candidate_tjobs]
        candidate_tjobs = [tjob for tjob in candidate_tjobs if tjob.pjob.idx == min(pjob_idx)]
        return random.choice(candidate_tjobs)
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    def dispatch_loop(self, loop, candidate_fw_tjobs):
        candidate_fw_tjobs = self.echelon_filter(candidate_fw_tjobs, self.env.ON_FW_LOOP)
        for tjob in loop.tjobs:  # 入出庫両方の搬送ジョブリストを到着順に前から順に見ていく
            if tjob.progress == self.env.TO_BW_LOOP:  # 入庫ジョブなら
                return tjob  # それを実行
            else:  # 出庫ジョブなら
                if tjob in candidate_fw_tjobs:
                    return tjob
        assert False, 'No transfer job to dispatch'

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
def main():
# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
    params = {
        'SEED': 1234,
        'CONSOLE_OUT': False,  # コンソールへの書き出し
        'PJOB': 100, # ピッキングジョブ数
        'VEHICLE': 30,  # ループの台車数
        'OPENABLE': 5,  # 同時に開封可能なバケット数
        'RELEASABLE': 50,  # GTPシステムに投入可能な搬送ジョブ数
        'REPEATABLE': 3,  # 許容される荷卸し失敗回数（これを超えると置場に戻される）
        }
    env = create_simulator(params=params, controller_cls=MyController)
    env.run(until=env.simulation_completed)
    for station in env.gtps.stations:
        print(f'Staion {station.picker}: makespan {station.makespan:.2f} utilization {station.get_utilization():.4f}')

    # env.gtps.dump_log()

# ----- ----- ----- ----- ----- ----- ----- ----- ----- -----
if __name__ == '__main__':
    main()
