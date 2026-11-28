"""Set the first bait far enough ahead of the shell to expire deploy protection."""
import sys
from math import hypot

from experiment_rows import run_rows
from logos_damage_response import evaluate
from observed_timeline import B,E,FIRST_GUN,L,M,SPONSOR,T,W,cast,here,out,price,put,ready
from replay_southern_policy import MID,SOUTH,WING
from snow_inbound import PROBES,SnowInbound
from study import plan
from tools.run_caster_seeds import trial_seeds


ANCHORS=(*PROBES,(625787789,453227734),(1407735752,1982040983),(917981655,1482109289),(1906049810,20327990))
PROBE=(ANCHORS[0],ANCHORS[1],ANCHORS[2],ANCHORS[3],ANCHORS[6],ANCHORS[8],ANCHORS[-4],ANCHORS[-3],ANCHORS[-2],ANCHORS[-1])
CORE_PROBE=((1628992184,818739297),(1207720657,489370134),(1531638222,751384477),(1906049810,20327990),
            (991248629,955226368),(878160448,1052129971),(1624788854,1009010171),(1407735752,1982040983))
RESCUE_PROBE=((1283021025,1192836086),(338834029,808196347),(577475341,1623893665),(1172317822,1979569853),
              (1935015943,1434738493),(625787789,453227734),(589476853,1527080212),(1407735752,1982040983))


class ProtectedBait(SnowInbound):
    def __init__(self,name):
        super().__init__('normal-snow-inbound-reserve')
        self.early_core=name.startswith('preheat-core')
        self.rescue=name.startswith('rescue-book')
        self.forecast=name.startswith('forecast')
        self.head={'wolves-to-south':'d','wolves-to-east':'r'}.get(name)
        self.coupled=name.startswith('coupled') or name=='hold-grant' or self.early_core or self.rescue or self.forecast or self.head
        self.after_paralysis=name.startswith('after-paralysis') or name=='hold-grant' or self.early_core or self.rescue or self.forecast or self.head
        self.form='front-30' if self.coupled or self.after_paralysis else name
        self.guard=name in ('coupled-strict','hold-grant') or self.early_core
        self.exit_bait=False
        self.overlap=False
        self.owns_late_logos=False
        self.logo_used=False
        self.release=None
        self.key_used=False
        self.wait_max=name=='after-paralysis-wait'
        self.core_placed=False
        self.core_handled=False
        self.pull_core=name=='preheat-core-refund'
        self.book_held=False
        self.book_pressed=False
        self.book_retired=False
        self.book_returned=False
        self.rescue_logo=False
        self.logo_reserve=False
        self.early_stop=135 if name=='rescue-book-short' else 198
        self.lookahead=46 if name=='forecast-46' else 37
        self.forecast_bought=False

    def prepare(self,q):
        self.alternate_opening=True
        if self.forecast:
            return plan(q,drop=[2899,2953])
        if self.head:
            q=plan(q,drop=[3152],add=[put(3152,W,'F11',self.head)])
        if self.form=='same-delay':
            return q
        enter={'front-30':2869,'front-18':2881,'actual-mark':2869,'front-48':2851}[self.form]
        self.bait_frame=enter
        stay=[] if self.form=='actual-mark' else [out(2953,E)]
        return plan(q,drop=[2899,2953],add=[put(enter,E,'D9','l'),*stay])

    def __call__(self,b,act,stats):
        f=b.frame
        if self.forecast and 2710<=f<=3120:
            gun=b.live.get(FIRST_GUN)
            logo=b.operators.get(L)
            eye=b.operators.get(E)
            if gun and logo and not self.forecast_bought and f<=2940 and b.dp>=price(b,E):
                phase=b.hostile.states.get(FIRST_GUN)
                profile=b.hostile.profiles.get(gun.key)
                if profile and not (phase and phase.release):
                    r0,c0=gun.previous
                    dr,dc=gun.row-r0,gun.col-c0
                    reach=profile['radius']+b.hostile._body(logo)[0]
                    near=hypot(gun.row-logo.row,gun.col-logo.col)
                    soon=hypot(gun.row+dr*self.lookahead-logo.row,gun.col+dc*self.lookahead-logo.col)
                    if near>=reach and (soon<=reach+.1 or f>=2900 and near<=reach+.29):
                        if hypot(gun.row+30*dr-3,gun.col+30*dc-8)<=2.27 and (3,8) not in b.occupied():
                            act(put(f,E,'D9','l'))
                            self.forecast_bought=True
            if eye and self.forecast_bought and not self.exit_bait:
                if any(gun.id==FIRST_GUN and center==(eye.row,eye.col) for _,gun,center,_,_ in b.hostile.shells) or not gun or f==3120:
                    act(out(f,E))
                    self.exit_bait=True
        if self.rescue and 2970<=f<=4500:
            logo=b.operators.get(L)
            book=b.operators.get(B)
            threat=next((end for end,gun,center,_,_ in b.hostile.shells if gun.id==FIRST_GUN and center==(5,6)),None)
            if f==3054 and book and (threat or not logo):
                self.book_held=True
                b.actions[f]=[a for a in b.actions[f] if a['operator_id']!=B]
                b.actions[6426]=[a for a in b.actions[6426] if a['operator_id']!=B]
            if logo and threat and 1<=threat-f<=5 and not self.rescue_logo:
                act(out(f,L))
                self.rescue_logo=True
            if book and self.book_held and not self.book_pressed and f>=3380 and ready(book,f):
                if SPONSOR in {e.id for e in b.targets(book,book.handler.blast)}:
                    act(cast(f,B))
                    act(cast(f+self.early_stop,B))
                    self.book_pressed=True
            if book and self.book_held and not self.book_retired and f>=4150:
                finished=SPONSOR in b.enemies and SPONSOR not in b.live
                if finished or f>=4490:
                    act(out(f,B))
                    self.book_retired=True
        if self.rescue and 6560<=f<=8020:
            if self.rescue_logo and not self.logo_reserve and L not in b.operators and b.ready_at[L]<=f and b.dp>=price(b,L) and (2,5) not in b.occupied():
                act(put(f,L,'C6','r'))
                self.logo_reserve=True
            if self.book_retired and not self.book_returned and B not in b.operators and b.ready_at[B]<=f and b.dp>=price(b,B) and (5,6) not in b.occupied():
                act(put(f,B,'F7','l'))
                self.book_returned=True
        if self.early_core and 7645<=f<=8260:
            middle=b.live.get(MID)
            eye=b.operators.get(E)
            if not eye and not self.core_placed and middle and not middle.cursor.hidden and middle.hp>=43000*2**32:
                if T in b.operators and M in b.operators and hypot(middle.row-2,middle.col-5)<=2.48 and (2,5) not in b.occupied():
                    if b.ready_at[E]<=f and b.dp>=price(b,E):
                        self.core_placed=True
                        b.actions[8473]=[a for a in b.actions[8473] if a['operator_id']!=E]
                        act(put(f,E,'C6','l'))
            elif self.core_placed and eye and not self.eye_early and ready(eye,f):
                targets={e.id for e in b.targets(eye,eye.handler.skill_cells)}
                if MID in targets and T in b.operators and b.operators[T].handler.active:
                    act(cast(f,E))
                    self.eye_early=True
        if self.pull_core and self.core_placed and not self.core_handled and 7660<=f<=8570 and E in b.operators:
            eye=b.operators[E]
            if any(1<=when-f<=6 and origin==(eye.row,eye.col) and gun.id==MID for when,gun,origin,_,_ in b.hostile.shells):
                act(out(f,E))
                self.core_handled=True
        if self.form=='actual-mark' and 2899<=f<=3145 and not self.exit_bait and E in b.operators:
            eye=b.operators[E]
            at=(eye.row,eye.col)
            locked=any(source.id==FIRST_GUN and center==at for _,source,center,_,_ in b.hostile.shells)
            if locked or f>=3139 or FIRST_GUN in b.enemies and FIRST_GUN not in b.live:
                act(out(f,E))
                self.exit_bait=True
        if self.coupled and f==8641:
            sp,hp,wing,central=stats.get('volcano-8640',[None,0,[],False])
            truth=b.operators.get(T)
            self.overlap=bool(sp and 76<=sp<=77 and 9500<=hp<=13000 and wing and central and truth and truth.handler.active)
            if self.guard:
                self.overlap=self.overlap and hp>=11000
            if self.overlap:
                self.owns_late_logos=True
                self.screen=True
                b.actions[8656]=[a for a in b.actions[8656] if a['operator_id']!=M]
        if self.overlap and 8642<=f<=8707 and not self.eye_early:
            eye=b.operators.get(E)
            truth=b.operators.get(T)
            if eye and truth and truth.handler.active and ready(eye,f) and (eye.row,eye.col) in truth.handler.wide:
                targets={e.id for e in b.targets(eye,eye.handler.skill_cells)}
                if MID in targets and targets & WING:
                    act(cast(f,E))
                    self.eye_early=True
        if self.overlap and f==8750:
            key=b.operators.get(M)
            if key and ready(key,f):
                act(cast(f,M))
        if self.after_paralysis and not self.overlap and f==8656:
            cannon=b.live.get(SOUTH)
            phase=b.hostile.states.get(SOUTH)
            truth=b.operators.get(T)
            key=b.operators.get(M)
            if (cannon and phase and phase.release and 1<=phase.release-f<=16 and truth and truth.handler.active
                    and key and (key.row,key.col) in truth.handler.wide and SOUTH in truth.handler.members and b.mantra.can_interrupt(cannon)):
                self.release=phase.release
                self.screen=True
                b.actions[8656]=[a for a in b.actions[8656] if a['operator_id']!=M]
        if self.release and self.release<=f<=8696 and not self.key_used:
            key=b.operators.get(M)
            state=b.logos.receivers.get(SOUTH) if b.logos else None
            consumed=not state or not state.palsy_stacks
            if key and ready(key,f) and (not self.wait_max or consumed or f>=8684):
                act(cast(f,M))
                self.key_used=True
        if self.overlap and f>=9800 and not self.logo_used:
            logo=b.operators.get(L)
            if logo and ready(logo,f) and b.targets(logo,logo.handler.skill_cells):
                act(cast(f,L))
                self.logo_used=True
        return super().__call__(b,act,stats)


def play(job):
    return evaluate(job,ProtectedBait(job[0]))


if __name__=='__main__':
    mode,tag,n=sys.argv[1:4]
    seeds=PROBE if mode=='probe' else CORE_PROBE if mode=='core' else RESCUE_PROBE if mode=='rescue' else list(trial_seeds(int(n),int(tag)))
    run_rows(here/f'protected-bait-{tag}-{mode}.jsonl',play,[(name,mode,s,t) for s,t in seeds for name in sys.argv[4:]],4)
