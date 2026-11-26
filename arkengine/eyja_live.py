"""Eyjafjalla's attacks, deployments and projectiles on the ordinary battle clock."""
from collections import Counter,defaultdict

from .animation_assets import read_skin_actions
from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_model import _assets
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .continuous_ranged import prepare_continuous_ranged
from .core_caster_skills import eyja_deployment,volcano_selection
from .damage import HP_EPSILON
from .elemental_projectiles import ranged_shot_release,stationary_cast_mounts
from .enemy_elements import EnemyElements
from .eyja_aura import EYJA,FlameBreath
from .eyja_ignition import _ability,ignition_circle_overlap
from .fixed import ONE,mul
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import deploy_skill
from .stage_deployment import DeploymentField
from .target_priority import native_box_candidates
from .trace_motion import ordinary_trace_step,paracurve_horizontal_step
from .volley_damage import _raw_f32


def run_live_eyja(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


class EyjaAttacks:
    def __init__(self,tables,card,event,world,stream,states,log,*,resistance_scale=None):
        self.t,self.card,self.world,self.stream,self.states,self.log=tables,card,world,stream,states,log
        self.resistance_scale=resistance_scale
        self.ev=_assets(tables,card)
        self.slot=card["skill"]
        self.bb={r["key"]:r["value"] for r in self.ev["skill_blackboard"]}
        self.normal=default_attack(tables,EYJA)["fields"]
        self.dt=tables.fixed_timestep()
        self.step=int(self.dt*ONE)
        self.rows,self.cols=event["rows"],event["cols"]
        self.trait=_trait_values(card)
        self.flame=FlameBreath.from_card(tables,card)
        self.pen=_raw_f32(self.trait.get("magic_resist_penetrate_fixed",0))
        self.debuff={}
        self.arrivals={}
        self.shots=[]
        self.gauge=self.place=None
        self.spell=self.expiry=self.poll=self.release=self.windup=None
        self.generation=0
        self.recovery=0
        self.locked=[]
        self.pending_more=False
        self.ignition=_ability(tables,card,self.ev) if self.slot==2 else None
        self.base_projectile=self.normal["_projectileKey"]
        self.normal_speed=self.speed(self.base_projectile,"AdvancedMovement")
        if self.slot==3:
            self.lava=default_attack(tables,EYJA,1)["fields"]["_projectileKey"]
            self.lava_speed=self.speed(self.lava,"ParacurveMovement")
            self.channel=next(c["fields"]["_default"] for c in self.ev["skill"]["components"] if c["script"]=="ThreePartChannelingAnimation")
            self.channel_skin=read_skin_actions(tables,"chararts/"+EYJA+".ab","defaultskin",list(self.channel.values()),
                                               [(2,"muzzlaTransform")],allow_unbound=True)

    def speed(self,key,mover):
        return next(c["fields"]["_speed"] for c in self.ev["projectiles"][key]["logic"]["components"] if c["script"]==mover)

    def enter(self,frame,placement):
        self.generation+=1
        self.world.deployment_frame=frame
        p=self.place=dict(placement)
        tile=self.world.tiles[p["row"],p["col"]]
        p["tile_z"]=tile["position"]["z"]-tile["locate_height_offset"]
        self.view="Back" if p["direction"]=="UP" else "Front"
        self.face=self.view
        self.side={"RIGHT":1,"LEFT":-1}.get(p["direction"])
        if self.side is None:
            skin=read_skin_actions(self.t,"chararts/"+EYJA+".ab","defaultskin",["Attack"])
            self.side=skin["default_horizontal_face_sign"]
        self.cells=range_cells(self.card["base_range"],p,self.rows,self.cols)
        self.shape=range_cells(self.card["skill_range"] or self.card["base_range"],p,self.rows,self.cols)
        has_elite=any(e["level"] in ("ELITE","BOSS") for e in self.candidates(frame,self.cells))
        talent=eyja_deployment(self.t,self.card,dict(elite_or_boss_in_range=has_elite),self.stream)
        skill,_=deploy_skill(self.t,self.card,frame)
        self.gauge=g=skill.state
        g.points=min(g.maximum,g.points+talent["sp"])
        self.panel=self.card["panel_attributes"]
        self.interval=single(single(self.panel["baseAttackTime"])*100/single(self.panel["attackSpeed"]+talent["attack_speed"]))
        self.period=fixed_wait_frames(single(self.interval/self.dt))
        self.lava_period=fixed_wait_frames(single((self.panel["baseAttackTime"]+self.bb.get("base_attack_time",0))*100/
                                                  (self.panel["attackSpeed"]+talent["attack_speed"])/self.dt))
        self.base_atk=_raw_f32(self.panel["atk"])
        self.bonus=_raw_f32(talent["caster_attack_bonus"])
        self.born=frame+_birth(self.t,self.card,self.ev,self.view)["born_exit_update"]
        self.poll=self.born+1
        self.recovery=frame+1
        if self.slot==2:
            self.cast=run_committed_cast(self.t,self.ev,horizon=self.period+2,facing=self.view,direction=p["direction"],
                                         effective_attack_interval=self.interval)
        self.log.append(dict(kind="deployment",frame=frame,sp=g.points,talent=talent,generation=self.generation))

    def leave(self):
        self.place=self.poll=self.windup=self.spell=self.expiry=self.release=None
        self.pending_more=False
        self.locked=[]

    def candidates(self,frame,cells,completed=False):
        rows=self.world.snapshot(frame,projectile_born=frame if completed else None)
        return [r for r in rows if self.states[r["id"]].hp>HP_EPSILON and
                circle_in_range(r["row"],r["col"],self.world.wave.radii[r["id"]],cells,box_query=True)]

    def attack(self,frame):
        active=self.spell is not None and self.spell<frame<=self.expiry
        return mul(self.base_atk,ONE+self.bonus+(_raw_f32(self.bb["atk"]) if active else 0))

    def press(self,frame,action):
        if self.slot!=3 or self.place is None:
            return
        active=self.expiry is not None and frame<=self.expiry
        cast=resolve_cast_start(self.t,self.ev,frame=frame,phase=action.get("phase","after_tick"),
            sp=self.gauge.points,owner_state="skill" if active else "born" if frame<self.born else "idle")
        if cast["spell_frame"] is None:
            self.log.append(dict(kind="skill_input_rejected",frame=frame,reason=cast["status"],sp=self.gauge.points))
            return
        self.gauge.points=cast["sp_after"]
        self.spell=cast["spell_frame"]
        self.expiry=frame+fixed_wait_frames(self.card["skill_row"]["duration"]/self.dt)
        self.recovery=self.expiry+2
        self.poll=self.spell+1
        self.log.append(dict(kind="volcano_cast",frame=frame,sp=self.gauge.points))
        if self.windup:
            self.log.append(dict(kind="ordinary_windup_cancelled",frame=frame,target=self.windup["target"]["id"],
                                 scheduled_release=self.windup["release"]))
        self.windup=None
        self.pending_more=False

    def select_volcano(self,frame,completed):
        ids=native_box_candidates(self.world,(r["id"] for r in self.candidates(frame,self.shape,completed)))
        if not ids:
            self.poll=frame+3
            self.pending_more=False
            return
        before=self.stream.draws
        pick=volcano_selection(ids,int(self.bb["attack@max_target"]),self.stream)
        self.locked=pick["targets"]
        self.release=frame+self.lava_period
        self.poll=self.release+1
        self.pending_more=True
        self.log.append(dict(kind="volcano_attack",frame=frame,target_set=list(self.locked),trigger_target=pick["trigger_target"],
            cast_selection=pick["cast_selection"],eligible_in_uid_order=ids,stream_draws=[before,self.stream.draws],
            interval_ticks=self.lava_period,rng_draws=self.stream.draws))

    def start_normal(self,frame,completed=False):
        rows=self.candidates(frame,self.cells,completed)
        if not rows:
            self.poll=frame+3
            return
        enemy=self.world.select(rows,frame,frame_by_target={r["id"]:frame for r in rows} if completed else None)
        dx,dy=enemy["col"]-self.place["col"],enemy["row"]-self.place["row"]
        if dx:
            self.side=1 if dx>0 else -1
            if abs(dx)>.2 or abs(dx)>abs(dy):
                self.face=self.view
        elif abs(dy)>.2:
            self.face="Back" if dy>0 else "Front"
        enhanced=self.slot==2 and self.gauge.points>=self.gauge.cost
        if self.slot==2:
            sample=self.cast["replacement"]
            clock=dict(action=self.ignition["action"],first_key_seconds=sample["attack_key_seconds"],playback_speed=sample["playback_speed"])
            release=frame+sample["release"]
        else:
            sample=ranged_shot_release(self.t,self.ev,self.normal,attack_frame=frame,attack_interval=self.interval,facing=self.face)
            clock,release=sample["clock"],sample["release_frame"]
        if enhanced:
            self.gauge.points-=self.gauge.cost
            self.recovery=frame+self.cast["first_unblocked_recovery_tick"]
        self.windup=dict(release=release,clock=clock,target=enemy,enhanced=enhanced,face=self.face,sign=self.side)
        self.poll=frame+self.period
        self.log.append(dict(kind="replacement" if enhanced else "ordinary" if self.slot==2 else "ordinary_attack",
                            frame=frame,target=enemy["id"],sp=self.gauge.points,release_frame=release,
                            first_unblocked_recovery=self.recovery if enhanced else None))

    def launch(self,frame,key,pos,mount,enemy,kind,speed):
        self.shots.append(dict(target=key,position=pos,goal=mount,offset=(mount[0]-enemy["col"],mount[1]-enemy["row"],mount[2]-enemy["tile_z"]),
            speed=speed,kind=kind,born=frame,generation=self.generation,attack=self.attack(frame)))

    def normal_release(self,frame):
        shot,self.windup=self.windup,None
        key=shot["target"]["id"]
        enemy=self.world.by_id(key,frame,projectile_born=frame)
        if enemy is None or self.states[key].hp<=HP_EPSILON:
            gain=0
            if shot["enhanced"]:
                gain=self.gauge.cost
                self.gauge.points=min(self.gauge.maximum,self.gauge.points+gain)
                self.recovery=frame+1
            self.log.append(dict(kind="cast_target_dead",frame=frame,target=key,refunded_sp=gain,sp=self.gauge.points))
            self.start_normal(frame,True)
            return
        pos,mount=stationary_cast_mounts(self.t,EYJA,shot["clock"],self.place,enemy,shot["face"],caster_face_sign=shot["sign"])
        enhanced=shot["enhanced"]
        self.launch(frame,key,pos,mount,enemy,"ignition" if enhanced else "ordinary_hit",
                    self.ignition["speed"] if enhanced else self.normal_speed)
        self.log.append(dict(kind="projectile_release",frame=frame,target=key,muzzle=pos,
                             projectile=self.ignition["special"] if enhanced else self.base_projectile))

    def lava_release(self,frame):
        alive=[e for key in self.locked if self.states[key].hp>HP_EPSILON and (e:=self.world.by_id(key,frame,projectile_born=frame))]
        self.release=None
        if not alive:
            self.log.append(dict(kind="volcano_spell_no_target",frame=frame,target_set=list(self.locked)))
            self.select_volcano(frame,True)
            return
        view=self.view
        clips=self.channel_skin["views"][view.lower()]["actions"]
        if clips[self.channel["loopAnim"]].get("status"):
            view="Front" if view=="Back" else "Back"
            clips=self.channel_skin["views"][view.lower()]["actions"]
        age=(frame-self.spell)*self.dt
        remaining=(self.expiry-frame)*self.dt
        begin,loop,end=(self.channel[k] for k in ("beginAnim","loopAnim","endAnim"))
        if age<clips[begin]["duration"]:
            action,seconds=begin,age
        elif remaining<=clips[end]["duration"]:
            action,seconds=end,max(0,clips[end]["duration"]-remaining)
        else:
            action,seconds=loop,(age-clips[begin]["duration"])%clips[loop]["duration"]
        for e in alive:
            pos,mount=stationary_cast_mounts(self.t,EYJA,dict(action=action),self.place,e,view,sample_seconds=seconds,caster_face_sign=self.side,skin_data=self.channel_skin)
            self.launch(frame,e["id"],pos[:2],mount,e,"volcano_hit",self.lava_speed)
        self.log.append(dict(kind="volcano_projectiles",frame=frame,projectile=self.lava,target_set=[e["id"] for e in alive]))

    def impact(self,frame,key,kind,atk,scale=1):
        state=self.states[key]
        if state.hp<=HP_EPSILON:
            return
        res=_raw_f32(self.world.stats[key]["res"])+state.res_add
        if frame<self.debuff.get(key,-1):
            res=mul(res,ONE+_raw_f32(self.ignition["debuff_ratio"]))
        if self.resistance_scale:
            res=mul(res,self.resistance_scale(key,frame))
        hit=state.receive_ordinary(frame,source=EYJA,damage_type="arts",attack_q32=mul(atk,_raw_f32(scale)),mitigation_q32=res-self.pen)
        if self.place and kind!="stop_hit" and frame>=self.recovery and self.world.stats[key]["level"] in ("ELITE","BOSS"):
            self.gauge.points=min(self.gauge.maximum,self.gauge.points+int(self.trait.get("sp",0)))
        self.log.append(dict(kind=kind,frame=frame,target=key,receiver=key,damage_q32=hit["damage_q32"],hp_q32=state.hp,sp=self.gauge.points))

    def advance_shots(self,frame):
        for s in tuple(self.shots):
            key,kind=s["target"],s["kind"]
            e=self.world.by_id(key,frame,projectile_born=s["born"]) if self.states[key].hp>HP_EPSILON else None
            if e:
                x,y,z=s["offset"]
                s["goal"]=e["col"]+x,e["row"]+y,e["tile_z"]+z
            elif kind!="ignition":
                self.shots.remove(s)
                continue
            elif not s.get("lost"):
                s["lost"]=True
                self.log.append(dict(kind="ignition_trace_lost",frame=frame,target=key))
            if kind=="ignition" and (frame-s["born"])*self.dt>self.ignition["lifetime"]:
                self.shots.remove(s)
                continue
            if kind=="volcano_hit":
                s["position"],hit=paracurve_horizontal_step(s["position"],s["goal"],s["speed"],self.dt,True)
            else:
                moved=ordinary_trace_step(s["position"],s["goal"],effective_dt=self.dt,speed=s["speed"],round_position=True)
                s["position"],hit=moved.position,moved.reached
            if not hit:
                continue
            self.shots.remove(s)
            atk=self.attack(frame) if self.place and self.generation==s["generation"] else s["attack"]
            if kind=="volcano_hit":
                self.arrivals[key]=(frame,frame)
            if kind!="ignition":
                self.impact(frame,key,kind,atk)
                continue
            center=e if e else dict(col=s["position"][0],row=s["position"][1])
            if e:
                duration=_raw_f32(self.ignition["duration"])
                self.debuff[key]=frame+(duration+self.step-1)//self.step
            for other in self.world.snapshot(frame,projectile_born=s["born"]):
                uid=other["id"]
                if (uid==key and e) or ignition_circle_overlap(center,other,self.ignition["radius"],self.world.wave.radii[uid]):
                    self.impact(frame,uid,"explosion",atk,self.ignition["area_scale"])
            if e:
                self.impact(frame,key,"stop_hit",atk,self.ignition["extra_scale"])

    def tick(self,frame):
        if self.place:
            if frame>=self.recovery and self.gauge.advance(self.step) and self.slot==2:
                self.log.append(dict(kind="natural_sp",frame=frame,sp=self.gauge.points))
            if frame==self.spell:
                self.log.append(dict(kind="volcano_mode",frame=frame))
            if self.expiry is not None and frame==self.expiry+1:
                self.log.append(dict(kind="volcano_mode_ended",frame=self.expiry))
                self.spell=self.expiry=self.release=None
                self.pending_more=False
                self.poll=frame
            if frame==self.poll:
                if self.spell is None:
                    self.start_normal(frame)
                elif frame<self.expiry and not self.pending_more:
                    self.select_volcano(frame,False)
        self.advance_shots(frame)
        if self.windup and self.windup["release"]==frame:
            self.normal_release(frame)
        if self.expiry is not None and frame<self.expiry:
            if frame==self.poll and self.pending_more:
                self.select_volcano(frame,True)
            if frame==self.release:
                self.lava_release(frame)


def _simulate(tables,request,releases):
    deck_only=not any(a.get("kind","deploy")=="deploy" and a["operator_id"]==EYJA for a in request["actions"])
    card,stage,_,event,world=prepare_continuous_ranged(tables,request,EYJA,releases,native_walk=False,deck_only=deck_only)
    field=DeploymentField(tables,stage,world.scene)
    stream=tables.combat_random(request.get("battle_random_seed",request.get("eyja_deployment_seed",0)))
    births=Counter(b["frame"] for b in world.births.values())
    shared_births="battle_random_seed" in request or "eyja_deployment_seed" not in request
    states={k:EnemyElements.from_scene_enemy(tables,dict(row,hp_q32=_raw_f32(row["hp"]))) for k,row in world.stats.items()}
    actions=defaultdict(list)
    for action in request["actions"]:
        actions[action["frame"]].append(action)
    log=[]
    actor=EyjaAttacks(tables,card,event,world,stream,states,log)
    units=world.unit_templates
    present={}
    world.present=False
    if world.incoming:
        world.incoming.units.clear()
    world.hp=0
    placements=[]
    def leave(uid,frame):
        place,_=present.pop(uid)
        field.occupied.discard((place["row"],place["col"]))
        field.free_slots+=field.initial["cards"][uid]["slots"]
        cd=_raw_f32(world.cards[uid]["panel_attributes"]["respawnTime"])
        field.available_after[uid]=frame+(cd+actor.step-1)//actor.step
        if uid==EYJA:
            actor.leave()

    def allied_attack(hit,key,attack,res):
        profession=hit.get("profession")
        if profession is None and (uid:=hit.get("operator_id")):
            profession=tables.read("excel/character_table.json").get(uid,{}).get("profession")
        atk=mul(attack,ONE+actor.flame.bonus(profession,owner_on_field=bool(actor.place)))
        if frame<actor.debuff.get(key,-1):
            res=mul(res,ONE+_raw_f32(actor.ignition["debuff_ratio"]))
        return atk,res

    end=request["end_frame"]
    stop="horizon"
    for frame in range(end+1):
        field.before_objects(frame)
        for row in actions[frame]:
            if row.get("kind","deploy")!="deploy":
                continue
            uid=row["operator_id"]
            price=field.deployment_cost(uid)
            reason="already_deployed" if uid in present else field.place_operator(row)
            if reason:
                log.append(dict(kind="deploy_rejected",frame=frame,operator_id=uid,reason=reason))
                continue
            present[uid]=(row,price)
            placements.append(dict(row,paid=price))
            hp=_raw_f32(world.cards[uid]["panel_attributes"]["maxHp"])
            if world.incoming:
                world.incoming.units[uid]=dict(units[uid],placement=dict(row),hp=hp,order=len(placements))
                world.incoming.move_holder(uid,row["row"],row["col"])
            if uid==EYJA:
                world.present,world.hp=True,hp
                actor.enter(frame,row)
            else:
                log.append(dict(kind="ally_deployed",frame=frame,operator_id=uid))
        if actor.place or world.incoming:
            world.receive(frame,states,log)
        if world.incoming:
            for uid in tuple(present):
                if uid not in world.incoming.units:
                    leave(uid,frame)
        if world.settle_exits(frame,lambda k:states[k].hp>HP_EPSILON) is not None:
            end,stop=world.defeated_at,"stage_defeat"
            break
        for key,state in states.items():
            if state.burst or state.last_tick is not None:
                before=state.hp
                state.tick(frame)
                if before!=state.hp:
                    log.append(dict(kind="element_tick",frame=frame,target=key,hp_q32=state.hp))
        world.allied_impacts(frame,states,log,arts_modifier=allied_attack)
        actor.tick(frame)
        for row in actions[frame]:
            if row.get("kind")=="skill" and row["operator_id"]==EYJA:
                actor.press(frame,row)
            elif row.get("kind")=="withdraw" and (uid:=row["operator_id"]) in present:
                price=present[uid][1]
                rate={"charger":2,"merchant":0}.get(world.cards[uid].get("branch"),1)
                field.dp=min(field.cap,field.dp+min(field.initial["cards"][uid]["cost"],price*rate//2))
                if uid==EYJA:
                    world.remove_owner(frame,log,"withdraw")
                else:
                    if world.incoming:
                        world.incoming.withdraw(uid)
                    log.append(dict(kind="withdraw",frame=frame,operator_id=uid))
                leave(uid,frame)
        if shared_births:
            stream.advance_spawn_offsets(births[frame])
        if field.order=="input_before_cost":
            field.gain(frame)
        if world.settled(frame,states,log):
            end,stop=frame,"stage_victory_projected"
            break
    result=dict(stage=stage["code"],skill=card["skill"],operator_id=EYJA,module=card["module"],deployment=next((p for p in placements if p["operator_id"]==EYJA),None),
                deployments=placements,events=log,through_frame=end,stop=stop,sp=actor.gauge.points if actor.gauge else None,
                hp_q32={k:v.hp for k,v in states.items()},debuff_end_frames=actor.debuff,arrival_windows=actor.arrivals,
                random_draws=stream.draws,source=tables.source)
    return world.stage_result(result)
