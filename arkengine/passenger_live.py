"""Passenger S2 and S3 on the continuous stage clock."""
from .attack_range import circle_in_range
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_ranges import skill_range_cells
from .caster_start import resolve_cast_start
from .chain_targets import nearest_chain_target, projectile_hatred_tie
from .elemental_projectiles import stable_enemy_mount, stationary_cast_mounts
from .fixed import ONE, mul
from .module_effects import active_board, talent_rows
from .mostima_aura import set_period
from .passenger_chain_stage import _attack_intervals, _projectiles, chain_flight_profile
from .passenger_isolation import isolation_clock, isolated_in
from .passenger_storm import passenger_boards, storm_profile
from .ranged_operator_battle import RangedOperatorBattle
from .skill_recovery import _divide
from .target_priority import native_box_candidates
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


class PassengerBattle(RangedOperatorBattle):
    uid="char_472_pasngr"

    def __init__(self,tables,request,releases):
        super().__init__(tables,request,releases)
        _,self.talents,self.trait=passenger_boards(tables,self.card)
        self.normal,self.special=_projectiles(self.ev,self.slot)
        phase=self.card["module_phase"] or {}
        extra=active_board(talent_rows(phase,1,"10",[]),self.card["elite"],self.card["level"],self.card["potential"])
        self.fast=_divide(ONE,ONE+_raw_f32(extra.get("sp_recovery_per_sec",0)))
        self.intervals=_attack_intervals(self.panel,self.bb,self.ev)
        self.marks={}
        self.slow={}
        self.storm_slow={}
        self.storm_slow_sources={}
        self.zones=[]
        self.zone_id=0
        self.launch=None
        self.mode=self.mode_end=None
        self.sluggish=self.tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"][0]["value"]
        self.history=[]
        self.storm_draws=0
        self.sp_after_cast=None

    def deploy(self,frame,place):
        super().deploy(frame,place)
        self.layout=dict(placement=self.placement,rows=self.scene["rows"],cols=self.scene["cols"])
        self.skill_cells=skill_range_cells(self.tables,self.card,self.layout)["cells"]
        self.isolation_timer,self.adjacent=isolation_clock(self.tables,self.ev,place,self.scene["rows"],self.scene["cols"])
        self.isolated=False
        self.resume=frame+1
        self.mode=self.mode_end=None
        clock=run_committed_cast(self.tables,self.ev,horizon=180,facing=self.view,direction=place["direction"])
        self.cast_clock=clock["natural_end"]
        self.marks.clear()
        self.paid=self.orders.paid[self.uid]

    def before_incoming(self,frame):
        self.current_frame=frame

    def clear_owner(self):
        super().clear_owner()
        self.zones.clear()
        for key,sources in self.storm_slow_sources.items():
            active=[born for born,expiry in sources.items() if expiry>self.current_frame]
            if active and self.alive(key):
                last=self.current_frame+int(self.world.births[key]["frame"]<=max(active))
                self.slow[key]=max(self.slow.get(key,-1),last)
                self.log("storm_sluggish_detached",self.current_frame+1,target=key,last_slow_move_tick=self.slow[key])
        self.storm_slow.clear()
        self.storm_slow_sources.clear()
        self.launch=None
        self.mode=self.mode_end=None

    def withdraw(self,frame):
        cancelled=[z["id"] for z in self.zones]
        super().withdraw(frame)
        for event in reversed(self.events):
            if event["kind"]=="withdraw" and event["frame"]==frame:
                event["cancelled_zones"]=cancelled
                break

    def movement(self,key,frame):
        return False,frame<=max(self.slow.get(key,-1),self.storm_slow.get(key,-1)),self.sluggish

    def tick(self,frame):
        if not self.world.present or frame==self.generation:
            return
        if self.isolation_timer.advance(self.step):
            rows=[dict(r,radius=self.radii[k]) for k in self.states if self.alive(k) and (r:=self.world.by_id(k,frame))]
            isolated=isolated_in(self.adjacent,rows)
            if isolated!=self.isolated:
                self.isolated=isolated
                self.log("isolation_changed",frame,isolated=isolated)
                if self.fast!=ONE:
                    set_period(self.sp,self.fast if isolated else ONE,stopped=frame<self.resume)
        self.sp.advance(self.step,stopped=frame<self.resume)
        if frame==self.mode_end:
            self.pending.clear()
            self.mode=self.mode_end=None
            self.next_attack=frame+1
            self.log("skill_mode_ended",frame)
        if self.mode is not None and frame==self.mode+1:
            self.next_attack=frame
        active=self.mode is not None and frame>self.mode
        if frame<self.next_attack or self.mode is not None and not active:
            return
        rows=self.pool(frame,self.skill_cells if active else self.cells)
        if not rows:
            self.next_attack=frame+3
            return
        if self.pending or any(s["generation"]==self.generation and s.get("wait",True) for s in self.flights):
            self.next_attack=frame+1
            return
        row=self.world.select(rows,frame)
        ability,radius=self.special if active else self.normal
        self.release_attack(frame,row,ability,self.intervals[int(active)],radius=radius,enhanced=active,isolated=self.isolated)
        self.log("attack_start" if self.slot==2 else "ordinary_attack_start",frame,target=row["id"],skill_attack=active,sp=self.sp.points)

    def button(self,frame,action):
        options=self.pool(frame,self.skill_cells) if self.slot==3 else []
        if self.sp.points<self.sp.cost or frame<self.resume-1 or self.mode is not None or self.slot==3 and not options:
            self.log("skill_button_rejected",frame,sp=self.sp.points)
            return
        cast=resolve_cast_start(self.tables,self.ev,frame=frame,phase=action.get("phase","after_tick"),sp=self.sp.points,
            owner_state="attack" if self.pending else "idle",direction=self.placement["direction"],facing=self.view)
        if cast["cast_frame"] is None:
            self.events.extend(cast["events"])
            return
        self.sp.points=cast["sp_after"]
        self.sp_after_cast=self.sp.points
        self.resume=cast["cast_frame"]+self.cast_clock["first_recovery_tick"]
        if self.pending:
            self.log("attack_interrupted_by_skill_input",frame,target=self.pending[0]["target"])
            self.pending.clear()
        for flight in self.flights:
            flight["wait"]=False
        self.events.extend(e for e in cast["events"] if e["kind"]!="projectile_release")
        if self.slot==2:
            self.mode=cast["spell_frame"]
            self.mode_end=cast["cast_frame"]+self.cast_clock["end"]
        else:
            highest=max(self.states[r["id"]].hp for r in options)
            row=self.world.select([r for r in options if self.states[r["id"]].hp==highest],frame)
            shot=next(e for e in cast["events"] if e["kind"]=="projectile_release")
            self.launch=(shot,row["id"],row)
            self.next_attack=max(self.next_attack,cast["cast_frame"]+self.cast_clock["end"]+1)

    def chain(self,frame,rows,center,count,radius,born,*,first=None):
        result=[first] if first else []
        def tie(keys):
            return projectile_hatred_tie(self.world,keys,frame,born)
        while len(result)<count:
            key=nearest_chain_target(frame,center,rows,self.radii,result,radius,nearest_tie=tie,continuous=True)
            if key is None:
                break
            result.append(key)
            center=next(r for r in rows if r["id"]==key)
        return result

    def hit(self,frame,key,bounce,born,source,enhanced,isolated,live):
        target=self.world.stats[key]
        state=self.states[key]
        anchor=frame-int(self.world.births[key]["frame"]>born)
        talent=self.talents[0]
        threshold=_raw_f32(talent["hp_ratio"])
        if live and state.hp*ONE>=_raw_f32(target["hp"])*threshold:
            duration=_raw_f32(talent["pasngr_t_1[enhance].duration"])
            self.marks[key]=anchor+(duration+self.step-1)//self.step
        marked=live and anchor<self.marks.get(key,-1)
        storm=source=="storm"
        scale=_raw_f32(self.bb["atk_scale"] if storm else 1)
        decay=self.trait.get("skill@chain.atk_scale" if storm else "attack@chain.atk_scale",self.trait.get("attack@chain.atk_scale",.85))
        for _ in range(bounce):
            scale=mul(scale,_raw_f32(decay))
        bonus=self.talents[1]["atk"] if isolated else 0
        if self.slot==2 and (live and self.mode is not None and self.mode<frame<self.mode_end or not live and enhanced):
            bonus+=self.bb["atk"]
        power=mul(mul(_raw_f32(self.panel["atk"]),ONE+_raw_f32(bonus)),scale)
        hit=state.receive_ordinary(frame,source=self.uid,damage_type="arts",attack_q32=power,
            mitigation_q32=max(0,_raw_f32(target["res"])+state.res_add),
            output_scale_q32=_raw_f32(talent["pasngr_t_1[enhance].damage_scale"]) if marked else ONE)
        seconds=self.trait.get("skill@sluggish",self.bb.get("sluggish",self.trait["attack@sluggish"])) if storm else self.trait["attack@sluggish"]
        if self.alive(key):
            ticks=(_raw_f32(seconds*self.resistance(key,frame))+self.step-1)//self.step
            slows=self.storm_slow if storm else self.slow
            slows[key]=max(slows.get(key,-1),anchor+ticks)
            if storm:
                sources=self.storm_slow_sources.setdefault(key,{})
                sources[born]=anchor+ticks
            self.control(key,frame,self.sluggish)
        return dict(target=key,bounce=bounce,hp_q32=state.hp,hp_after_q32=state.hp,damage_q32=hit["damage_q32"],marked=marked,
                    isolated=isolated,sluggish_seconds=seconds,skill_attack=enhanced,release_frame=born)

    def advance_flight(self,frame,shot):
        key,born=shot["target"],shot["release_frame"]
        row=self.world.by_id(key,frame,projectile_born=born) if self.alive(key) else None
        if row:
            shot["last_mount"]=stable_enemy_mount(self.tables,row)
        move=ordinary_trace_step(shot["position"],shot["last_mount"],effective_dt=self.dt,round_position=True,speed=shot["speed"])
        shot["position"]=move.position
        if not move.reached and frame<shot["expiry"]:
            return
        rows=[r for r in self.world.snapshot(frame,projectile_born=born) if self.alive(r["id"])]
        if not row:
            self.log("chain_reached_lost_target",frame,target=key,release_frame=born)
        center=row or dict(row=move.position[1],col=move.position[0])
        count=int(self.bb["attack@max_target"] if shot["enhanced"] else self.trait["attack@max_target"])
        live=self.generation==shot["generation"]
        for bounce,recipient in enumerate(self.chain(frame,rows,center,count,shot["radius"],born)):
            hit=self.hit(frame,recipient,bounce,born,"bolt",shot["enhanced"],self.isolated if live else shot["isolated"],live)
            hit["position"]=next((r["row"],r["col"]) for r in rows if r["id"]==recipient)
            self.log("chain_impact" if self.slot==2 else "ordinary_chain_impact",frame,**hit)
            if self.alive(recipient):
                self.log("sluggish_applied",frame,target=recipient,last_slow_move_tick=self.slow[recipient])
        self.flights.remove(shot)

    def pulse(self,frame,zone):
        born=zone["frame"]
        rows=[r for r in self.world.snapshot(frame,projectile_born=born) if self.alive(r["id"])]
        pool={r["id"]:r for r in rows if circle_in_range(r["row"],r["col"],self.radii[r["id"]],zone["cells"],box_query=True)}
        hits=[]
        if pool:
            before=self.rng.draws
            first_id,=self.rng.select_targets(native_box_candidates(self.world,pool),1)
            first=pool[first_id]
            self.storm_draws+=self.rng.draws-before
            chain=self.chain(frame,rows,first,int(self.bb["chain.max_target"]),zone["radius"],born,first=first["id"])
            for bounce,key in enumerate(chain):
                hit=self.hit(frame,key,bounce,born,"storm",True,zone["isolated"],True)
                hits.append(hit)
                self.log("storm_hit",frame,zone=zone["id"],**hit)
        self.log("storm_pulse" if hits else "empty_storm_pulse",frame,zone=zone["id"],pulse=(frame-born)//zone["interval"],hits=hits)

    def after_frame(self,frame):
        due=[z for z in self.zones if frame>z["frame"] and (frame-z["frame"])%z["interval"]==0]
        callbacks=[(s["release_frame"],self.advance_flight,s) for s in self.flights]+[(z["frame"],self.pulse,z) for z in due]
        for _,callback,obj in sorted(callbacks,key=lambda entry:entry[0]):
            callback(frame,obj)
        self.zones[:]=[z for z in self.zones if frame<z["frame"]+z["interval"]*z["count"]]
        for shot in self.pending[:]:
            if shot["release_frame"]!=frame:
                continue
            self.pending.remove(shot)
            key=shot["target"]
            row=self.world.receiver_at(key,frame) if self.alive(key) else None
            if row:
                shot["position"],shot["last_mount"]=stationary_cast_mounts(self.tables,self.uid,shot["clock"],self.placement,row,self.view,caster_face_sign=shot["sign"])
                speed,lifetime=chain_flight_profile(self.ev,shot["projectile"])
                shot.update(speed=speed,expiry=frame+self.ticks(lifetime)+1,isolated=self.isolated)
                self.flights.append(shot)
                self.log("ordinary_projectile_release" if self.slot==3 else "projectile_release",frame,target=key,projectile=shot["projectile"])
            else:
                self.next_attack=frame+1
        if self.launch and self.launch[0]["frame"]==frame:
            shot,key,cached=self.launch
            self.launch=None
            target=self.world.receiver_at(key,frame) if self.alive(key) else None
            target=target or cached
            row,col=(round(single(target[axis])) for axis in ("row","col"))
            area=skill_range_cells(self.tables,self.card,dict(self.layout,area="storm",target_tile=dict(row=row,col=col)))["cells"]
            _,_,interval,count=storm_profile(self.tables,self.card,self.ev,shot["projectile"])
            self.zone_id+=1
            variants=self.ev["projectiles"][shot["projectile"]]
            radius=next(c["fields"]["m_Radius"] for prefab in variants.values() for c in prefab["components"] if c["script"]=="CircleCollider2D")
            zone=dict(id=self.zone_id,frame=frame,cells=area,interval=interval,count=count,center=[row,col],target=key,
                      radius=radius,isolated=self.isolated)
            self.zones.append(zone)
            self.history.append({k:v for k,v in zone.items() if k!="cells"})
            self.log("projectile_release",frame,zone=self.zone_id,target=key,center=[row,col],projectile=shot["projectile"])

    def run(self):
        result=super().run()
        result.update(storms=self.history,native_draws=self.storm_draws,hp_q32=result["remaining_hp_q32"],sp_after_cast=self.sp_after_cast)
        if result["deployment"]:
            result["deployment"]["paid"]=self.paid
        if self.history:
            first=self.history[0]
            result.update(target=first["target"],target_enemy=self.world.stats[first["target"]]["enemy_key"],storm_center=first["center"])
        return result
