"""Move Snow's wide hit forward when the first late A2 is about to enter its lane."""
import sys
from math import hypot

from experiment_rows import run_rows
from late_intercept import Intercept
from logos_damage_response import evaluate
from observed_timeline import B,E,FIRST_GUN,L,M,S,T,W,cast,here,price,put,ready
from replay_southern_policy import MID,WING,Tail
from tools.run_caster_seeds import trial_seeds
from wolf_down_policy import HEAD,SPOTS,circle_in_range,range_cells


A2='w0-f12-a0-n0'
PROBES=((1557471014,612883695),(953944070,1426910160),(628155088,196929602),(1727549616,1664913877),
        (299196511,1672095373),(499045565,1816409424),(589476853,1527080212),(296761114,1452984730),
        (551317787,2086239378),(61129363,1190947731),(605846201,1997970174),(1257960646,842931806))


class SnowInbound(Intercept):
    def __init__(self,name):
        super().__init__('intercept-late')
        self.name_mode=name
        self.snow=False
        self.use_late=False
        self.last_wolves=False
        self.small_mid=False
        self.eye_early=False
        self.finish_tried=set()
        self.finish_cast=set()
        self.finish_ranges={}

    def finish_one(self,b,act):
        f=b.frame
        if f<11200 or len(b.live)!=1 or f<max(b.spawns):
            return
        enemy=next(iter(b.live.values()))
        if enemy.cursor.hidden or enemy.hp>11000*2**32:
            return
        for uid in self.finish_tried.intersection(b.operators):
            actor=b.operators[uid]
            if uid in self.finish_cast or not ready(actor,f):
                continue
            cells=getattr(actor.handler,'skill_cells',getattr(actor.handler,'skill',None))
            if isinstance(cells,(list,tuple)) and circle_in_range(enemy.row,enemy.col,enemy.radius,cells,box_query=True):
                act(cast(f,uid))
                self.finish_cast.add(uid)
        if self.finish_tried.intersection(b.operators):
            return
        profile=b.hostile.profiles.get(enemy.key)
        danger=profile['radius']+.4 if profile else .5
        taken=b.occupied()
        for uid in (E,B,L,S,M,T,W):
            if uid in self.finish_tried or uid in b.operators or b.ready_at[uid]>f or b.dp<price(b,uid):
                continue
            for tile,loc in SPOTS:
                r,c=loc['row'],loc['col']
                if (r,c) in taken or hypot(enemy.row-r,enemy.col-c)<=danger:
                    continue
                for heading,direction in HEAD.items():
                    key=uid,tile,heading
                    if key not in self.finish_ranges:
                        self.finish_ranges[key]=range_cells(b.cards[uid]['base_range'],{**loc,'direction':direction},b.rows,b.cols)
                    if circle_in_range(enemy.row,enemy.col,enemy.radius,self.finish_ranges[key],box_query=True):
                        act(put(f,uid,tile,heading))
                        self.finish_tried.add(uid)
                        return

    def prepare(self,q):
        if self.name_mode.startswith(('normal-','select-')):
            self.alternate_opening=False
            return q
        return super().prepare(q)

    def __call__(self,b,act,stats):
        if self.name_mode.startswith('select-') and b.frame==2701:
            gun=b.live.get(FIRST_GUN)
            self.use_late=bool(gun and gun.hp<=53500*2**32)
            if self.use_late:
                for f in (2899,2953):
                    b.actions[f]=[a for a in b.actions[f] if a['operator_id']!=E]
        fixed=self.name_mode.startswith(('normal-','select-')) and not self.use_late
        before=Tail.__call__ if fixed else Intercept.__call__
        special=self.name_mode.endswith(('-covered','-reserve'))
        if self.name_mode.endswith('-reserve'):
            self.finish_one(b,act)
        if self.name_mode.endswith(('-broad','-split','-covered','-reserve')) and b.frame>=11250 and not self.last_wolves:
            wolf=b.operators.get(W)
            if wolf and ready(wolf,b.frame) and any(not e.cursor.hidden for e in b.live.values()):
                act(cast(b.frame,W))
                self.last_wolves=True
        if 8622<=b.frame<=8634 and not self.snow:
            enemy=b.live.get(A2)
            mid=b.live.get(MID)
            actor=b.operators.get(S)
            waiting=E in b.operators and actor and ready(actor,b.frame)
            timely=enemy and not enemy.cursor.hidden and 1.55<=enemy.row<=1.62 and 3.65<=enemy.col<=4.3
            mid_gap=mid is None or mid.hp <=15500*2**32
            if self.name_mode.endswith('-narrow'):
                mid_gap=mid and 13000*2**32<=mid.hp<=15500*2**32
                waiting=waiting and b.operators[E].handler.gauge.state.points>=72
            elif self.name_mode.endswith('-seven'):
                timely=timely and b.frame>=8630
                mid_gap=mid and 9000*2**32<=mid.hp<=12000*2**32
                waiting=waiting and b.operators[E].handler.gauge.state.points>=74
            elif self.name_mode.endswith(('-broad','-split','-covered','-reserve')):
                timely=timely and b.frame>=8630
                mid_gap=mid and 9000*2**32<=mid.hp<=16000*2**32
                if self.name_mode.endswith(('-split','-covered','-reserve')):
                    mid_gap=mid_gap and not (12000*2**32<mid.hp<13500*2**32)
                waiting=waiting and b.operators[E].handler.gauge.state.points>=68
            if timely and waiting and mid_gap:
                b.actions[8637]=[a for a in b.actions[8637] if a['operator_id']!=S]
                act(cast(b.frame,S))
                self.snow=True
                self.small_mid=mid is not None and mid.hp<=12000*2**32
        if special and b.frame==9030 and self.small_mid:
            wolf=b.operators.get(W)
            if wolf and ready(wolf,b.frame):
                b.actions[9031]=[a for a in b.actions[9031] if a['operator_id']!=W]
                act(cast(b.frame,W))
        if special and 8641<=b.frame<8708 and not self.eye_early and not self.fire:
            eye=b.operators.get(E)
            truth=b.operators.get(T)
            gun=b.live.get(MID)
            old_hp=stats.get('volcano-8640',[None,0])[1]
            load=old_hp if self.name_mode.endswith('-reserve') else (gun.hp/2**32 if gun else 0)
            if eye and truth and truth.handler.active and (eye.row,eye.col) in truth.handler.wide and ready(eye,b.frame) and gun and 22000<load<=24000:
                targets={e.id for e in b.targets(eye,eye.handler.skill_cells)}
                if MID in targets and targets & WING:
                    act(cast(b.frame,E))
                    self.eye_early=True
        return before(self,b,act,stats) or self.eye_early


def play(job):
    label=job[0]
    if label=='budget-screen':
        return evaluate(job,Tail(label))
    if label=='intercept-late':
        return evaluate(job,Intercept(label))
    return evaluate(job,SnowInbound(label))


if __name__=='__main__':
    mode,tag,total=sys.argv[1:4]
    pairs=(*PROBES,(625787789,453227734),(917981655,1482109289),(1407735752,1982040983),(1351463237,1922164518),
           (1906049810,20327990)) if mode=='probe' else (PROBES[:4]+PROBES[-2:] if mode=='focus' else list(trial_seeds(int(total),int(tag))))
    run_rows(here/f'snow-inbound-{tag}-{mode}.jsonl',play,[(m,mode,s,t) for s,t in pairs for m in sys.argv[4:]],4)
