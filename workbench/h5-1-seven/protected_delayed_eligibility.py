"""Let a bait become selectable during the first cannon's natural windup."""
import sys
from math import hypot

from experiment_rows import run_rows
from logos_damage_response import evaluate
from observed_timeline import E,FIRST_GUN,L,here,out,price,put
from protected_bait import CORE_PROBE,RESCUE_PROBE,ProtectedBait
from study import plan
from tools.run_caster_seeds import trial_seeds


class DelayedEligibility(ProtectedBait):
    checkpoint_frame=2590

    def __init__(self,name):
        super().__init__('hold-grant')
        self.test=name
        self.issued=False
        self.pulled=False
        self.wait={'mature-early':30,'mature-windup':24,'mature-late':20,'mature-conservative':26}.get(name)

    def prepare(self,q):
        q=super().prepare(q)
        if self.wait:
            q=plan(q,drop=[2869,2953])
        return q

    def __call__(self,b,act,stats):
        f=b.frame
        if self.wait and 2590<=f<=3170:
            cannon=b.live.get(FIRST_GUN)
            logo=b.operators.get(L)
            eyja=b.operators.get(E)
            if cannon and logo and not self.issued and f<=3075 and b.dp>=price(b,E):
                phase=b.hostile.states.get(FIRST_GUN)
                profile=b.hostile.profiles.get(cannon.key)
                if profile and phase and phase.release is None:
                    r0,c0=cannon.previous
                    dr,dc=cannon.row-r0,cannon.col-c0
                    dy,dx=cannon.row-logo.row,cannon.col-logo.col
                    radius=profile['radius']+b.hostile._body(logo)[0]
                    # Keep the upper side clear of the splash around the primary damage dealer.
                    lookahead=self.wait
                    if self.test=='mature-conservative' and f<2743:
                        lookahead=14
                    approaching=dy*dr+dx*dc<0
                    soon=hypot(dy+dr*lookahead,dx+dc*lookahead)<=radius
                    in_d9=hypot(cannon.row+30*dr-3,cannon.col+30*dc-8)<=profile['radius']+.15
                    if approaching and soon and in_d9:
                        self.issued=True
                        act(put(f,E,'D9','l'))
                        stats['first-bait-projection']=[f,round(cannon.col,3),round(dc,4),lookahead,profile['wait']]
            if eyja and not self.pulled:
                marked=next((impact for impact,gun,center,_,_ in b.hostile.shells if gun.id==FIRST_GUN and center==(eyja.row,eyja.col)),None)
                if marked and (self.test!='mature-late' or marked-f<=7) or (FIRST_GUN in b.enemies and not cannon) or f>=3148:
                    act(out(f,E))
                    self.pulled=True
        return super().__call__(b,act,stats)


def play(job):
    return evaluate(job,DelayedEligibility(job[0]))


if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='probe':
        pairs=RESCUE_PROBE[:5]+RESCUE_PROBE[-1:]
    elif mode=='fresh':
        pairs=list(trial_seeds(64,282658))
    else:
        pairs=CORE_PROBE
    jobs=[(m,mode,s,t) for s,t in pairs for m in sys.argv[2:]]
    run_rows(here/f'protected-delayed-eligibility-{mode}.jsonl',play,jobs,4)
