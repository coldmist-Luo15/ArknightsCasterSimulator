"""Nymph's nightmare projectile, S3 assault and Lost Soul on shared receivers."""
from .attack_range import circle_in_range, range_cells
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_model import _assets
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .elemental_casters import _board
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .eyja_ignition import ignition_circle_overlap
from .fixed import ONE, mul
from .marcil_deploy import _birth
from .mostima_aura import set_period
from .mystic_timing import fixed_wait_frames
from .nymph_impact import s3_damage_scales
from .nymph_soul import SoulRules, SoulOnTarget, y_recovery_period
from .skill_recovery import _divide, deploy_skill
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


class NymphCombat:
    def __init__(self,battle,actor):
        self.b,self.a,self.card=battle,actor,actor.card
        t=battle.tables
        self.ev=_assets(t,self.card)
        self.board={b["key"]:b["value"] for b in self.ev["skill_blackboard"]}
        self.normal=default_attack(t,actor.uid)["fields"]
        self.slot=self.card["skill"]
        if self.slot==2:
            self.skill,=(c["fields"] for c in self.ev["skill"]["components"] if c["script"]=="RangedAttack")
            variants=self.ev["projectiles"][self.skill["_projectileKey"]].values()
            if any(not c["fields"]["_stopWhenSourceInvalid"] for p in variants for c in p["components"] if c["script"]=="SimpleProjectile"):
                raise ValueError("妮芙二技能的退场清弹配置变化")
            delay,={c["fields"]["_delayAfterReached"] for p in variants for c in p["components"] if c["script"]=="AdvancedMovement"}
            self.blast_delay=fixed_wait_frames(delay/battle.dt)-1
        else:
            self.skill,=(c["fields"] for c in self.ev["character"]["components"] if c["path"]=="Modes/S3/Body/Attack" and c["script"]=="RangedAttackWithExtraActions")
            self.scales=s3_damage_scales(self.skill,self.board)
        self.talent=_board(self.card,1,"2")
        self.rules=SoulRules.from_card(t,self.card,self.ev)
        self.factor=_raw_f32(_trait_values(self.card).get("damage_scale",1))
        self.y=y_recovery_period(t,self.card,self.ev)
        self.global_talent=self.card["module"]=="uniequip_002_nymph" and self.card["module_level"]>=2
        self.cells=[range_cells(r,actor.place,battle.rows,battle.cols) for r in (self.card["base_range"],self.card["skill_range"] or self.card["base_range"])]
        self.speeds={}
        for ability in (self.normal,self.skill):
            k=ability["_projectileKey"]
            self.speeds[k],={c["fields"]["_speed"] for p in self.ev["projectiles"][k].values() for c in p["components"] if c["script"]=="AdvancedMovement"}
        self.face="Back" if actor.direction=="UP" else "Front"
        self.sign=-1 if actor.direction=="LEFT" else 1
        self.gauge,_=deploy_skill(t,self.card,battle.frame)
        self.born=battle.frame+_birth(t,self.card,self.ev,self.face)["born_exit_update"]
        self.poll=self.born+1
        self.unblock=battle.frame+1
        self.pending=self.mode=self.restore=self.commit=None
        self.stacks=0
        self.shots=[]
        self.release_s2=None
        self.nightmares=[]
        self.blasts=[]
        self.notes=set()
        self.souls={}
        self.closed=False

    def active(self):
        return self.slot==3 and not self.closed and self.mode is not None and self.mode<self.b.frame<self.restore

    def atk(self):
        return self.b.attack(self.a,self.talent["atk"]*self.stacks+(self.board["atk"] if self.active() else 0))

    def dark_started(self,target):
        if self.global_talent or circle_in_range(target.row,target.col,target.radius,self.cells[0],box_query=True):
            self.stacks=min(self.stacks+1,int(self.talent["max_stack_cnt"]))
            self.b.log("nymph_dark_stack",target=target.id,stacks=self.stacks)

    def hit(self,e,skill,cached):
        elements=self.b.logos
        s=elements.receiver(e)
        soul=self.souls.setdefault(e.id,SoulOnTarget(self.rules))
        if not self.closed:
            soul.before_attack_damage()
        dark=s.burst=="DARK"
        factor=self.factor if s.burst else ONE
        atk=cached if self.closed else self.atk()
        scale=self.scales[1 if dark else 0] if skill else ONE
        self.b.arts(self.a,e,atk,scale_raw=scale,output_raw=factor,kind="nymph_s3_hit" if skill else "nymph_ordinary_hit")
        if dark and skill and e.alive:
            s.hp=e.hp
            s.apply_elemental_hp(self.b.frame,mul(atk,self.scales[2]),source="nymph_s3",output_scale_q32=factor)
            elements._settle(e,s)

    def nightmare_hit(self,target,kind):
        state=self.b.logos.receiver(target)
        soul=self.souls.setdefault(target.id,SoulOnTarget(self.rules))
        soul.before_attack_damage()
        damage=self.b.arts(self.a,target,self.atk(),scale_raw=_raw_f32(self.board["atk_scale"]),
                           output_raw=self.factor if state.burst else ONE,kind=kind)
        if target.alive:
            state.hp=target.hp
            state.apply_buildup(self.b.frame,"DARK",mul(damage,_raw_f32(self.board["ep_damage_ratio"])))
            soul.after_s2_buildup(state)
            self.b.logos._settle(target,state)

    def move_nightmares(self):
        from .recal_wolves import apply_recal_fear
        retained=[]
        for shot in self.nightmares:
            enemy=shot['target']
            if enemy.alive and not enemy.cursor.hidden and not shot['fixed']:
                shot['last']=(enemy.row,enemy.col)
            else:
                shot['fixed']=True
            row,col=shot['last']
            dx,dy,z=shot['offset']
            move=ordinary_trace_step(shot['position'],(col+dx,row+dy,z),effective_dt=self.b.dt,
                                     round_position=True,speed=self.speeds[self.skill['_projectileKey']])
            if not move.reached:
                shot['position']=move.position
                retained.append(shot)
                continue
            if not shot['fixed']:
                apply_recal_fear(self.b,enemy,(self.a.row,self.a.col),self.board['fear'],self.b.frame)
                self.nightmare_hit(enemy,'nymph_s2_primary')
            if self.closed:
                break
            self.blasts.append((self.b.frame+self.blast_delay,dict(row=row,col=col)))
        self.nightmares=[] if self.closed else retained

    def objects(self):
        f=self.b.frame
        if self.commit==f:
            self.gauge.state.points-=self.gauge.state.cost
            self.commit=None
        if self.restore==f:
            self.mode=self.restore=self.pending=None
            self.poll=f if self.slot==2 else f+1
        if not self.closed:
            rate=self.b.sp_rate(self.a)
            if self.y and any(s.burst and key in self.b.live and circle_in_range(self.b.live[key].row,
                    self.b.live[key].col,self.b.live[key].radius,self.cells[0],box_query=True) for key,s in self.b.logos.receivers.items()):
                rate+=_trait_values(self.card)["sp_recovery_per_sec"]
            set_period(self.gauge.state,_divide(ONE,_raw_f32(rate)),stopped=f<self.unblock)
            if f>=self.unblock:
                self.gauge.state.advance(self.gauge.step)
        for key,soul in tuple(self.souls.items()):
            e=self.b.live.get(key)
            if e is None:
                del self.souls[key]
            elif soul.active or soul.listeners:
                s=self.b.logos.receiver(e)
                s.hp=e.hp
                soul.tick(f,s,self.atk(),self.factor)
                self.b.logos._settle(e,s)
        if not self.closed and f==self.poll:
            active=self.active()
            rows=self.b.targets(self.a,self.cells[active])[:int(self.board["attack@max_target"]) if active else 1]
            if not rows:
                self.poll+=3
            else:
                target=rows[0]
                dx,dy=target.col-self.a.col,target.row-self.a.row
                if dx:
                    self.sign=1 if dx>0 else -1
                elif abs(dy)>.2:
                    self.face="Back" if dy>0 else "Front"
                p=self.card["panel_attributes"]
                bonus=self.talent.get("attack_speed",0) if self.stacks>=self.talent.get("stack_cnt_check",float("inf")) else 0
                extra=bonus+(self.board.get("attack_speed",0) if active else 0)
                iv=p["baseAttackTime"]*100/max(10,(p["attackSpeed"]+extra)*self.b.attack_speed(self.a)/p["attackSpeed"])
                duty=self.skill if active else self.normal
                shot=ranged_shot_release(self.b.tables,self.ev,duty,attack_frame=f,attack_interval=iv,
                    facing=self.face,mode_path="Modes/S3" if active else None)
                self.pending=(shot,rows,active,duty)
                self.poll=f+fixed_wait_frames(iv/self.b.dt)
        remain=[]
        for e,pos,offset,speed,skill,atk in self.shots:
            if e.alive and not e.cursor.hidden:
                m=ordinary_trace_step(pos,(e.col+offset[0],e.row+offset[1],offset[2]),effective_dt=self.b.dt,round_position=True,speed=speed)
                if m.reached:
                    self.hit(e,skill,atk)
                else:
                    remain.append((e,m.position,offset,speed,skill,atk))
        self.shots=remain
        if self.nightmares:
            self.move_nightmares()

    def coroutines(self):
        if self.release_s2 and self.release_s2[0]==self.b.frame:
            _,target,row,clock,face,sign=self.release_s2
            self.release_s2=None
            fixed=not target.alive or target.cursor.hidden
            if not fixed or self.skill['_emitToInputPosWhenTargetIsInvalid']:
                row=dict(row,receiver_mount_rule=lambda rule: self.b.log('rule_applied',rule=rule,target=target.id))
                origin,mount=stationary_cast_mounts(self.b.tables,self.a.uid,clock,self.a.place,row,face,caster_face_sign=sign)
                self.nightmares.append(dict(target=target,position=origin,offset=(mount[0]-row['col'],mount[1]-row['row'],mount[2]),
                                            last=(row['row'],row['col']),fixed=fixed))
        for blast in tuple(self.blasts):
            if self.closed or blast[0]!=self.b.frame:
                continue
            self.blasts.remove(blast)
            for target in tuple(self.b.live.values()):
                if self.closed:
                    break
                if not target.cursor.hidden and ignition_circle_overlap(blast[1],dict(row=target.row,col=target.col),self.board['projectile_range'],target.radius):
                    self.nightmare_hit(target,'nymph_s2_splash')
        if not self.pending or self.pending[0]["release_frame"]!=self.b.frame:
            return
        shot,rows,active,duty=self.pending
        self.pending=None
        for e in rows:
            if not e.alive:
                continue
            def note(rule):
                if (e.id,rule) not in self.notes:
                    self.b.log("rule_applied",rule=rule,target=e.id)
                    self.notes.add((e.id,rule))
            row=dict(prefab=e.prefab,row=e.row,col=e.col,tile_z=0,airborne=e.airborne,receiver_mount_rule=note)
            origin,mount=stationary_cast_mounts(self.b.tables,self.a.uid,shot["clock"],self.a.place,row,self.face,caster_face_sign=self.sign)
            self.shots.append((e,origin,(mount[0]-e.col,mount[1]-e.row,mount[2]),self.speeds[duty["_projectileKey"]],active,self.atk()))

    def press(self):
        f=self.b.frame
        selected=self.b.targets(self.a,self.cells[0])[:1] if self.slot==2 else []
        if selected:
            e=selected[0]
            if e.col!=self.a.col:
                self.sign=1 if e.col>self.a.col else -1
            elif abs(e.row-self.a.row)>.2:
                self.face="Back" if e.row>self.a.row else "Front"
        state="skill" if self.mode is not None else "born" if f<self.born else "attack" if self.pending else "idle"
        cast=resolve_cast_start(self.b.tables,self.ev,frame=f,phase="after_tick",facing=self.face,
            direction=self.a.direction,owner_state=state,sp=self.gauge.state.points,triggerable=self.slot==3 or bool(selected))
        if cast["cast_frame"] is None:
            self.b.log("skill_rejected",operator_id=self.a.uid,reason=cast["status"])
            return
        at=cast["cast_frame"]
        self.b.skill_started(self.a,at)
        interval=self.card['panel_attributes']['baseAttackTime']*100/self.b.attack_speed(self.a) if self.slot==2 else None
        native=run_committed_cast(self.b.tables,self.ev,horizon=0,facing=self.face,direction=self.a.direction,
                                  effective_attack_interval=interval,watched_buff_survives=True)["natural_end"]
        if at==f:
            self.gauge.state.points=cast["sp_after"]
        else:
            self.commit=at
        self.unblock=at+native["first_recovery_tick"]
        self.mode=cast["spell_frame"]
        self.restore=at+native["end"]+1
        self.pending=None
        if self.slot==2:
            row=dict(prefab=e.prefab,row=e.row,col=e.col,tile_z=0,airborne=e.airborne)
            self.release_s2=(self.mode,e,row,cast['ability_clock'],self.face,self.sign)
            self.poll=self.restore
            self.b.log('nymph_s2_lock',operator_id=self.a.uid,target=e.id)
        else:
            self.poll=self.mode+1
        self.b.log("skill_pressed",operator_id=self.a.uid,skill=self.slot,mode_frame=self.mode,expire_frame=self.restore)

    def close(self):
        self.closed=True
        self.pending=self.mode=self.restore=self.commit=None
        self.release_s2=None
        self.blasts.clear()
        self.nightmares.clear()
        for s in self.souls.values():
            s.withdraw()
