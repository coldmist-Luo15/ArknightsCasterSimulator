"""和弦的储能、两套攻击模式和水域共用关卡的移动、阻挡与受击状态。"""
from .attack_range import circle_in_range
from .blocking import StationaryBlockSearch
from .caster_cast import run_committed_cast
from .caster_start import resolve_cast_start
from .five_star_skills import HAMONI
from .harmonie_water import WaterAttacks, _combat_clip, _water_assets
from .ranged_operator_battle import RangedOperatorBattle
from .sbell_live import blocked_enemy_attack
from .skill_recovery import RecoveryTimer
from .stage_channel import object_position_frame
from .volley_damage import _raw_f32


class HarmonieBattle(RangedOperatorBattle):
    uid=HAMONI

    def __init__(self,tables,request,releases):
        super().__init__(tables,request,releases)
        self.pulse,self.charge_key=_water_assets(self.ev)
        self.hidden=request.get("enemy_invisible_until",{})
        self.wet=set()
        self.dots={}
        self.held={}
        self.combat_end={}
        self.resume_move={}
        self.attacks=None
        self.attach=self.finish=self.recovery=None
        self.casts=[]
        if request.get("blocker"):
            candidates=[dict(row,row=0,col=0,motion_mode="FLY" if row.get("airborne") else "WALK",
                             instance_uid=self.world.births[key]["order"]) for key,row in self.world.stats.items()]
            self.blockers["blocker"]=StationaryBlockSearch(tables,request["blocker"],candidates)

    def deploy(self,frame,place):
        super().deploy(frame,place)
        self.first_attack=self.next_attack
        self.clock=run_committed_cast(self.tables,self.ev,horizon=0,facing=self.view,direction=place["direction"])["natural_end"]
        self.attach=self.finish=None
        self.recovery=frame+1
        flights=self.attacks.projectiles if self.attacks else []
        self.attacks=self.new_mode(self.first_attack-1,0,False)
        self.attacks.projectiles=flights

    def new_mode(self,frame,stored,active):
        return WaterAttacks(self.tables,self.card,self.ev,self.placement,self.view,self.cells,frame,stored,self.charge_key,self.events,active=active)

    def clear_owner(self):
        super().clear_owner()
        self.attach=self.finish=None
        self.wet.clear()
        self.dots.clear()
        if self.attacks:
            self.attacks.pending=self.attacks.search=None

    def movement(self,key,frame):
        return frame<self.resume_move.get(key,0),key in self.wet,self.bb["move_speed"]

    def blocked_attack(self,frame,uid,key):
        ready,_=self.melee_clocks[uid,key]
        if frame==ready:
            enemy=self.world.stats[key]
            if enemy["enemy_key"] not in self.melee_profiles:
                self.melee_profiles[enemy["enemy_key"]]=blocked_enemy_attack(self.tables,self.world.wave,self.world.incoming,key)
            period=self.melee_profiles[enemy["enemy_key"]][1]
            clip=_combat_clip(self.tables,enemy["prefab"])
            winddown=min(period,self.ticks(clip)) if clip is not None else period
            self.combat_end[key]=frame+winddown+1
            if uid=="blocker":
                self.melee_clocks[uid,key]=(frame+period,None)
        if uid!="blocker":
            super().blocked_attack(frame,uid,key)

    def update_walks(self,frame):
        # A departing holder releases eligibility immediately. A committed melee
        # animation finishes before the receiver resumes following its route.
        for key,uid in self.held.items():
            if uid not in self.blockers or self.blockers[uid].withdraw==frame:
                self.resume_move[key]=max(frame,self.combat_end.get(key,frame))+1
        super().update_walks(frame)
        held={key:uid for uid,b in self.blockers.items() for key in b.manager.blocked}
        for key in self.held.keys()-held.keys():
            if self.alive(key):
                first=self.resume_move[key]=max(frame,self.combat_end.pop(key,frame))+1
                self.log("released_enemy_resume_move",frame,target=key,first_move_frame=first,position=self.world.positions[frame].get(key))
                if _combat_clip(self.tables,self.world.stats[key]["prefab"]) is None:
                    self.world.note_mount(key,frame,"ordinary_blocked_attack_interval_before_move")
        self.held=held
        if self.world.wave.flags:
            self.world.wave.flags.update_blocked(set(held),frame)

    def button(self,frame,action):
        if self.finish and frame<self.finish:
            self.log("skill_while_active",frame,sp=self.sp.points)
            return
        awake=self.attacks.awake_until is not None and frame<self.attacks.awake_until
        state="born" if frame<self.first_attack-1 else "attack" if awake else "idle"
        cast=resolve_cast_start(self.tables,self.ev,frame=frame,sp=self.sp.points,direction=self.placement["direction"],
                                facing=self.view,phase=action.get("phase","after_tick"),owner_state=state)
        self.events.extend(cast["events"])
        self.casts.append(cast)
        if cast["cast_frame"] is not None:
            self.sp.points=cast["sp_after"]
            self.attach=cast["spell_frame"]
            self.finish=cast["cast_frame"]+self.clock["end"]
            self.recovery=cast["cast_frame"]+self.clock["first_recovery_tick"]

    def damage(self,frame,key,power,kind,**details):
        if self.invincible(key,frame):
            return
        state=self.states[key]
        hit=state.receive_ordinary(frame,source=self.uid,damage_type="arts",attack_q32=power,
                    mitigation_q32=_raw_f32(self.world.stats[key]["res"])+state.res_add)
        self.log(kind,frame,target=key,damage_q32=hit["damage_q32"],hp_q32=state.hp,hp_after_q32=state.hp,
                 position=self.world.positions[frame][key],**details)

    def water(self,frame,rows):
        for key in tuple(self.wet-rows.keys()):
            self.wet.remove(key)
            self.dots.pop(key,None)
        for key,row in rows.items():
            contact=(frame>self.attach and frame>self.world.births[key]["frame"] and not row.get("airborne",False)
                     and circle_in_range(*row["position"],self.radii[key],self.cells,box_query=True))
            if key in self.wet and key not in self.dots and row["targetable"] and (frame-self.attach-1)%10==0:
                tick=object_position_frame(self.world.births[key],frame,self.world.positions,self.world.active,key,
                                           observer_born=self.generation,observer_phase="INPUT")
                first=frame+int(tick==frame)
                self.attach_dot(key,first)
                self.log("water_damage_attached",frame,target=key,first_target_tick=first)
            if key in self.dots:
                first,timer=self.dots[key]
                if frame>=first and timer.advance(self.step):
                    self.damage(frame,key,_raw_f32(self.bb["damage_value"]),"water_damage")
            if contact and key not in self.wet and self.alive(key):
                self.wet.add(key)
                self.control(key,frame,self.bb["move_speed"])
                if row["targetable"]:
                    self.attach_dot(key,frame+1)
                self.log("water_enter",frame,target=key,position=row["position"],blocked=row["blocked"],
                         primary_eligible=(row["blocked"] or row["is_blocking"]) and row["targetable"],speed_delta=self.bb["move_speed"],stored=self.attacks.stored)
            elif not contact and key in self.wet:
                self.wet.remove(key)
                self.dots.pop(key,None)
                self.log("water_exit",frame,target=key,position=row["position"])

    def attach_dot(self,key,first):
        timer=RecoveryTimer(_raw_f32(self.pulse))
        timer.remaining=0
        self.dots[key]=(first,timer)

    def tick(self,frame):
        if not self.attacks:
            return
        if self.world.present and frame>self.generation:
            self.sp.advance(self.step,stopped=frame<self.recovery)
        rows={}
        for key in self.world.positions[frame]:
            row=self.world.receiver_at(key,frame) if self.alive(key) else None
            if row:
                flags=self.world.wave.flags.at(key,frame) if self.world.wave.flags else ()
                row.update(position=(row["row"],row["col"]),radius=self.radii[key],blocked=key in self.held,
                           is_blocking=row.get("is_blocking",False) or "BLOCKING" in flags,
                           targetable=self.world.selectable(key,frame) and frame>self.hidden.get(key,-1))
                rows[key]=row
        if self.world.present and self.attach is not None and self.attach<=frame<=self.finish:
            self.water(frame,rows)
        hp={k:self.states[k].hp for k in rows}
        def pick(keys,tick):
            if len(keys)==1:
                return keys[0]
            ordered=self.world.wave.attack_order(keys,tick,after_objects=True)
            self.log("route_priority",tick,target=ordered[0],rank_by_target={key:i for i,key in enumerate(ordered)})
            return ordered[0]
        def apply(tick,key,power,kind,**details):
            self.damage(tick,key,power,kind,**details)
            hp[key]=self.states[key].hp
        self.attacks.advance(frame,rows,hp,pick,apply)
        if self.world.present and frame in (self.attach,self.finish):
            prior=self.attacks
            active=frame==self.attach
            self.attacks=self.new_mode(frame,prior.stored,active)
            self.attacks.face_sign=prior.face_sign
            self.attacks.projectiles=prior.projectiles
            self.log("water_mode_start" if active else "restore_default_mode",frame,
                     retained_projectiles=len(prior.projectiles),stored=prior.stored)
            if prior.awake_until and frame<prior.awake_until:
                self.attacks.search=frame
                self.attacks.advance(frame,rows,hp,pick,apply,move_flights=False)
            if not active:
                self.log("water_auras_detached",frame,owners_losing_water=sorted(self.wet))
                self.wet.clear()
                self.dots.clear()

    def run(self):
        result=super().run()
        result.update(casts=self.casts,stored=self.attacks.stored if self.attacks else 0,
                      skill_end_frame=self.finish,first_unblocked_recovery_tick=self.recovery)
        return result
