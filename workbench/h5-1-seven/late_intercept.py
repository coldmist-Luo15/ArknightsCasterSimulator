"""Let the first artillery windup pick its original target before dropping the bait."""
import sys
from math import hypot

from experiment_rows import run_rows
from logos_damage_response import evaluate
from observed_timeline import B,E,FIRST_GUN,L,M,cast,here,out,price,put
from replay_southern_policy import Tail
from study import plan
from tools.run_caster_seeds import trial_seeds


class Intercept(Tail):
    alternate_opening=True
    checkpoint_frame=2701

    def __init__(self,label):
        super().__init__("budget-screen")
        self.label=label
        self.flags=set()

    def prepare(self,q):
        hold=[3054,6426] if self.label=="intercept-hold" else []
        return plan(q,drop=[2899,2953]+hold)

    def __call__(self,b,act,stats):
        f=b.frame
        def issue(tag,action):
            self.flags.add(tag)
            act(action)
        gun=b.live.get(FIRST_GUN)
        if 2760<=f<=3210 and gun and "bait" not in self.flags:
            clock=b.hostile.states.get(FIRST_GUN)
            if clock and clock.release and 1<=clock.release-f<=4 and clock.targets:
                if not b.disarmed(gun) and b.dp>=price(b,E) and b.ready_at[E]<=f:
                    for row,col,tile in ((3,8,"D9"),(4,8,"E9")):
                        if (row,col) not in b.occupied() and hypot(gun.row-row,gun.col-col)<=2.22:
                            impact=clock.release+90
                            if 3296<=impact<=3370:
                                b.actions[3296]=[a for a in b.actions[3296] if a["operator_id"]!=M]
                                act(put(impact+1,M,"E9","l"))
                            issue("bait",put(f,E,tile,"l"))
                            break
        if 2760<=f<=3230 and E in b.operators and "bait" in self.flags and "redeem" not in self.flags:
            eye=b.operators[E]
            if any(origin==(eye.row,eye.col) and source.id==FIRST_GUN for _,source,origin,_,_ in b.hostile.shells):
                issue("redeem",out(f,E))
            elif gun is None or f==3230:
                issue("redeem",out(f,E))
        if self.label=="intercept-hold":
            if 3054<=f<=3200 and B in b.operators and "left_mar" not in self.flags:
                if gun is None or f==3200:
                    issue("left_mar",out(f,B))
            if 6426<=f<=7150 and "second_mar" not in self.flags and B not in b.operators and b.ready_at[B]<=f and b.dp>=price(b,B):
                if (5,6) not in b.occupied():
                    issue("second_mar",put(f,B,"F7","l"))
        return super().__call__(b,act,stats)


def play(job):
    return evaluate(job,None if job[0]=="safehandoff" else Intercept(job[0]))


if __name__=="__main__":
    mode,tag=sys.argv[1:3]
    pairs=[(588925228,398117685),(1935015943,1434738493),(628155088,196929602),(1257960646,842931806),
           (499045565,1816409424),(1727549616,1664913877),(1106834848,1419395519),(1601499280,1104354708)]
    if mode=="sample":
        pairs=list(trial_seeds(int(sys.argv[3]),int(tag)))
    run_rows(here/f"intercept-{tag}-{mode}.jsonl",play,[(name,mode,a,b) for name in sys.argv[4:] for a,b in pairs],4)
