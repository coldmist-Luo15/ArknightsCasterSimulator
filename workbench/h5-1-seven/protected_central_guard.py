"""Use Mostima's ready skill to interrupt the central cannon while Mantra is alive."""
import sys

from experiment_rows import run_rows
from logos_damage_response import evaluate
from observed_timeline import B,M,T,cast,here,ready
from protected_bait import CORE_PROBE
from protected_delayed_eligibility import DelayedEligibility
from replay_southern_policy import MID
from study import plan
from tools.run_caster_seeds import trial_seeds


CASES=(*CORE_PROBE,(1599982897,2110473975),(958232254,7021251),(190896138,2002206111),(1172317822,1979569853))


class CentralGuard(DelayedEligibility):
    def __init__(self,name):
        super().__init__('mature-windup' if name.endswith('-aim') else 'mature-base')
        self.mid_active='guard' in name and not name.startswith('book-only')
        self.arm=name.startswith('anticipate')
        self.guard_cast=False
        self.book_frame=int(name.split('-')[1]) if name.startswith('book-8') else None

    def prepare(self,q):
        q=super().prepare(q)
        if self.book_frame:
            q=plan(q,drop=[8271],add=[cast(self.book_frame,B)])
        return q

    def __call__(self,b,act,stats):
        f=b.frame
        if self.mid_active and not self.guard_cast and 7970<=f<8656:
            gun=b.live.get(MID)
            clock=b.hostile.states.get(MID)
            key=b.operators.get(M)
            truth=b.operators.get(T)
            if (gun and clock and key and truth and truth.handler.active and MID in truth.handler.members
                    and (key.row,key.col) in truth.handler.wide and ready(key,f)
                    and truth.handler.grants < int(truth.handler.board['max_trigger_cnt'])):
                threat=(clock.release and clock.release>f and not b.mantra.can_interrupt(gun)
                         and any(a.uid in (T,M) for a in clock.targets))
                anticipate=self.arm and f==8230 and gun.hp>37500*2**32 and not b.mantra.can_interrupt(gun)
                if threat or anticipate:
                    b.actions[8656]=[a for a in b.actions[8656] if a['operator_id']!=M]
                    act(cast(f,M))
                    self.guard_cast=True
                    stats['central-guard']=[f,clock.release,round(gun.hp/2**32),truth.handler.grants]
        return super().__call__(b,act,stats)


def play(job):
    return evaluate(job,CentralGuard(job[0]))


if __name__=='__main__':
    mode=sys.argv[1]
    pairs=list(trial_seeds(64,282671)) if mode=='fresh' else CASES
    run_rows(here/f'protected-central-guard-{mode}.jsonl',play,[(name,mode,s,t) for s,t in pairs for name in sys.argv[2:]],4)
