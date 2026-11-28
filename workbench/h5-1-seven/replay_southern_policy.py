"""Combine a south-facing damage handoff with the confirmed north-shell escape."""
import sys
from math import hypot

from experiment_rows import run_rows
from last_shell import PAIRS
from logos_damage_response import evaluate
from observed_timeline import E, J, L, M, T, W, NORTH_GUN, WING, cast, here, out, price, put, ready
from study import plan
from tools.run_caster_seeds import trial_seeds


MID = "w0-f9-a1-n0"
SOUTH = "w0-f11-a6-n0"
DEVICE_EARLY = {"early-device", "device-on-threat", "device-lock-south"}


class Tail:
    def __init__(self,name):
        self.name = name
        self.fire = False
        self.mostima = False
        self.screen = False
        self.withdraw = False
        self.extra_lock = False
        self.jam = False
        self.lock_left = False
        self.south_logos = False
        self.south_ready = False

    def prepare(self,q):
        if self.name in DEVICE_EARLY:
            if self.name=="device-lock-south":
                return plan(q,drop=[5415,5754,8473,9576,9876])
            return plan(q,drop=[5415])
        if self.name in {"low-mostima", "low-mostima-screen"}:
            face="l" if self.name=="low-mostima" else "d"
            return plan(q,drop=[3296,7020,9576],add=[put(3296,M,"D9","l"),put(7020,T,"E9",face),out(9576,M),put(9576,L,"D9","u")])
        if self.name in {"logos-y","logos-y-cover"}:
            return plan(q,squad={L:{"operator_id":L,"skill":2,"module":"uniequip_003_logos","module_level":3}})
        return q

    def __call__(self,b,act,stats):
        f=b.frame
        if self.name in DEVICE_EARLY:
            self.early_device(b,act)
            if self.name=="device-lock-south":
                self.south_lock(b,act)
        if self.name in {"grant-first","grant-balance"} and 8600<=f<8708 and not self.fire:
            eye=b.operators.get(E)
            mantra=b.operators.get(T)
            if eye and mantra and mantra.handler.active and ready(eye,f) and (eye.row,eye.col) in mantra.handler.wide:
                m=b.live.get(MID)
                targets=b.targets(eye,eye.handler.skill_cells)
                coverage={e.id for e in targets}&{MID,SOUTH,*WING}
                useful=coverage.intersection(mantra.handler.members)
                if MID in useful and len(useful)>=2:
                    act(cast(f,E))
                    self.fire=True
                    if self.name=="grant-balance" and f<=8656 and m and 16000*2**32<m.hp<=21500*2**32:
                        b.actions[8656]=[a for a in b.actions[8656] if a["operator_id"]!=M]
                    else:
                        self.mostima=True
        if self.name in {"logos-extra","logos-y-cover"} and 4420<=f<=5700 and not self.extra_lock:
            logos=b.operators.get(L)
            if logos and ready(logos,f):
                targets=b.targets(logos,logos.handler.skill_cells)
                if any(u.id.startswith(("w0-f3-","w0-f5-","w0-f6-","w0-f8-")) for u in targets):
                    act(cast(f,L))
                    self.extra_lock=True
        if f==8640:
            eye=b.operators.get(E)
            mid=b.live.get(MID)
            targets=b.targets(eye,eye.handler.skill_cells) if eye else []
            wing={u.id for u in targets}&WING
            stats["volcano-8640"]=[round(eye.handler.gauge.state.points,2) if eye else None,
                round(mid.hp/2**32) if mid else 0, sorted(wing), MID in {u.id for u in targets}]
            broad = self.name in {"volcano-split","volcano-screen"}
            selective = self.name in {"volcano-budget","budget-screen"} and mid and 16000*2**32<mid.hp<=22000*2**32
            if eye and ready(eye,f) and mid in targets and wing and (broad or selective):
                act(cast(f,E))
                self.fire=True
                b.actions[8656]=[a for a in b.actions[8656] if a["operator_id"]!=M]
        if self.fire and not self.mostima and 8780<=f<=8950:
            m=b.operators.get(M)
            if m and ready(m,f):
                fish = {u.id for u in b.targets(m,m.handler.ranges[True])}
                if fish.intersection({MID,SOUTH}|WING) or f==8950:
                    act(cast(f,M))
                    self.mostima=True
        if self.name in {"tail-screen","volcano-screen","budget-screen","grant-first","grant-balance"} and 9300<=f<9576 and not self.screen:
            self.south(b,act)
        if 10200<=f<=10940 and not self.withdraw:
            self.north(b,act)
        return self.fire

    def early_device(self,b,act):
        f=b.frame
        if self.jam or not 4190<=f<=5415 or not b.inventory[J] or b.dp<40 or (4,4) in b.occupied():
            return
        threats=[e for e in b.live.values() if not e.cursor.hidden and e.id.startswith(("w0-f5-","w0-f6-"))]
        if self.name=="early-device" or f==5415 or any( abs(e.row-4)+abs(e.col-4)<=3.45 for e in threats):
            act(put(f,J,"E5","l"))
            self.jam=True

    def south_lock(self,b,act):
        f=b.frame
        logos=b.operators.get(L)
        if self.jam and logos and 5100<=f<=5230 and not self.lock_left:
            if f>=5200 or b.operators.get(M) and b.operators[M].handler.active:
                act(out(f,L))
                self.lock_left=True
        if 8650<=f<=9200 and not self.south_logos and not logos and b.ready_at[L]<=f and b.dp>=price(b,L) and (2,5) not in b.occupied():
            cannon=b.live.get(MID)
            close= cannon and hypot(cannon.row-2,cannon.col-5)<=2.35
            if not close:
                act(put(f,L,"C6","r"))
                self.south_logos=True
        if self.south_logos and logos and 8700<=f<=10100 and not self.south_ready and ready(logos,f):
            t=b.targets(logos,logos.handler.skill_cells)
            if t and t[0].id in {MID,SOUTH,*WING}:
                act(cast(f,L))
                self.south_ready=True

    def south(self,b,act):
        f=b.frame
        if L in b.operators or b.ready_at[L]>f or b.dp<price(b,L) or not {T,M}<=b.operators.keys():
            return
        for key in (MID,SOUTH):
            gun=b.live.get(key)
            clock=b.hostile.states.get(key)
            if not gun or not clock or not clock.release or not 1<=clock.release-f<=2:
                continue
            if b.mantra and b.mantra.can_interrupt(gun) or b.disarmed(gun):
                continue
            if not any(x.uid in (M,T) for x in clock.targets):
                continue
            if (2,5) in b.occupied() or hypot(gun.row-2,gun.col-5)>2.18:
                continue
            for frame in tuple(b.actions):
                if frame>=f:
                    b.actions[frame]=[a for a in b.actions[frame] if a["operator_id"]!=L and
                        not (a["operator_id"]==M and a.get("kind")=="withdraw")]
            act(put(f,L,"C6","r"))
            self.screen=True
            break

    def north(self,b,act):
        f=b.frame
        gun=b.live.get(NORTH_GUN)
        clock=b.hostile.states.get(NORTH_GUN)
        if not gun or not clock or not clock.release or not 1<=clock.release-f<=5 or b.disarmed(gun):
            return
        if b.mantra and b.mantra.can_interrupt(gun):
            return
        if not clock.targets or clock.targets[0].uid!=L or not {W,T,L}<=b.operators.keys():
            return
        if any(hypot(gun.row-a.row,gun.col-a.col)<=2.3 for a in b.operators.values() if a.uid!=L):
            return
        logos,mantra=b.operators[L],b.operators[T]
        if hypot(logos.row-mantra.row,logos.col-mantra.col)>1.35:
            return
        for frame in tuple(b.actions):
            if frame>=f:
                b.actions[frame]=[a for a in b.actions[frame] if a["operator_id"]!=L]
        act(out(f,L))
        self.withdraw=True


def play(job):
    return evaluate(job,None if job[0]=="safehandoff" else Tail(job[0]))


if __name__=="__main__":
    mode,tag,n=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
    pairs=[*PAIRS,(1257960646,842931806),(1106834848,1419395519),(1727549616,1664913877),(589476853,1527080212),
           (296761114,1452984730),(61129363,1190947731),(551317787,2086239378),(1402784213,999166733)]
    if mode=="sample":
        pairs=list(trial_seeds(n,tag))
    run_rows(here/f"volcano-split-{tag}-{mode}.jsonl",play,
             [(name,mode,s,t) for s,t in pairs for name in sys.argv[4:]],4)
