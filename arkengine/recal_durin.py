"""Durin's ordinary homing arts attack and hit-time magical evasion."""
from math import hypot

from .attack_range import range_cells
from .caster_model import _assets
from .caster_runtime import default_attack
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .trace_motion import ordinary_trace_step


class DurinCombat:
    def __init__(self,battle,actor):
        self.b,self.a=battle,actor
        self.ev=_assets(battle.tables,actor.card)
        self.normal=default_attack(battle.tables,actor.uid)["fields"]
        p=self.ev["projectiles"][self.normal["_projectileKey"]]
        self.speed,={c["fields"]["_speed"] for variant in p.values() for c in variant["components"] if c["script"]=="AdvancedMovement"}
        template=self.ev["templates"]["evade_magic"]["eventToActions"]["ON_TAKE_DAMAGE"][0]
        if template["_damageMask"]!="MAGICAL" or template["_applyWayFilter"]!="ALL":
            raise ValueError("杜林闪避的伤害类型变化")
        self.chance=next((entry["value"] for row in actor.card["base_talents"] for entry in row["blackboard"] if entry["key"]=="prob"),0)
        self.face="Back" if actor.direction=="UP" else "Front"
        self.sign=-1 if actor.direction=="LEFT" else 1
        self.poll=battle.frame+_birth(battle.tables,actor.card,self.ev,self.face)["born_exit_update"]+1
        self.cells=range_cells(actor.card["base_range"],actor.place,battle.rows,battle.cols)
        self.pending=None
        self.shots=[]
        self.notes=set()
        self.closed=False

    def evades(self,damage_type):
        return damage_type in (2,"MAGICAL","arts","magic") and self.b.rng.dice(self.chance)

    def objects(self):
        f=self.b.frame
        if not self.closed and f==self.poll:
            targets=self.b.targets(self.a,self.cells)
            if targets:
                e=targets[0]
                iv=self.a.card["panel_attributes"]["baseAttackTime"]*100/self.b.attack_speed(self.a)
                dx,dy=e.col-self.a.col,e.row-self.a.row
                if abs(dx)>.2:
                    self.sign=1 if dx>0 else -1
                if abs(dy)>abs(dx)+.25*hypot(dx,dy):
                    self.face="Back" if dy>0 else "Front"
                clock=ranged_shot_release(self.b.tables,self.ev,self.normal,attack_frame=f,attack_interval=iv,facing=self.face)
                self.pending=e,clock
                self.poll+=fixed_wait_frames(iv/self.b.dt)
            else:
                self.poll+=3
        remaining=[]
        for e,pos,offset in self.shots:
            if e.alive:
                step=ordinary_trace_step(pos,(e.col+offset[0],e.row+offset[1],offset[2]),effective_dt=self.b.dt,round_position=True,speed=self.speed)
                if step.reached:
                    self.b.arts(self.a,e,self.b.attack(self.a),kind="durin_hit")
                else:
                    remaining.append((e,step.position,offset))
        self.shots=remaining

    def coroutines(self):
        if self.pending and self.pending[1]["release_frame"]==self.b.frame:
            e,clock=self.pending
            self.pending=None
            if e.alive:
                def note(rule):
                    if (e.id,rule) not in self.notes:
                        self.notes.add((e.id,rule))
                        self.b.log("rule_applied",rule=rule,target=e.id)
                row=dict(prefab=e.prefab,row=e.row,col=e.col,tile_z=0,airborne=e.airborne,receiver_mount_rule=note)
                origin,mount=stationary_cast_mounts(self.b.tables,self.a.uid,clock["clock"],self.a.place,row,self.face,caster_face_sign=self.sign)
                self.shots.append((e,origin,(mount[0]-e.col,mount[1]-e.row,mount[2])))

    def press(self):
        self.b.log("skill_rejected",operator_id=self.a.uid,reason="no_skill")

    def close(self):
        self.closed=True
        self.pending=None
