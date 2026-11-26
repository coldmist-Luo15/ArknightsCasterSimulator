"""Ho'olheyak's independent directional tornadoes, aura receivers and levitation."""
from .attack_range import circle_in_range
from .baked_mount import single
from .caster_equipment_traits import _trait_values
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .core_caster_skills import _talent
from .damage import HP_EPSILON
from .fixed import ONE, mul
from .hoolheyak_curve import tornado_scale
from .hoolheyak_stage import _magic_aura, _mass_aura, _vulnerability
from .hoolheyak_tornado import _profile
from .levitate import ordinary_levitate, WalkingLevitate
from .ranged_operator_battle import RangedOperatorBattle
from .stage_channel import object_position_frame
from .volley_damage import _raw_f32


class HoolheyakBattle(RangedOperatorBattle):
    uid="char_4027_heyak"
    exact_contacts=True

    def __init__(self,tables,request,releases):
        super().__init__(tables,request,releases)
        self.tornadoes=[]
        self.emissions={}
        self.lifts={}
        self.volley=0
        self.talent=_talent(tables,self.card,0)
        self.trait=_trait_values(self.card)
        self.ordinary=default_attack(tables,self.uid)["fields"]
        self.power=_raw_f32(self.panel["atk"])
        self.mass=self.magic=None
        self.mode=self.expiry=None
        self.cleanses={}
        for event in request.get("silence_clears",()):
            self.cleanses.setdefault((event["frame"],event["phase"]),[]).append(event["target"])
        self.probes={}
        for event in request.get("silence_checks_after_objects",()):
            self.probes.setdefault(event["frame"],[]).append(event)

    def deploy(self,frame,place):
        super().deploy(frame,place)
        self.base_cells=self.cells
        self.mode=self.expiry=None
        self.heading=dict(UP=(1,0),DOWN=(-1,0),LEFT=(0,-1),RIGHT=(0,1))[place["direction"]]
        _,self.mounts,self.pre,self.interval,self.life,self.mover,self.curve=_profile(self.tables,self.card,self.ev,self.view)
        self.auras(frame,False)

    def auras(self,frame,active):
        self.cells=self.skill_cells if active else self.base_cells
        self.mass=_mass_aura(self.tables,self.card,self.ev,self,frame,mode="S3" if active else "Default")
        self.magic=_magic_aura(self.tables,self.ev,self,frame,self.trait)

    def by_id(self,key,frame,*,after_objects=False,projectile_born=None):
        row=self.world.receiver_at(key,frame) if after_objects else self.world.by_id(key,frame,projectile_born=projectile_born)
        if row and key in self.lifts:
            lift=self.lifts[key]
            row.update(height_offset=lift.height,airborne=row.get("airborne",False) or frame<lift.expires)
        return row

    def projectile_receiver(self,key,frame,birth):
        return self.by_id(key,frame,projectile_born=birth)

    def ally_magic(self,hit,key,attack,res):
        scale=_vulnerability(self.magic,key,self.trait)[0] if self.magic else ONE
        return attack,res,scale

    def unblockable(self,key,frame):
        return key in self.lifts and frame<self.lifts[key].expires

    def movement(self,key,frame):
        lift=self.lifts.get(key)
        return bool(lift and frame<=lift.expires+1),False,0

    def cleanse(self,frame,phase):
        for key in self.cleanses.get((frame,phase),()):
            self.states[key].silence.clear()
            self.log("silence_cleared",frame,target=key,phase=phase)

    def update_walks(self,frame):
        self.cleanse(frame,"before_objects")
        for key,lift in tuple(self.lifts.items()):
            lift.advance(frame)
            if frame==lift.expires:
                self.states[key].airborne=self.world.stats[key].get("airborne",False)
                self.log("landed",frame,target=key)
            if frame>lift.expires and not lift.height:
                del self.lifts[key]
        super().update_walks(frame)

    def clear_owner(self):
        super().clear_owner()
        self.mass=self.magic=None
        self.emissions.clear()
        self.mode=self.expiry=None

    def button(self,frame,a):
        if self.mode is not None or self.sp.points<self.sp.cost:
            self.log("skill_rejected",frame,sp=self.sp.points)
            return
        cast=resolve_cast_start(self.tables,self.ev,frame=frame,facing=self.view,direction=self.placement["direction"],
            owner_state="attack" if self.pending else "idle",phase=a.get("phase","after_tick"),trigger="MANUAL",sp=self.sp.points)
        self.sp.points=cast["sp_after"]
        self.mode=cast["spell_frame"]
        self.expiry=self.mode+self.ticks(self.card["skill_row"]["duration"])
        self.pending.clear()
        self.events.extend(cast["events"])

    def tick(self,frame):
        if not self.world.present or frame==self.generation:
            return
        if self.mass:
            self.mass.advance()
        if self.magic:
            self.magic.advance(save_before_update=True)
        if frame==self.mode:
            self.auras(frame,True)
            self.next_attack=frame+1
        if frame==self.expiry:
            self.auras(frame,False)
            self.mode=self.expiry=None
            self.emissions.clear()
            self.next_attack=frame+1
            self.log("skill_mode_ended",frame)
        self.sp.advance(self.step,stopped=self.mode is not None)
        if frame<self.next_attack or self.mode is not None and frame<=self.mode:
            return
        rows=self.pool(frame,self.cells)
        if not rows:
            self.next_attack=frame+3
        elif self.mode is not None:
            self.volley+=1
            emission=frame+self.ticks(self.pre)
            self.emissions[emission]=self.volley
            self.next_attack=frame+self.ticks(self.interval)
            self.log("tornado_attack",frame,volley=self.volley,parallel_ability_frame=emission-1)
        else:
            target=self.world.select(rows,frame)
            self.release_attack(frame,target,self.ordinary,single(self.panel["baseAttackTime"]*100/self.panel["attackSpeed"]))
            self.log("ordinary_attack_start",frame,target=target["id"])

    def strike(self,frame,row,shot,scale,effect):
        key=row["id"]
        state=self.states[key]
        live=self.generation==shot["generation"]
        talent=self.talent if live and effect["airborne_after"] else {}
        pen=_raw_f32(self.trait.get("magic_resist_penetrate_fixed",0)) if live else 0
        power=mul(mul(self.power,scale),_raw_f32(talent.get("atk_scale",1)))
        before=self.world.births[key]["frame"]>shot["release_frame"]
        vulnerable=_vulnerability(self.magic,key,self.trait,before_update=before)[0] if live else ONE
        packet=state.receive_ordinary(frame,source=self.uid,damage_type="arts",attack_q32=power,
            mitigation_q32=max(0,_raw_f32(row["res"])+state.res_add-pen),output_scale_q32=vulnerable)
        seconds=talent.get("silence",0)*self.resistance(key,frame) if not row["silence_immune"] else 0
        if seconds and state.hp>HP_EPSILON:
            state.silence.attach(self.tables,frame,seconds,receiver_born=self.world.births[key]["frame"],
                                 projectile_born=shot["release_frame"],receiver_first_on_tie=True)
        return dict(damage_q32=packet["damage_q32"],hp_after_q32=state.hp,silence_seconds=seconds,source_alive=live)

    def impact(self,frame,row,shot):
        key=row["id"]
        airborne=row.get("airborne",False) or key in self.lifts and frame<self.lifts[key].expires
        result=self.strike(frame,row,shot,ONE,dict(airborne_after=airborne))
        if self.generation==shot["generation"] and self.mode is None and row["level"] in ("ELITE","BOSS"):
            self.sp.points=min(self.sp.maximum,self.sp.points+int(self.trait.get("sp",0)))
        self.log("ordinary_hit",frame,target=key,airborne=airborne,release_frame=shot["release_frame"],**result)

    def after_frame(self,frame):
        for shot in sorted(self.tornadoes,key=lambda s:s["born"]):
            if frame<=shot["born"]:
                continue
            self.bolts(frame,before_birth=shot["born"])
            shot["age"]+=self.step
            if shot["age"]>=_raw_f32(self.life["_lifeTime"]):
                self.tornadoes.remove(shot)
                self.log("tornado_lifetime",frame,volley=shot["volley"],mount=shot["mount"])
                continue
            pos=shot["pos"]
            rows=[r for r in self.world.snapshot(frame,projectile_born=shot["release_frame"])
                  if self.alive(r["id"]) and circle_in_range(r["row"],r["col"],self.radii[r["id"]],[pos],box_query=True)]
            if rows:
                ticks={r["id"]:object_position_frame(self.world.births[r["id"]],frame,self.world.positions,self.world.active,r["id"],
                           observer_born=shot["release_frame"],observer_phase="COROUTINES",same_phase_rule=self.world.enemy_before_projectile)
                       for r in rows}
                ranked=self.world.priority(set(ticks),frame,frame_by_target=ticks) if len(rows)>1 else None
                row=next(r for r in rows if r["id"]==ranked["target"]) if ranked else self.world.select(rows,frame)
                key=row["id"]
                raw=row.get("airborne",False)
                lifted=key in self.lifts and frame<self.lifts[key].expires
                mass=self.mass.weight(row) if self.mass else row["mass_level"]
                effect=ordinary_levitate(self.bb["attack@levitate"],dict(raw_airborne=raw,mass_level=mass,
                    levitate_immune=row["levitate_immune"],levitate_sequence=int(lifted)))
                scale=tornado_scale(self.curve,shot["age"],_raw_f32(self.life["_lifeTime"]),self.bb)
                prior_hp=self.states[key].hp
                result=self.strike(frame,row,shot,scale,effect)
                if effect["levitate_applied"] and self.alive(key):
                    anchor=frame-int(self.world.births[key]["frame"]>shot["release_frame"])
                    lift=self.lifts.get(key)
                    if lift is None:
                        lift=self.lifts[key]=WalkingLevitate(anchor)
                    else:
                        anchor=lift.frame
                    duration=mul(effect["initial_levitate_lifetime_q32"],_raw_f32(self.resistance(key,frame)))
                    lift.apply(anchor,duration,self.step)
                    if anchor<frame:
                        lift.advance(frame)
                    self.states[key].airborne=True
                    self.control(key,frame)
                    if self.world.incoming and key in self.world.incoming.enemies:
                        self.world.incoming.stun(key,lift.expires)
                self.log("tornado_hit",frame,target=key,volley=shot["volley"],mount=shot["mount"],age_q32=shot["age"],
                    emission_frame=shot["release_frame"],born_frame=shot["born"],skill_scale_q32=scale,
                    position=[row["row"],row["col"]],hp_lost_q32=prior_hp-self.states[key].hp,
                    projected_damage_q32=[result["damage_q32"]]*2,**result,**effect)
                self.tornadoes.remove(shot)
            else:
                speed=single(self.mover["_speed"]*self.dt)
                shot["pos"]=tuple(single(round(single(v+single(d*speed))*10000)/10000) for v,d in zip(pos,shot["direction"]))
        self.bolts(frame)
        if self.mass:
            self.mass.contacts(frame)
        if self.magic:
            self.magic.contacts(frame)
        if frame in self.emissions:
            volley=self.emissions.pop(frame)
            dr,dc=self.heading
            for mount,off in self.mounts:
                delay=single(self.rng.value()*self.life["_randomDelayToBorn"])
                born=frame+(self.ticks(delay) if delay>single(1e-5) else 0)
                pos=(self.placement["row"]-dc*off,self.placement["col"]+dr*off)
                self.tornadoes.append(dict(release_frame=frame,born=born,mount=mount,pos=pos,direction=self.heading,age=0,
                                           volley=volley,generation=self.generation))
                self.log("tornado_emit",frame,volley=volley,mount=mount,born_frame=born,first_fixed_update=born+1,
                         birth_delay_seconds=delay,position=list(pos),direction=self.heading)
        self.cleanse(frame,"after_objects")
        for probe in self.probes.get(frame,()):
            key=probe["target"]
            gates=self.states[key].silence.gates(frame,ignore_silence=probe.get("ignore_silence",False),
                                                  silenceable_buff=probe.get("silenceable_buff",True))
            self.log("silence_check",frame,target=key,**gates)
