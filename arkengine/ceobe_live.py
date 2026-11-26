"""Ceobe's charged axe and rapid knife on the live stage receiver list."""
from .baked_mount import single
from .caster_cast import _after_spell_clock, run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_special_start import resolve_special_start
from .caster_start import resolve_cast_start
from .ceobe_lone import ceobe_alone
from .core_caster_skills import _talent, ceobe_peel_ratio
from .damage import HP_EPSILON
from .fixed import ONE, mul
from .mode_replay import _AttackCooldown
from .ranged_operator_battle import RangedOperatorBattle
from .volley_damage import _raw_f32


class RootedRoute:
    """Keep the native motion samples on an uninterrupted leg, including its steering velocity."""
    def __init__(self,world,key,frame):
        self.key=key
        self.tick=frame
        self.exit=world.wave.escapes.pop(key,None)
        self.position=world.positions[frame][key]
        self.samples={f:(positions.get(key),key in world.active[f],
                         [entry for entry in world.history.get(f,()) if entry[1]["instance_id"]==key])
                      for f,positions in world.positions.items() if f>=frame}
        world.exits[world.exit_index:]=[pair for pair in world.exits[world.exit_index:] if pair[1]!=key]

    def advance(self,world,frame,slowed,*,speed_cap=None,paused=False):
        following=self.samples.get(self.tick+1)
        # WAIT and disappearance timers keep ticking under bind. Only walking is held.
        if not paused or following and any("wait" in kind and "motion" not in kind for kind,_ in following[2]):
            self.tick+=1
        if self.tick==self.exit:
            world.active[frame].discard(self.key)
            return False
        sample=self.samples.get(self.tick)
        if sample:
            pos,active,history=sample
            if pos is not None:
                self.position=pos
                world.positions[frame][self.key]=pos
            if active:
                world.active[frame].add(self.key)
            else:
                world.active[frame].discard(self.key)
            world.history[frame]=[e for e in world.history.get(frame,()) if e[1]["instance_id"]!=self.key]+history
        return True


class CeobeBattle(RangedOperatorBattle):
    uid="char_2013_cerber"
    native_walk=False

    def __init__(self,tables,request,releases):
        super().__init__(tables,request,releases)
        self.roots={}
        self.held_ticks={}
        self.resume={}
        self.first_mode=None
        self.clears={}
        for event in request.get("root_clears_before_objects",()):
            self.clears.setdefault(event["frame"],[]).append(event["target"])
        self.peel=_talent(tables,self.card,0)
        self.lone=_talent(tables,self.card,1)
        self.trait=_trait_values(self.card)
        self.snapshot="cerber_e_003[t_2]" in self.ev["templates"]
        self.normal=next(c["fields"] for c in self.ev["character"]["components"] if c["path"]=="Modes/Default/Body/Attack" and c["script"]=="RangedAttack")
        path="" if self.slot==1 else "Modes/S2/Body/Attack"
        parts=self.ev["skill" if self.slot==1 else "character"]["components"]
        self.special=next(c["fields"] for c in parts if c["path"]==path and c["script"]=="RangedAttack")
        self.extra_units={u["id"]:u for u in request.get("units",())}
        self.ally_events={}
        for event in request.get("ally_unit_events",()):
            self.ally_events.setdefault(event["frame"]+1,[]).append(event)

    def alone_now(self,at_deploy=False):
        units=dict(self.extra_units)
        if at_deploy and "units_at_deploy" in self.request:
            units={u["id"]:u for u in self.request["units_at_deploy"]}
        for uid,unit in self.units.items():
            p=unit["placement"]
            units[uid]=dict(id=uid,alive=unit["hp"]>HP_EPSILON,row=p["row"],col=p["col"],grid_row=p["row"],grid_col=p["col"],
                            radius=unit["radius"])
        for unit in self.world.scene.predefines:
            p=unit["position"]
            key=unit["inst"]["characterKey"]
            units.setdefault((key,p["row"],p["col"]),dict(id=key,alive=True,row=p["row"],col=p["col"],grid_row=p["row"],grid_col=p["col"],radius=.1))
        return ceobe_alone(self.tables,self.card,dict(self.scene,placement=self.placement),units.values())

    def deploy(self,frame,place):
        super().deploy(frame,place)
        self.mode=self.mode_end=None
        self.last,self.repeats=None,0
        self.registered=self.sp_block=False
        self.casting_until=-1
        self.skill_poll=frame+1
        self.default_poll=self.next_attack
        self.cooldown=None
        self.alone=self.alone_now(at_deploy=self.snapshot)
        self.update_power()
        if self.slot==2:
            clock=run_committed_cast(self.tables,self.ev,horizon=0,facing=self.view,direction=place["direction"])["natural_end"]
            self.duration=clock["end"]-clock["spell_frame"]
        self.log("lone_talent",frame,active=self.alone,sampled_at_deployment=self.snapshot)

    def update_power(self):
        self.power=mul(_raw_f32(self.panel["atk"]),ONE+_raw_f32(self.lone.get("atk",0) if self.alone else 0))
        self.speed=self.panel["attackSpeed"]+(self.lone.get("attack_speed",0) if self.alone else 0)

    def interval(self,skill):
        factor=self.bb.get("base_attack_time",1) if skill else 1
        return single(single(single(self.panel["baseAttackTime"])*single(factor))*100/single(self.speed))

    def control(self,key,frame,ratio=0):
        if key in self.walks:
            if self.blockers and isinstance(self.walks[key],RootedRoute):
                self.walks[key]=self.world.start_walk(self.tables,key,frame,ratio)
            return
        w=self.world
        current=[r for kind,r in w.history.get(frame,()) if r["instance_id"]==key and kind in
                 ("free_motion_sample","post_wait_motion_sample","exit_motion_sample")]
        if (not self.blockers and not (w.incoming and key in w.incoming.enemies)
                and len(current)==1 and not current[0].get("leg_end") and key in w.positions[frame]):
            self.walks[key]=RootedRoute(w,key,frame)
        else:
            super().control(key,frame,ratio)

    def movement(self,key,frame):
        return frame<=self.roots.get(key,-1),False,0

    def update_walks(self,frame):
        for key in self.clears.get(frame,()):
            if key in self.roots:
                self.roots.pop(key)
                self.resume[key]=frame
                self.log("root_cleared",frame,target=key)
        for key in self.roots:
            self.held_ticks[key]=self.held_ticks.get(key,0)+1
        super().update_walks(frame)
        for key,tick in tuple(self.resume.items()):
            if frame==tick:
                self.resume.pop(key)
                if self.alive(key) and key in self.world.positions[frame]:
                    self.log("first_walk_after_root",frame,target=key,route_frame=frame-self.held_ticks[key],
                             previous_position=list(self.world.positions[frame-1][key]),position=list(self.world.positions[frame][key]))
        for key,end in tuple(self.roots.items()):
            if frame==end:
                self.roots.pop(key)
                self.resume[key]=frame+1
                self.log("root_expired",frame,target=key,first_unbound_move_tick=frame+1)

    def choose(self,frame,skill):
        rows=self.pool(frame,self.cells)
        if rows and skill:
            if self.slot==1:
                rows=[r for r in rows if not r["blocked"]] or rows
            else:
                defenses={r["id"]:self.states[r["id"]].effective_defense(_raw_f32(r["defense"])) for r in rows}
                highest=max(defenses.values())
                rows=[r for r in rows if defenses[r["id"]]==highest]
        return self.world.select(rows,frame) if rows else None

    def register(self,frame,source):
        cast=resolve_special_start(self.tables,self.ev,frame=frame,phase="before_coroutines",direction=self.placement["direction"],
            facing=self.view,source=source,sp=self.sp.points,trigger_search_passed=True)
        self.sp.points=cast["sp_after"]
        self.sp_block=True
        self.events.extend(cast["events"])

    def tick(self,frame):
        for e in self.ally_events.get(frame,()):
            if e["kind"]=="ally_deploy":
                self.extra_units[e["unit"]["id"]]=e["unit"]
            else:
                self.extra_units.pop(e["id"],None)
        if not self.world.present or frame==self.generation:
            return
        active=self.mode is not None and self.mode<frame<self.mode_end
        if not self.snapshot and self.alone_now()!=self.alone:
            self.alone=not self.alone
            self.update_power()
            if self.cooldown and frame<=self.next_attack:
                self.cooldown.change_period(frame-1,self.step,_raw_f32(self.interval(active)))
                remaining=self.cooldown.remaining_at(frame-1,self.step)
                self.next_attack=frame-1+(remaining+self.step-1)//self.step
            self.log("lone_talent_changed",frame,active=self.alone,next_attack_frame=self.next_attack)
        if frame==self.mode_end:
            self.pending.clear()
            self.log("skill_mode_ended",frame)
            self.default_poll=self.next_attack=frame+1
            self.mode=self.mode_end=None
        if self.mode is not None and frame==self.mode+1:
            self.next_attack=self.default_poll=frame
        slot1=self.slot==1
        skill=self.registered if slot1 else active
        poll=self.skill_poll if slot1 and self.registered else self.default_poll
        row=None
        if frame>=max(self.next_attack,poll) and (self.mode is None or active):
            if slot1 and self.registered:
                self.skill_poll=frame+3
            else:
                self.default_poll=frame+3
            row=self.choose(frame,skill)
            if row and slot1 and not skill and self.sp.points>=self.sp.cost and frame>self.casting_until:
                skill=True
                row=self.choose(frame,True)
                self.skill_poll=max(self.skill_poll,frame+3)
                self.register(frame,"before_attack")
        if row:
            interval=self.interval(skill)
            shot=self.release_attack(frame,row,self.special if skill else self.normal,interval,"Modes/S2" if active else None,
                                     skill=skill,power=self.power)
            self.cooldown=_AttackCooldown(frame,_raw_f32(interval))
            if skill and slot1:
                self.registered=False
                clock=_after_spell_clock(shot["release_frame"]-frame,shot["clock"]["cached_duration_seconds"],self.step,same_frame_resume=True)
                self.casting_until=frame+clock["end"]
            self.log("attack_start",frame,target=row["id"],skill_attack=skill,blocked=row["blocked"],selection_position=[row["row"],row["col"]])
        self.sp.advance(self.step,stopped=self.sp_block if slot1 else self.mode is not None)
        if slot1:
            self.sp_block=frame<=self.casting_until
            if frame>self.casting_until and not self.registered and frame>=self.skill_poll:
                self.skill_poll=frame+3
                if self.sp.points>=self.sp.cost and self.choose(frame,True):
                    self.register(frame,"auto_tick")
                    self.registered=True

    def button(self,frame,a):
        if self.slot!=2 or self.mode is not None or self.sp.points<self.sp.cost:
            self.log("skill_rejected",frame,sp=self.sp.points)
            return
        cast=resolve_cast_start(self.tables,self.ev,frame=frame,facing=self.view,direction=self.placement["direction"],
            owner_state="attack" if self.pending else "idle",phase=a.get("phase","after_tick"),trigger="MANUAL",sp=self.sp.points)
        self.sp.points=cast["sp_after"]
        self.mode=cast["spell_frame"]
        if self.first_mode is None:
            self.first_mode=self.mode
        for shot in self.pending:
            self.log("normal_attack_interrupted",frame,target=shot["target"])
        self.mode_end=self.mode+self.duration
        self.pending.clear()
        self.events.extend(cast["events"])

    def after_frame(self,frame):
        self.bolts(frame)

    def run(self):
        result=super().run()
        mode=self.first_mode if self.first_mode is not None and self.first_mode<=result["through_frame"] else None
        result.update(mode_start=mode,lone_talent_active=getattr(self,"alone",False),
                      lone_talent_clock="deployment" if self.snapshot else "current")
        if result["stop"]=="horizon":
            result["status"]="ceobe_replacement_battle" if self.slot==1 else "ceobe_mode_battle" if mode is not None else "ceobe_ordinary_battle"
        return result

    def impact(self,frame,row,shot):
        key=row["id"]
        state=self.states[key]
        live=self.generation==shot["generation"]
        if live:
            self.repeats=self.repeats+1 if self.last==key else 0
            self.last=key
        defense=state.effective_defense(_raw_f32(row["defense"]))
        res=max(0,_raw_f32(row["res"])+state.res_add-(_raw_f32(self.trait.get("magic_resist_penetrate_fixed",0)) if live else 0))
        ratio=ceobe_peel_ratio(self.peel,self.repeats) if live else 0
        peel=state.receive_ordinary(frame,source="peel",damage_type="arts",attack_q32=mul(defense,_raw_f32(ratio)),mitigation_q32=res) if live else None
        scale=self.bb.get("atk_scale",1) if shot["skill"] else 1
        main=state.receive_ordinary(frame,source="attack",damage_type="arts",attack_q32=mul(self.power if live else shot["power"],_raw_f32(scale)),mitigation_q32=res)
        credit=0
        if live and row["level"] in ("ELITE","BOSS") and not (self.sp_block or self.mode is not None):
            credit=int(self.trait.get("sp",0))
            self.sp.points=min(self.sp.maximum,self.sp.points+credit)
        seconds=0
        flags=self.world.wave.flags.at(key,frame) if self.world.wave.flags else ()
        if self.slot==1 and shot["skill"] and not row.get("root_immune") and "ROOT_IMMUNE" not in flags and state.hp>HP_EPSILON:
            seconds=self.bb["duration"]
            born=self.world.births[key]["frame"]
            anchor=frame-int(born>shot["release_frame"])
            ticks=(_raw_f32(seconds*self.resistance(key,frame))+self.step-1)//self.step
            self.roots[key]=max(self.roots.get(key,0),anchor+ticks)
            self.control(key,frame)
            self.log("root_applied",frame,target=key,expiry_frame=self.roots[key])
        self.log("impact",frame,target=key,skill_attack=shot["skill"],effective_defense_q32=defense,peel_ratio=ratio,
                 peel=peel,main=main,impact_position=[row["row"],row["col"]],root_seconds=seconds,module_sp_credit=credit,hp_after_q32=state.hp,release_frame=shot["release_frame"])
