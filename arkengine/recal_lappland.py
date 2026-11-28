"""Lappland the Decadenza S1. Run existing ordinary funnels and remote units on the shared board."""
from .caster_cast import run_committed_cast
from .caster_drone_chain_mystic import LAPPLAND, _drone, _lappland_weighted_target
from .caster_model import _assets
from .caster_special_start import resolve_special_start
from .fixed import ONE, mul
from .lappland_stage import LapplandAttacks
from .skill_recovery import deploy_skill
from .volley_damage import _raw_f32


class LapplandCombat(LapplandAttacks):
    def __init__(self,battle,actor):
        self.battle,self.actor=battle,actor
        self.present=True
        self.active=False
        self.stats_by_enemy={}
        self.notes=set()
        view="Back" if actor.direction=="UP" else "Front"
        scene=dict(placement=actor.place,view=view,rows=battle.rows,cols=battle.cols)
        # LapplandAttacks initializes per-enemy maps for single-stage playback.
        # On the live board, receivers are looked up at the time of each impact.
        class World:
            stats={}
        super().__init__(battle.tables,actor.card,_assets(battle.tables,actor.card),scene,World(),battle.events,self)
        self.world=self
        self.gauge,_=deploy_skill(battle.tables,actor.card,battle.frame)
        state=self.gauge.state
        state.points=min(state.maximum,state.points+int(self.talents[1]["sp"]))
        self.native=run_committed_cast(battle.tables,self.ev,horizon=0,facing=view,direction=actor.direction)["toggle_cast"]
        self.remote_keys=[self.parts[r["m_PathID"]]["fields"]["_projectileKey"] for r in self.remote["_funnelActions"]]
        self.units=[]
        self.unblock=actor.deployed_frame+1

    def enemy_row(self,e):
        def note(rule):
            if (e.id,rule) not in self.notes:
                self.battle.log("rule_applied",target=e.id,rule=rule)
                self.notes.add((e.id,rule))
        return dict(id=e.id,prefab=e.prefab,row=e.row,col=e.col,tile_z=0,airborne=e.airborne,
                    res=e.res,hp=e.hp/ONE,character_type=False,receiver_mount_rule=note)

    def pool(self,frame,cells):
        return [self.enemy_row(e) for e in self.battle.targets(self.actor,cells)]

    def select(self,rows,frame):
        return rows[0] if rows else None

    def by_id(self,key,frame,**kw):
        e=self.battle.live.get(key)
        return self.enemy_row(e) if e and not e.cursor.hidden else None

    receiver_at=by_id

    def alive(self,k):
        return k in self.battle.live

    def interval(self):
        base=self.stats["attackSpeed"]
        speed=base+self.talents[1].get("attack_speed",0)+(self.module_speed if self.module_started else 0)
        return _raw_f32(self.stats["baseAttackTime"]*100/max(10,speed*self.battle.attack_speed(self.actor)/base))

    def damage(self,frame,key,scale,kind,active):
        target=self.battle.live.get(key)
        if target:
            self.battle.arts(self.actor,target,self.battle.attack(self.actor,self.board["atk"] if active else 0),
                             scale_raw=scale,kind=kind)
            if self.stage(frame)>=2 and target.alive and not target.attr("silenceImmune"):
                target.silence.attach(self.tables,frame,self.talents[0]["attack@silence_duration"])

    def objects(self):
        frame=self.battle.frame
        if self.present and frame>=self.unblock:
            self.gauge.state.advance(mul(self.step,_raw_f32(self.battle.sp_rate(self.actor))))
        self.tick(frame,self.active)

    def coroutines(self):
        if not self.active or not self.present:
            return
        f=self.battle.frame
        count=2+int(self.board["attack@cnt"])+int(self.stage(f)==3)
        while len(self.units)<count:
            self.units.append(dict(projectile=self.remote_keys[len(self.units)],target=None))
        for drone in self.units:
            key=drone["target"]
            if key is None:
                targets=sorted(self.battle.targets(self.actor,self.drone_cells),key=lambda e:e.order)
                draw=_lappland_weighted_target([dict(id=e.id,moving=e.previous!=e.cursor.position) for e in targets],1,self.battle.rng)
                key=draw.get("target") if "target" in draw else draw["candidates"][0]
                if key:
                    drone.update(target=key,remaining=0,prior_same_target_hits=0,previous_target=None)
                    self.battle.log("remote_funnel_release",operator_id=self.actor.uid,target=key,unit=drone["projectile"])
                continue
            target=self.battle.live.get(key)
            if not target or target.cursor.hidden or target.previous!=target.cursor.position:
                drone["target"]=None
                continue
            drone["remaining"]-=self.step
            if drone["remaining"]>0:
                continue
            drone["remaining"]=self.interval()-self.step
            packet=_drone(LAPPLAND,1,"drone_hit",dict(drone,talent_stage=self.stage(f),target=self.enemy_row(target)),
                          self.board,self.stats,self.trait,self.talents,stream=self.battle.rng)
            drone.update(previous_target=key,prior_same_target_hits=packet["prior_same_target_hits"])
            self.damage(f,key,_raw_f32(packet["damage_scale"]),"remote_funnel_hit",True)

    def press(self):
        f=self.battle.frame
        state=self.gauge.state
        if not self.present or f<self.unblock or state.points<state.cost:
            self.battle.log("skill_rejected",operator_id=self.actor.uid,sp=state.points)
            return
        cast=resolve_special_start(self.tables,self.ev,frame=f,phase="after_tick",facing=self.view,
            direction=self.actor.direction,sp=state.points,toggled=self.active,source="manual")
        if cast["cast_frame"] is None:
            self.battle.log("skill_rejected",operator_id=self.actor.uid,reason=cast["status"])
            return
        state.points=cast["sp_after"]
        self.module_started=True
        self.active=cast["toggled_after"]
        self.battle.skill_started(self.actor,cast["cast_frame"])
        self.unblock=cast["cast_frame"]+self.native["first_recovery_tick"]
        self.reset_attack(f)
        self.units.clear()
        self.battle.log("skill_pressed",operator_id=self.actor.uid,skill=1,remote=self.active)

    def close(self):
        self.active=self.present=False
        self.units.clear()
