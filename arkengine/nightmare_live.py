"""Nightmare's two modes share the stage HP receivers and the enemy movement odometer."""
from .attack_range import circle_in_range
from .baked_mount import single
from .bombardier_attack import _operator_hit_offset
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_first_spell import attack_key_clock
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON
from .elemental_casters import _board
from .five_star_skills import nightmare_airborne, nightmare_heal_targets, nightmare_heal_impact
from .fixed import ONE, mul
from .nightmare_route import _map_distance, rupture_packet
from .ranged_operator_battle import RangedOperatorBattle
from .skill_recovery import RecoveryTimer
from .trace_motion import paracurve_horizontal_step
from .volley_damage import _raw_f32


class NightmareBattle(RangedOperatorBattle):
    uid="char_164_nightm"

    def __init__(self,tables,request,releases):
        super().__init__(tables,request,releases)
        self.talent=_board(self.card,0,"1")
        self.credit=int(_trait_values(self.card).get("sp",0))
        self.power=_raw_f32(self.panel["atk"])
        if self.slot==2:
            self.power=mul(self.power,ONE+_raw_f32(self.talent["atk"]))
        self.heals=[]
        self.marks={}
        self.distance={k:(0.,(b["route_start"]["row"],b["route_start"]["col"])) for k,b in self.world.births.items()}
        self.external_marks={}
        for hit in request.get("rupture_applications",()):
            self.external_marks.setdefault(hit["frame"],[]).append(hit)
        if self.slot==1:
            spell=next(c["fields"] for c in self.ev["character"]["components"] if c["script"]=="NightmSkill_1")
            self.projectile=spell["_secondaryOptions"]["projectileKey"]
            parts={c["script"]:c["fields"] for c in self.ev["projectiles"][self.projectile]["logic"]["components"]}
            self.heal_speed=parts["ParacurveMovement"]["_speed"]
            self.heal_life=self.ticks(parts["SimpleProjectile"]["_lifeTime"])
        self.action=None
        self.cast=None
        self.offsets={}

    def deploy(self,frame,place):
        super().deploy(frame,place)
        self.action=self.cast=None
        self.unblock=self.restore=None
        self.period=single(self.panel["baseAttackTime"]*100/self.panel["attackSpeed"])
        self.key,_=attack_key_clock(self.tables,self.ev,self.view,"Attack",self.period)
        self.clock=run_committed_cast(self.tables,self.ev,horizon=0,facing=self.view,direction=place["direction"])["natural_end"]

    def clear_owner(self):
        super().clear_owner()
        self.action=self.cast=None

    def receive(self,frame,source,loss,damage_type):
        if self.slot==1 and damage_type in ("PHYSICAL","MAGICAL") and self.rng.dice(self.talent["prob"]):
            self.log("dodge",frame,source=source,damage_type=damage_type,prevented_q32=loss)
            return 0
        super().receive(frame,source,loss,damage_type)
        return loss

    def trigger(self,frame,targets,phase):
        auto=self.slot==2
        self.cast=resolve_cast_start(self.tables,self.ev,frame=frame,facing=self.view,direction=self.placement["direction"],
            phase=phase,trigger="AUTO" if auto else "MANUAL",sp=self.sp.points,owner_state="attack" if self.action else "idle")
        self.cast["targets"]=targets
        self.sp.points=self.cast["sp_after"]
        self.action=None
        self.restore=(frame+self.clock["end"] if auto else self.cast["spell_frame"]+self.clock["end"]-self.clock["spell_frame"])
        self.unblock=self.restore+self.clock["first_recovery_tick"]-self.clock["end"]
        self.events.extend(self.cast["events"])

    def button(self,frame,a):
        if self.slot==1 and self.cast is None and self.sp.points>=self.sp.cost:
            self.trigger(frame,[],a.get("phase","after_tick"))
        else:
            self.log("skill_rejected",frame,sp=self.sp.points)

    def movement(self,key,frame):
        ratio=self.marks.get(key,{}).get("speed",0)
        return False, bool(ratio),ratio

    def apply_mark(self,key,frame,source,value,duration,interval,speed):
        if key in self.marks:
            self.marks[key]["remaining"]=_raw_f32(duration)
            self.log("rupture_refresh",frame,target=key,source=self.marks[key]["source"],damage_per_tile=self.marks[key]["value"])
        else:
            self.marks[key]=dict(source=source,value=value,speed=speed,remaining=_raw_f32(duration),
                                 last=self.distance[key][0],pulse=RecoveryTimer(_raw_f32(interval)))
            if speed:
                self.control(key,frame,speed)
            self.log("rupture_apply",frame,target=key,source=source,damage_per_tile=value,total_move_distance=self.distance[key][0])

    def tick_distance(self,frame):
        for key in self.world.active[frame]:
            if not self.alive(key):
                continue
            total,last=self.distance[key]
            mark=self.marks.get(key)
            if mark:
                mark["remaining"]-=self.step
                finished=mark["remaining"]<=0
                if mark["pulse"].advance(self.step) or finished:
                    delta,damage=rupture_packet(mark["last"],total,mark["value"])
                    mark["last"]=total
                    if damage and not self.invincible(key,frame):
                        state=self.states[key]
                        if key in self.receiver_buffs:
                            damage=self.receiver_buffs[key].damage(frame,mark["source"],"true",damage)
                        state.hp=max(0,state.hp-damage)
                        if state.hp<=HP_EPSILON:
                            state.hp=0
                        self.log("rupture_damage",frame,target=key,source=mark["source"],damage_q32=damage,
                                 hp_after_q32=state.hp,distance_delta_q32=delta,position=self.world.positions[frame][key])
                if finished:
                    del self.marks[key]
                    self.log("rupture_finish",frame,target=key)
            if (frame-self.world.births[key]["frame"])%5==0:
                current=self.world.positions[frame][key]
                self.distance[key]=single(total+_map_distance(current,last)),current
        for e in self.external_marks.get(frame,()):
            key=e["target"]
            if self.alive(key) and key in self.world.active[frame]:
                self.apply_mark(key,frame,e["source"],e["damage_per_tile"],e["duration"],e.get("interval",.066),e.get("move_speed",0))

    def tick(self,frame):
        if self.slot==2 or self.external_marks:
            self.tick_distance(frame)
        if not self.world.present or frame==self.generation:
            return
        if frame==self.restore:
            self.cast=None
            if self.slot==2:
                self.next_attack=frame+1
            self.log("skill_end",frame)
        if self.cast is None and (self.unblock is None or frame>=self.unblock):
            self.sp.advance(self.step)
        if self.slot==2 and self.cast is None and self.sp.points>=self.sp.cost and frame>=self.next_attack:
            pool=self.pool(frame,self.skill_cells)
            targets=[]
            while pool and len(targets)<int(self.bb["max_target"]):
                air=[r for r in pool if nightmare_airborne(dict(r,airborne=r.get("airborne",False)))]
                row=self.world.select(air or pool,frame)
                targets.append(row["id"])
                pool.remove(row)
            if targets:
                self.trigger(frame,targets,"before_coroutines")
        if self.cast and frame==self.cast["spell_frame"]:
            if self.slot==1:
                self.next_attack=frame+1
                self.log("healing_mode",frame)
            else:
                for key in self.cast["targets"]:
                    if self.alive(key) and self.world.receiver_at(key,frame):
                        self.apply_mark(key,frame,self.uid,self.bb["value"],self.bb["duration"],self.bb["interval"],self.bb["move_speed"])
        active=self.slot==1 and self.cast and frame>=self.cast["spell_frame"]
        if frame>=self.next_attack and (self.cast is None or active):
            rows=self.pool(frame,self.cells)
            if rows:
                key=self.world.select(rows,frame)["id"]
                self.action=frame+self.key,key,bool(active)
                self.next_attack=frame+self.ticks(self.period)
                self.log("attack_start",frame,target=key,healing=bool(active),hit_frame=self.action[0])
            else:
                self.next_attack=frame+3
        if self.action and frame==self.action[0]:
            _,key,healing=self.action
            self.action=None
            if self.alive(key) and not self.invincible(key,frame) and self.world.receiver_at(key,frame):
                state=self.states[key]
                hit=state.receive_ordinary(frame,source=self.uid,damage_type="arts",attack_q32=self.power,
                    mitigation_q32=max(0,_raw_f32(self.world.stats[key]["res"])+state.res_add))
                previous=self.sp.points
                if self.cast is None and (self.unblock is None or frame>=self.unblock) and self.world.stats[key]["level"] in ("ELITE","BOSS"):
                    self.sp.points=min(self.sp.maximum,self.sp.points+self.credit)
                self.log("attack_hit",frame,target=key,damage_q32=hit["damage_q32"],hp_after_q32=state.hp,module_sp=self.sp.points-previous)
            if healing:
                self.release_heals(frame)

    def mount(self,uid):
        unit=self.units[uid]
        p=unit["placement"]
        if uid not in self.offsets:
            self.offsets[uid]=_operator_hit_offset(self.tables,uid,self.world.cards[uid].get("battle_skin","defaultskin"))
        tile=self.world.tiles[p["row"],p["col"]]
        offset=self.offsets[uid] or (0.,0.,0.)
        return tuple(v+d for v,d in zip((p["col"],p["row"],tile["position"]["z"]-tile["locate_height_offset"]),offset))

    def ally_row(self,uid):
        unit=self.units[uid]
        return dict(id=uid,hp_q32=unit["hp"],max_hp_q32=unit["maximum"],healable=unit.get("healable",True))

    def release_heals(self,frame):
        candidates=[self.ally_row(uid) for uid,u in self.units.items() if u["hp"]>HP_EPSILON and
                    circle_in_range(u["placement"]["row"],u["placement"]["col"],u["radius"],
                                    self.cells,box_query=True)]
        for ally in nightmare_heal_targets(candidates,int(self.bb["attack@max_target"])):
            uid=ally["id"]
            self.heals.append(dict(uid=uid,instance=self.units[uid],position=self.mount(self.uid),born=frame))
            self.log("heal_projectile_release",frame,target=uid,projectile=self.projectile)

    def after_frame(self,frame):
        for shot in self.heals[:]:
            if frame<=shot["born"]:
                continue
            uid=shot["uid"]
            if self.units.get(uid) is shot["instance"] and self.units[uid]["hp"]>HP_EPSILON:
                pos,hit=paracurve_horizontal_step(shot["position"],self.mount(uid),self.heal_speed,self.dt,True)
                shot["position"]=pos
                if not hit and frame<shot["born"]+self.heal_life:
                    continue
                amount=mul(self.power,_raw_f32(self.bb["attack@heal_scale"]))
                heal=nightmare_heal_impact(self.ally_row(uid),amount)
                self.units[uid]["hp"]=heal["hp_after_q32"]
                if uid==self.uid:
                    self.world.hp=heal["hp_after_q32"]
                if self.world.incoming:
                    self.world.incoming.units[uid]["hp"]=heal["hp_after_q32"]
                self.log("heal_projectile_hit",frame,target=uid,requested_q32=amount,displayed_heal_q32=amount,restored_q32=heal["restored_q32"],
                         source_hp_q32=self.world.hp,holder_hp_q32=heal["hp_after_q32"])
            self.heals.remove(shot)
