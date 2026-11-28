"""Absorb a predicted second opening shell and supply the missing midgame support."""
import sys
from math import hypot

from experiment_rows import run_rows
from logos_damage_response import evaluate
from observed_timeline import E,FIRST_GUN,J,L,M,S,T,W,cast,here,out,price,put,ready
from protected_bait import RESCUE_PROBE
from protected_delayed_eligibility import DelayedEligibility
from study import plan


class SecondBait(DelayedEligibility):
    def __init__(self,name):
        super().__init__('mature-windup' if name!='reserve-static' else 'mature-base')
        self.name_second=name
        self.eligible=False
        self.moved=False
        self.evacuated=False
        self.redeployed=False
        self.mid_cast=False
        self.early_jam=False
        self.field=False
        self.pressing=False

    @staticmethod
    def remove(b,frame,uid):
        b.actions[frame]=[a for a in b.actions[frame] if a['operator_id']!=uid]

    def __call__(self,b,act,stats):
        f=b.frame
        if 2760<=f<=3400:
            gun=b.live.get(FIRST_GUN)
            m=b.operators.get(M)
            first=next((end-90 for end,src,point,_,_ in b.hostile.shells if src.id==FIRST_GUN and point==(3,8)),None)
            if first is not None:
                self.eligible=first
            if (gun and L in b.operators and self.eligible and not m and not self.moved and f>=self.eligible+105
                    and f<=self.eligible+118 and b.dp>=price(b,M)):
                profile=b.hostile.profiles[gun.key]
                if hypot(gun.row-3,gun.col-8)<=profile['radius']+.1 and (3,8) not in b.occupied():
                    hp=round(gun.hp/2**32)
                    if hp>=(5000 if self.name_second=='reserve-all' else 10000):
                        for t in (3296,4900):
                            self.remove(b,t,M)
                        self.moved=True
                        act(put(f,M,'D9','l'))
                        stats['moved-mostima']=[f,hp]
            if m and self.moved and not self.evacuated:
                armed=next((end for end,src,point,_,_ in b.hostile.shells if src.id==FIRST_GUN and point==(m.row,m.col)),None)
                if armed and armed-f<=5:
                    act(out(f,M))
                    self.evacuated=True
                elif f==3399:
                    self.redeployed=True
        if self.evacuated and 4160<=f<=7100:
            if not self.early_jam and self.name_second!='reserve-static' and f<=5320 and b.inventory[J] and b.dp>=40:
                protected=any(e.id.startswith(('w0-f5-','w0-f6-')) and not e.cursor.hidden for e in b.live.values())
                if protected:
                    rc=(4,4) if self.name_second!='reserve-jam-right' else (4,8)
                    if rc not in b.occupied():
                        self.remove(b,5415,J)
                        act(put(f,J,'E5' if rc==(4,4) else 'E9','l'))
                        self.early_jam=True
            if self.name_second=='reserve-field' and not self.field and f>=6060 and T not in b.operators and f>=b.ready_at[T] and b.dp>=price(b,T):
                if (3,8) not in b.occupied():
                    self.field=True
                    self.remove(b,7020,T)
                    self.remove(b,7480,T)
                    act(put(f,T,'D9','l'))
            if not self.redeployed and f>=b.ready_at[M] and b.dp>=price(b,M):
                if (4,8) not in b.occupied():
                    act(put(f,M,'E9','l'))
                    self.redeployed=True
        if self.field and not self.pressing and 6300<=f<=7540:
            a=b.operators.get(T)
            if a and ready(a,f) and any(e.id.startswith(('w0-f8-','w0-f9-')) for e in b.targets(a,a.handler.wide)):
                act(cast(f,T))
                self.pressing=True
        if self.moved and not self.mid_cast and 4500<=f<=8655:
            a=b.operators.get(M)
            if a and ready(a,f) and any(e.id.startswith(('w0-f5-','w0-f6-','w0-f8-','w0-f9-')) for e in b.targets(a,a.handler.ranges[True])):
                if f<6000 or self.name_second=='reserve-field':
                    act(cast(f,M))
                    self.mid_cast=True
        return super().__call__(b,act,stats)


def play(job):
    return evaluate(job,SecondBait(job[0]))


if __name__=='__main__':
    pairs=RESCUE_PROBE[:5]+RESCUE_PROBE[-1:]
    run_rows(here/'protected-second-bait.jsonl',play,[(m,'probe',s,t) for s,t in pairs for m in sys.argv[1:]],4)
