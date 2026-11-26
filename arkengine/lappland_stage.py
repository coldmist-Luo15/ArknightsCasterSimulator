"""Native wave entry for Lappland's ordinary, remote and roaming funnels."""
from .caster_ranges import skill_range_cells
from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .battle_random import BattleRandom
from .caster_cast import run_committed_cast
from .caster_drone_chain_mystic import LAPPLAND, _drone, _lappland_weighted_target
from .caster_prefabs import caster_prefab_evidence
from .caster_special_start import resolve_special_start
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_casters import _board
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .enemy_silence import ReceiverSilence
from .fear_navigation import StageFearRoutes
from .fixed import ONE, mul
from .marcil_deploy import _birth
from .module_effects import trait_board
from .mystic_timing import fixed_wait_frames
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .stage_deployment import RangedReentries
from .skill_recovery import _divide, deploy_skill
from .stage_channel import UnsupportedChannelScene, object_position_frame
from .target_priority import native_box_candidates
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32
from .whitw_wolves import run_wolf_sortie, wolf_release_frame


class LapplandAttacks:
    def __init__(self,tables,card,evidence,scene,world,events,routes):
        self.tables,self.card,self.ev,self.scene,self.world=tables,card,evidence,scene,world
        self.events,self.routes=events,routes
        self.slot=card["skill"]
        self.place,self.view=scene["placement"],scene["view"]
        self.start=self.place["frame"]
        self.dt=tables.fixed_timestep()
        self.step=_raw_f32(self.dt)
        self.stats=card["panel_attributes"]
        self.talents=[_board(card,i,str(i+1)) for i in (0,1)]
        self.trait=trait_board(tables.read("excel/character_table.json")[LAPPLAND],card["module_phase"],card)
        self.board={r["key"]:r["value"] for r in evidence["skill_blackboard"]}
        self.atk=_raw_f32(self.stats["atk"])
        self.module_speed=_board(card,-1,"10").get("attack_speed",0)
        for eq in evidence["equipment"].values():
            if any(b["templateKey"]=="whitw2_e[attack_speed_up_after_skill]" for p in eq["components"]
                   for b in p["fields"].get("_buffs",[])):
                self.module_speed=max(self.module_speed,_board(card,1,"10").get("attack_speed",0))
        self.module_started=False
        self.start_attack=self.start+_birth(tables,card,evidence,self.view)["born_exit_update"]+1
        self.sign=-1 if self.place["direction"]=="LEFT" else 1
        self.attack=None
        self.flights=[]
        self.normal_funnel=[]
        self.normal_counts={}
        self.radii={k:tables.enemy_circle(s["prefab"]) for k,s in world.stats.items()}
        self.silence={k:ReceiverSilence() for k in world.stats}
        self.hp={k:_raw_f32(s["hp"]) for k,s in world.stats.items()}
        self.cells=range_cells(card["base_range"],self.place,scene["rows"],scene["cols"])
        self.skill_cells=range_cells(card["skill_range"] or card["base_range"],self.place,scene["rows"],scene["cols"])
        self.drone_cells=(skill_range_cells(tables,card,dict(placement=self.place,rows=scene["rows"],cols=scene["cols"]))["cells"]
                          if self.slot==1 else self.skill_cells)
        self.parts={r["id"]:r for r in evidence["character"]["components"] if r["active"] and r["enabled"]}
        self.normal,self.remote=self.abilities()

    def abilities(self):
        normal,=(r["fields"] for r in self.parts.values() if r["script"]=="MultiFunnelsNormalAttack"
                 and r["path"].startswith("Modes/Default/"))
        scripted="RangedAttack" if self.slot==3 else "MultiFunnelRemoteAttack"
        remote,=(r["fields"] for r in self.parts.values() if r["script"]==scripted
                 and r["path"]==(f"Modes/S{self.slot}/Body/Attack" if self.slot==3 else
                                  f"Modes/S{self.slot}/Body/M{self.slot}FunnelRemoteAttack"))
        return normal,remote

    def stage(self,f):
        return min(3,int(((f-self.start)*self.step+_divide(ONE,100000*ONE))//_raw_f32(self.talents[0]["interval"])))

    def interval(self):
        speed=self.stats["attackSpeed"]+self.talents[1].get("attack_speed",0)
        if self.module_started:
            speed+=self.module_speed
        return _divide(_raw_f32(self.stats["baseAttackTime"]),_divide(_raw_f32(speed),100*ONE))

    def reset_attack(self,f):
        self.attack=None
        self.start_attack=f+1

    def alive(self,k):
        return self.hp[k]>HP_EPSILON

    def apply_silence(self,frame,key,seconds):
        if seconds and self.alive(key) and not self.world.stats[key]["silence_immune"]:
            expires=self.silence[key].attach(self.tables,frame,seconds)
            self.events.append(dict(kind="silence_applied",frame=frame,target=key,expires=list(expires)))

    def damage(self,frame,key,scale,kind,active):
        attack=mul(self.atk,ONE+(_raw_f32(self.board["atk"]) if active else 0))
        packet=ordinary_arts_hit(attack_raw=mul(attack,scale),resistance_raw=_raw_f32(self.world.stats[key]["res"]),
                                 hp_raw=self.hp[key])
        self.hp[key]=packet.hp_after_raw
        self.events.append(dict(kind=kind,frame=frame,target=key,damage_q32=packet.damage_raw,hp_q32=self.hp[key]))
        if self.stage(frame)>=2:
            self.apply_silence(frame,key,self.talents[0]["attack@silence_duration"])

    def pool(self,frame,cells):
        return [r for r in self.world.snapshot(frame) if self.alive(r["id"]) and
                circle_in_range(r["row"],r["col"],self.radii[r["id"]],cells,box_query=True)]

    def receive(self,frame):
        was_present=self.world.present
        if frame in self.withdrawals:
            self.world.remove_owner(frame,self.events,"withdraw")
        self.world.receive(frame,self.hp,self.events)
        if self.reentries and was_present and not self.world.present:
            self.reentries.leave(frame,LAPPLAND,manual=frame in self.withdrawals)

    def tick(self,frame,active):
        if not self.world.present:
            self.attack=None
            self.start_attack=None

        world=self.world
        if frame==self.start_attack:
            choice=self.routes.select(self.pool(frame,self.skill_cells if active else self.cells),frame)
            if choice is None:
                self.start_attack+=3
            else:
                if choice["col"]!=self.place["col"]:
                    self.sign=1 if choice["col"]>self.place["col"] else -1
                ability=self.remote if active else self.normal
                interval=self.interval()/ONE
                shot=ranged_shot_release(self.tables,self.ev,ability,attack_frame=frame,attack_interval=interval,
                                         facing=self.view,mode_path=f"Modes/S{self.slot}" if active else "Modes/Default")
                self.attack=shot,choice,active
                self.start_attack+=fixed_wait_frames(single(interval/self.dt))
                self.events.append(dict(kind="owner_attack_start",frame=frame,target=choice["id"],skill=active,
                                        release_frame=shot["release_frame"]))
        for shot in self.flights[:]:
            key=shot["target"]
            row=world.by_id(key,frame,projectile_born=shot["born"]) if self.alive(key) else None
            if row is None:
                self.flights.remove(shot)
                continue
            x,y,z=shot["offset"]
            motion=ordinary_trace_step(shot["position"],(row["col"]+x,row["row"]+y,z),effective_dt=self.dt,round_position=True)
            shot["position"]=motion.position
            if motion.reached:
                self.damage(frame,key,ONE,"owner_projectile_hit",active)
                self.flights.remove(shot)
        for shot in self.normal_funnel[:]:
            if frame!=shot["due"]:
                continue
            self.normal_funnel.remove(shot)
            key=shot["target"]
            if self.alive(key) and world.by_id(key,frame,projectile_born=shot["born"]):
                cap=self.trait["max_atk_scale"]*(self.talents[0]["scale"] if self.stage(frame)>=1 else 1)
                scale=min(cap,self.trait["init_atk_scale"]+shot["prior"]*self.trait["delta_atk_scale"])
                self.damage(frame,key,_raw_f32(scale),"ordinary_funnel_hit",active)
        if not self.attack or frame!=self.attack[0]["release_frame"]:
            return
        shot,chosen,was_active=self.attack
        self.attack=None
        key=chosen["id"]
        row=world.receiver_at(key,frame) if self.alive(key) else None
        if row is None:
            return
        origin,mount=stationary_cast_mounts(self.tables,LAPPLAND,shot["clock"],self.place,row,self.view,
                                           caster_face_sign=self.sign)
        self.flights.append(dict(born=frame,target=key,position=origin,
                                 offset=(mount[0]-row["col"],mount[1]-row["row"],mount[2])))
        self.events.append(dict(kind="owner_projectile_release",frame=frame,target=key,skill=was_active))
        if was_active:
            return
        count=1+int(self.slot==1)+int(self.stage(frame)==3)
        for index,ref in enumerate(self.normal["_funnelActions"][:count]):
            if any(s["index"]==index for s in self.normal_funnel):
                continue
            ability=self.parts[ref["m_PathID"]]["fields"]
            projectile=self.ev["projectiles"][ability["_projectileKey"]]
            variant=projectile.get("logic") or next(iter(projectile.values()))
            move,=(r["fields"] for r in variant["components"] if r["script"]=="AttachToTarget")
            if not move["_delayAfterReached"] or not move["_updateDelayTimeOnlyOnce"]:
                raise ValueError("荒芜拉普兰德普通浮游弹的延时配置变化")
            previous,prior=self.normal_counts.get(index,(None,0))
            prior=prior if previous==key else 0
            self.normal_counts[index]=(key,prior+1)
            self.normal_funnel.append(dict(born=frame,due=frame+fixed_wait_frames(single(move["_delayTime"]/self.dt)),
                                           target=key,prior=prior,index=index))
            self.events.append(dict(kind="ordinary_funnel_release",frame=frame,target=key,index=index))


def run_lappland_stage(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    slot=request["squad"][0]["skill"]
    presses={b["frame"] for b in request["actions"] if b.get("kind")=="skill"}
    withdrawals={b["frame"] for b in request["actions"] if b.get("kind")=="withdraw"}
    end=request["end_frame"]
    card,stage,placed,scene,world=prepare_continuous_ranged(tables,request,LAPPLAND,releases)
    ev=caster_prefab_evidence(tables,LAPPLAND,slot,skill_level=card["skill_level"],
                              module_id=card["module"],module_level=card["module_level"])
    rng=tables.combat_random(request.get("battle_random_seed",0))
    map_rng=BattleRandom(request.get("map_random_seed",0))
    routes=StageFearRoutes(tables,world,rng,map_rng)
    start=placed["frame"]
    events=[dict(kind="deployed",frame=start)]
    owner=LapplandAttacks(tables,card,ev,scene,world,events,routes)
    owner.reentries=RangedReentries.for_request(tables,stage,world,request,start)
    owner.withdrawals=withdrawals
    sp=deploy_skill(tables,card,start)[0].state
    sp.points=min(sp.maximum,sp.points+int(owner.talents[1]["sp"]))
    native=run_committed_cast(tables,ev,horizon=0,facing=owner.view,direction=placed["direction"])
    end_clock=native.get("natural_end") or native.get("toggle_cast")
    births={}
    for b in world.births.values():
        birth=b["frame"]
        births[birth]=births.get(birth,0)+2
    for birth,count in births.items():
        if birth<=start:
            for _ in range(count):
                rng.next_double()
    active=False
    spell=restore=unblock=-1
    wolf_release=-1
    skip_until=-1
    wolf_cast=None
    wolf_status=None
    remote=[]
    if slot!=3:
        remote_keys=[owner.parts[r["m_PathID"]]["fields"]["_projectileKey"] for r in owner.remote["_funnelActions"]]
    frame=start
    for frame in range(start+1,end+1):
        if frame<=skip_until:
            continue
        new_place=owner.reentries.advance(frame,events) if owner.reentries else None
        if new_place:
            placed=new_place
            scene=dict(scene,placement=placed,view="Back" if placed["direction"]=="UP" else "Front")
            prior=owner
            owner=LapplandAttacks(tables,card,ev,scene,world,events,routes)
            owner.reentries,owner.withdrawals=prior.reentries,withdrawals
            owner.hp,owner.silence=prior.hp,prior.silence
            owner.flights=prior.flights
            start=frame
            sp=deploy_skill(tables,card,start)[0].state
            sp.points=min(sp.maximum,sp.points+int(owner.talents[1]["sp"]))
            native=run_committed_cast(tables,ev,horizon=0,facing=owner.view,direction=placed["direction"])
            end_clock=native.get("natural_end") or native.get("toggle_cast")
            active=False
            wolf_release=spell=restore=unblock=-1
            remote.clear()
            events.append(dict(kind="deployed",frame=frame))
        owner.receive(frame)
        if not world.present:
            active=False
            remote.clear()
        routes.advance(frame,owner.alive,events)
        if world.settle_exits(frame,owner.alive) is not None:
            break
        sp.advance(owner.step,stopped=frame<unblock)
        if world.present and frame==spell:
            active=True
            owner.reset_attack(frame)
            events.append(dict(kind="skill_mode",frame=frame))
        if frame==restore:
            active=False
            remote.clear()
            owner.reset_attack(frame)
            events.append(dict(kind="restore_default_mode",frame=frame))
        owner.tick(frame,active)
        if active and slot in (1,2):
            count=1+int(owner.board["attack@cnt"])+int(slot==1)+int(owner.stage(frame)==3)
            while len(remote)<count:
                remote.append(dict(projectile=remote_keys[len(remote)],target=None))
            for drone in remote:
                key=drone["target"]
                if key is None:
                    pool={r["id"]:r for r in owner.pool(frame,owner.drone_cells)}
                    candidates=[]
                    for key in native_box_candidates(world,pool):
                        r=pool[key]
                        tick=object_position_frame(world.births[key],frame,world.positions,world.active,key,
                                                   observer_born=start,observer_phase="INPUT")
                        earlier=world.positions.get(tick-1,{}).get(key)
                        candidates.append(dict(r,moving=earlier is None or earlier!=(r["row"],r["col"])))
                    selection=_lappland_weighted_target(candidates,slot,rng)
                    key=selection["target"] if "target" in selection else selection["candidates"][0]
                    if key is not None:
                        drone.update(target=key,born=frame,first=frame+1,remaining=0,prior_same_target_hits=0,
                                     previous_target=None)
                        events.append(dict(kind="remote_funnel_release",frame=frame,target=key,unit=drone["projectile"]))
                    continue
                target=world.by_id(key,frame,projectile_born=drone["born"]) if owner.alive(key) else None
                if target is not None and slot==1:
                    tick=object_position_frame(world.births[key],frame,world.positions,world.active,key,
                                               observer_born=drone["born"],observer_phase="COROUTINES",same_phase_rule=world.enemy_before_projectile)
                    if world.positions.get(tick-1,{}).get(key)!=(target["row"],target["col"]):
                        target=None
                if target is None or not world.selectable(key,frame):
                    drone["target"]=None
                    events.append(dict(kind="remote_funnel_return",frame=frame,target=key,unit=drone["projectile"]))
                    continue
                drone["remaining"]-=owner.step
                if drone["remaining"]>0:
                    continue
                drone["remaining"]=owner.interval()-owner.step
                packet=_drone(LAPPLAND,slot,"drone_hit",dict(drone,talent_stage=owner.stage(frame),
                    target=dict(target,hp=owner.hp[key]/ONE,character_type=False)),owner.board,owner.stats,owner.trait,
                    owner.talents,stream=rng)
                damage=packet["hit"]["damage_raw"]
                owner.hp[key]=max(0,owner.hp[key]-damage)
                drone.update(previous_target=key,prior_same_target_hits=packet["prior_same_target_hits"])
                events.append(dict(kind="remote_funnel_hit",frame=frame,target=key,unit=drone["projectile"],
                                   damage_q32=damage,hp_q32=owner.hp[key],scale=packet["damage_scale"]))
                owner.apply_silence(frame,key,packet["silence_seconds"])
                if packet["fear_seconds"] and owner.alive(key):
                    routes.apply(frame,key,(placed["row"],placed["col"]),packet["fear_seconds"],
                                 (drone["projectile"],frame),events)
        if world.present and frame in presses:
            if sp.points<sp.cost or (slot!=1 and frame<unblock):
                events.append(dict(kind="skill_rejected",frame=frame,sp=sp.points))
            else:
                options=dict(frame=frame,phase="after_tick",facing=owner.view,direction=placed["direction"],sp=sp.points)
                cast=(resolve_special_start(tables,ev,**options,toggled=active,source="manual") if slot==1 else
                      resolve_cast_start(tables,ev,**options,trigger="MANUAL",owner_state="attack" if owner.attack else "idle"))
                if cast["cast_frame"] is None:
                    events.append(dict(kind="skill_rejected",frame=frame,reason=cast["status"]))
                else:
                    sp.points=cast["sp_after"]
                    owner.module_started=True
                    unblock=cast["cast_frame"]+end_clock["first_recovery_tick"]
                    if slot==1:
                        active=cast["toggled_after"]
                        owner.reset_attack(frame)
                        remote.clear()
                    else:
                        spell=cast["spell_frame"]
                        restore=cast["cast_frame"]+end_clock["end"]
                        owner.attack=None
                        owner.start_attack=spell+1
                        if slot==3:
                            wolf_cast=cast
                            wolf_release=wolf_release_frame(ev,spell,owner.step)
                    events.extend(cast["events"])
        for _ in range(births.get(frame,0)):
            rng.next_double()
        if world.present and frame==wolf_release:
            targets=[dict(row,spawn=world.births[key]["frame"],radius=owner.radii[key],character_type=False,
                          row=world.births[key]["route_start"]["row"],col=world.births[key]["route_start"]["col"])
                     for key,row in world.stats.items()]
            wolf_scene=dict(scene,placement=owner.place,targets=targets,skill_end_frame=restore,birth_draws=births)
            pack=run_wolf_sortie(tables,card,wolf_scene,stream=rng,map_stream=map_rng,stage_owner=owner,stage_cast=wolf_cast)
            wolf_status=pack["status"]
            skip_until=pack["through_frame"]-int(wolf_status=="skill_ended")
            if world.defeated_at is not None or wolf_status=="stage_victory_projected":
                frame=pack["through_frame"]
                break
        if world.settled(frame,owner.hp,events):
            break
    through=world.defeated_at or frame
    status="stage_defeat" if world.defeated_at else "stage_victory_projected" if world.victory is not None and through>=world.victory else "horizon"
    result=dict(status=status,stop=status,through_frame=through,stage=stage["code"],operator_id=LAPPLAND,
                skill=slot,deployment=placed,source=tables.source,events=sorted([e for e in events if e["frame"]<=through],
                key=lambda e:e["frame"]),sp=sp.points,hp_q32=owner.hp,native_draws=rng.draws,map_draws=map_rng.draws)
    return world.stage_result(result,fixed_timestep=owner.dt,target_field="target",hp_field="hp_q32",projection_horizon=end)
