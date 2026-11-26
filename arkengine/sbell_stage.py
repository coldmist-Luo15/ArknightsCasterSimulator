"""Pramanix the Prerita on the ordinary 30 Hz stage clock."""
from collections import defaultdict

from .attack_range import circle_in_range, range_cells
from .caster_cast import run_committed_cast
from .caster_ranges import DIRECTION
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON
from .fixed import ONE, mul
from .forced_motion import enemy_push_profile, push_force, start_push
from .module_effects import trait_board
from .phalanx_mode_scene import _active_mode, _timing
from .phalanx_skills import SNOW, _talent, execute_phalanx_event
from .phalanx_wave import run_with_phalanx_waves
from .sbell_attraction import run_s3_attraction
from .sbell_blessing import run_blessing
from .sbell_live import FrozenSnowGoals, SbellSnowWave
from .sbell_snow import run_s2_snow_outward
from .skill_recovery import RecoveryTimer, deploy_skill
from .stage_channel import prepare_channel
from .stage_receivers import StageReceivers
from .stage_deployment import DeploymentField
from .volley_damage import _raw_f32


class SnowCaster:
    def __init__(self,battle,world,live,goals,random,ground,exits,place):
        self.battle,self.world,self.live,self.goals=battle,world,live,goals
        self.card,self.tables,self.wave=battle.card,battle.tables,world.wave
        self.events=world.events
        self.random,self.ground,self.exits=random,ground,exits
        self.place=place
        self.slot=self.card["skill"]
        self.board={b["key"]:b["value"] for b in self.card["skill_row"]["blackboard"]}
        self.talent=_talent(self.card,0,"1")
        self.maximum=int(self.talent["max_cast_cnt"])
        self.step=_raw_f32(self.tables.fixed_timestep())
        self.blessing=True
        self.frozen_until=0
        self.alive=True
        self.gauge,_=deploy_skill(self.tables,self.card,place["frame"])
        self.natural=RecoveryTimer(_raw_f32(self.talent["interval"]))
        self.rapid=self.forward=None
        self.front=set()
        self.forward_left=0
        self.cast=self.switch=self.expiry=self.next_search=self.impact=None
        self.baked_events={}
        self.active=False
        self.recovery=place["frame"]+1
        self.blocked_until=place["frame"]
        self.cast_args=dict(direction=place["direction"],facing="Back" if place["direction"]=="UP" else "Front")
        self.clock=run_committed_cast(self.tables,battle.evidence,horizon=max(120,int(self.card["skill_row"]["duration"]*30)+30),**self.cast_args)
        grid=battle.scene.level["mapData"]["map"]
        self.layout=dict(rows=len(grid),cols=len(grid[0]),placement=place)
        self.ordinary=set(range_cells(self.card["base_range"],place,len(grid),len(grid[0])))
        self.skill=set(range_cells(self.card["skill_range"] or self.card["base_range"],place,len(grid),len(grid[0])))
        self.base=live.base=self.ordinary & ground
        self.wave.cells=self.ordinary
        self.snow={p:int(self.talent["first_snow"]) for p in self.base if self.talent["first_snow"]}
        self.timing=_timing(self.tables,self.card,_active_mode(self.tables,self.card,False)) if self.slot!=1 else None
        self.trait=trait_board(self.tables.read("excel/character_table.json")[SNOW],self.card["module_phase"] or {},self.card)
        self.forward_period=0
        if self.slot==1:
            node=next(c["fields"] for c in battle.evidence["skill"]["components"] if c["script"]=="BuffDuringCastingFixed" and not c["path"])
            self.forward_period=_raw_f32(node["_buffs"][0]["triggerInterval"])

    def log(self,frame,kind,**values):
        self.events.append(dict(frame=frame,kind=kind,**values))

    def remove(self,frame,cause):
        self.alive=False
        self.snow.clear()
        self.live.contacts.clear()
        self.live.ineligible.clear()
        self.live.blocked.clear()
        for tile in tuple(self.goals.tokens):
            self.goals.remove(tile,frame,cause)
        self.active=False
        self.world.hp[SNOW]=0
        if self.world.incoming:
            self.world.incoming.withdraw(SNOW)
        self.log(frame,cause,operator_id=SNOW)

    def incoming_hit(self,frame,source,loss,family,meta):
        if loss<=0 or not self.alive:
            return
        hp=self.world.hp[SNOW]
        after=max(0,hp-loss)
        lethal=after<=HP_EPSILON
        args=dict(max_hp_q32=_raw_f32(self.card["panel_attributes"]["maxHp"]),hp_q32=after,blessing_available=self.blessing)
        if lethal:
            targets=self.wave.snapshot(frame)
            ids=[k for k,v in targets.items() if circle_in_range(v["row"],v["col"],v["radius"],self.wave.cells,box_query=True)]
            result=run_blessing(self.tables,self.card,dict(args,callback="TRY_SET_HP_ZERO",
                selected_enemy_ids=ids,undeadable=meta.get("undeadable",False),modifier_cancelled=meta.get("modifier_cancelled",False)))
            if result["consumed_hp_zero"]:
                self.blessing=False
                after=result["hp_after_q32"]
                self.log(frame,"blessing_consumed",source=source,healed_q32=after,targets=ids)
            elif meta.get("modifier_cancelled") or meta.get("undeadable"):
                after=meta.get("hp_after_other_consumer_q32",hp if meta.get("modifier_cancelled") else ONE)
        else:
            source_flags=self.wave.flags.at(source,frame) if source in self.wave.births else meta.get("source_flags",())
            sender=dict(side=meta.get("source_side","ENEMY"),id=source,flags=source_flags)
            result=run_blessing(self.tables,self.card,dict(args,callback="ON_TAKE_DAMAGE",damage_source=sender))
        self.world.hp[SNOW]=after
        self.world.incoming.units[SNOW]["hp"]=after
        self.log(frame,family+"_hit",source=source,holder=SNOW,damage_q32=loss,hp_before_q32=hp,hp_after_q32=after)
        for app in result["buff_applications"]:
            k,seconds=app["target"],app["duration_seconds"]
            if k==SNOW:
                self.frozen_until=frame+(_raw_f32(seconds)+self.step-1)//self.step
                self.log(frame,"blessing_self_frozen",expiry_frame=self.frozen_until)
                self.impact=None
                if self.active:
                    self.next_search=self.frozen_until
                else:
                    self.cast=self.switch=None
                    self.forward=None
                    self.baked_events.clear()
            elif k in self.wave.active[frame] and k not in self.world.deaths:
                if app["buff"]=="c2e_cold":
                    self.live.apply_cold(k,frame,seconds)
                elif not self.battle.scene.enemies[self.wave.births[k]["enemy_key"]]["attributes"]["frozenImmune"]["m_value"]:
                    until=frame+self.live.status_ticks(k,frame,seconds)
                    self.live.set_frozen(k,frame,until)
                    self.log(frame,"blessing_enemy_frozen",enemy=k,expiry_frame=until)
            else:
                self.log(frame,"blessing_status",source=k,buff=app["buff"],seconds=seconds)
        if after<=HP_EPSILON:
            self.remove(frame,"operator_defeated")

    def defenses(self,frame):
        panel=self.card["panel_attributes"]
        overridden=self.active or self.switch is not None or self.slot==1 and frame<self.blocked_until
        trait={} if overridden else self.trait
        self.world.defenses[SNOW]=mul(_raw_f32(panel["def"]),ONE+_raw_f32(trait.get("def",0)))
        self.world.resistances[SNOW]=_raw_f32(panel["magicResistance"]+trait.get("magic_resistance",0))

    def press(self,frame,phase):
        ready=self.gauge.state.points
        if self.slot==2:
            if self.active:
                self.end_mode(frame,"manual_discard")
                self.log(frame,"skill_button",sp=ready,outcome="discarded")
            else:
                self.log(frame,"skill_button",sp=ready,outcome="not_discardable")
            return
        if not self.alive or frame<self.frozen_until or self.cast or self.switch or self.active:
            self.log(frame,"skill_button",sp=ready,outcome="owner_unavailable")
            return
        state="skill" if frame<self.blocked_until else "idle"
        cast=resolve_cast_start(self.tables,self.battle.evidence,frame=frame,phase=phase,sp=ready,owner_state=state,**self.cast_args)
        self.log(frame,"skill_button",sp=ready,**({"outcome":cast["status"]} if cast["cast_frame"] is None else {}))
        if cast["cast_frame"] is None:
            return
        self.cast=cast
        if cast["cast_frame"]==frame:
            self.commit(frame)

    def commit(self,frame):
        cast=self.cast
        self.cast=None
        self.gauge.state.points=cast["sp_after"]
        self.log(frame,"skill_cast",sp=cast["sp_after"])
        if self.slot==1:
            self.impact=cast["spell_frame"]
            self.blocked_until=frame+self.clock["end_frame"]
            self.recovery=frame+self.clock["first_unblocked_recovery_tick"]
            self.baked_events={e["frame"]:e for e in cast["events"] if e["kind"]=="baked_OnAttack"}
            self.forward=RecoveryTimer(self.forward_period)
            self.forward_left=int(self.board["trig_cnt"])
            self.front.clear()
        else:
            self.switch=cast["spell_frame"]
            self.expiry=frame+self.clock["natural_end"]["end"]
            self.recovery=frame+self.clock["first_unblocked_recovery_tick"]
            self.rapid=RecoveryTimer(_raw_f32(self.board["interval"]))

    def switch_mode(self,frame):
        self.active=True
        self.switch=None
        self.next_search=frame+1
        self.wave.cells=self.skill
        if self.slot==3:
            targets=self.wave.snapshot(frame)
            for key,target in targets.items():
                target["one_minus_status_resistance"]=self.live.resist_ratio(key,frame)
            selected=run_s3_attraction(self.tables,self.card,dict(**self.layout,frame=frame,targets=targets,
                attraction_terrain=dict(ordinary_ground=self.ground,end_tiles=self.exits)),
                navigation=self.live.get_navigation(),random=self.random)["receivers"]
            self.log(frame,"mode_switch",attraction=selected)
            for key,row in selected.items():
                if row["status"]=="selected":
                    self.live.apply_attraction(key,frame,row)
        else:
            self.log(frame,"mode_switch")

    def end_mode(self,frame,reason=None):
        self.active=False
        self.wave.cells=self.ordinary
        self.impact=self.next_search=self.expiry=self.rapid=None
        self.recovery=frame+1
        self.log(frame,"mode_end",**({"reason":reason} if reason else {}))

    def grow(self,frame):
        if self.slot==2 and self.active:
            protected=set(self.goals.tokens)
            old=self.snow
            update=run_s2_snow_outward(self.tables,self.card,dict(**self.layout,frame=frame,
                snow_eligible_tiles=self.ground-protected,end_tiles=self.exits,occupied_end_tiles=protected,
                initial_snow=[dict(row=r,col=c,layers=n) for (r,c),n in old.items()]))
            self.snow={(t["row"],t["col"]):t["layers"] for t in update["tiles"]}
            for token in update["tokens"]:
                self.goals.create(frame,token)
            self.log(frame,"snow_outward",tokens=update["tokens"],
                     changes=[t for t in update["tiles"] if old.get((t["row"],t["col"]))!=t["layers"]])
        else:
            cells=(self.skill & self.ground if self.active and self.slot==3 else self.base)-self.goals.tokens.keys()
            for cell in cells:
                self.snow[cell]=min(self.maximum,self.snow.get(cell,0)+1)
            if self.slot==3:
                self.log(frame,"snow_growth",tiles=len(cells))

    def spread(self,frame):
        dr,dc=DIRECTION[self.place["direction"]]
        self.front.update(self.base & self.snow.keys())
        pending={(r+dr,c+dc):self.snow[r,c] for r,c in self.front if (r,c) in self.snow and (r+dr,c+dc) in self.ground}
        changes=[]
        for p,n in sorted(pending.items()):
            if self.snow.get(p,0)<n:
                self.snow[p]=n
                changes.append(dict(row=p[0],col=p[1],layers=n,permanent=p in self.base))
        self.front.update(pending)
        self.log(frame,"snow_forward",changes=changes)
        self.forward_left-=1
        if not self.forward_left:
            self.forward=None

    def hit(self,frame):
        aura=self.live.aura_members(frame) if (self.card["module_info"] or {}).get("typeIcon")=="plx-y" else []
        data=execute_phalanx_event(self.tables,self.card,dict(**self.layout,kind="phalanx_impact",frame=frame,
             targets=self.wave.snapshot(frame),aura_count=len(aura)))
        self.log(frame,"area_arts_damage",hits=data["hits"],aura_members=aura)
        for key,hit in data["hits"].items():
            self.world.damage(key,frame,hit["hp_after_q32"])
            if key in self.world.deaths or self.slot!=1:
                continue
            req=hit["status_requests"]
            self.live.apply_cold(key,frame,req["cold_seconds"])
            if "push_force" in req:
                birth=self.wave.births[key]
                unit=self.battle.scene.enemies[birth["enemy_key"]]
                mass=unit["attributes"]["massLevel"]["m_value"]
                strength=push_force(self.tables,req["push_force"],mass)
                if strength:
                    body=enemy_push_profile(self.tables,unit["prefabKey"]["m_value"] or birth["enemy_key"])
                    prior=self.live.motions.get(key)
                    motion=start_push(self.tables,body,self.wave.positions[frame][key],DIRECTION[self.place["direction"]],
                        force_level=req["push_force"],weight=mass,frame=frame,previous=prior[1] if prior else None,
                        move_multiplier=self.battle.scene.level["options"]["moveMultiplier"])
                    self.live.apply_push(key,frame,body,motion)
                    self.log(frame,"snow_push_impulse",enemy=key,position=motion.position,weight=mass,force=strength,velocity=motion.velocity)
        self.impact=None

    def tick(self,frame):
        if self.expiry==frame:
            self.end_mode(frame)
        before=dict(self.snow)
        if self.rapid is None and self.natural.advance(self.step):
            self.grow(frame)
        if self.rapid and self.rapid.advance(self.step):
            self.grow(frame)
        forward=self.forward and self.forward.advance(self.step)
        if forward:
            self.spread(frame)
        self.live.contacts_at(frame,self.snow,before,self.active,after_objects=bool(forward))
        if frame>=self.recovery and not self.active and self.cast is None and self.switch is None:
            self.gauge.state.advance(self.gauge.step)
        if frame<self.frozen_until:
            return
        if self.cast and self.cast["cast_frame"]==frame:
            self.commit(frame)
        if self.switch==frame:
            self.switch_mode(frame)
        if self.slot==2 and not self.active and self.gauge.state.points>=self.gauge.state.cost:
            cast=resolve_cast_start(self.tables,self.battle.evidence,frame=frame,phase="before_coroutines",
                                    sp=self.gauge.state.points,**self.cast_args)
            self.log(frame,"automatic_trigger",sp=self.gauge.state.points)
            self.gauge.state.points=cast["sp_after"]
            self.log(frame,"skill_cast",sp=self.gauge.state.points)
            self.switch_mode(frame)
        if self.next_search==frame:
            targets=self.wave.search(frame,cells=self.skill)
            if targets:
                self.log(frame,"attack_start",targets=targets)
                self.impact=frame+self.timing["ticks_to_key"]
                self.next_search=frame+self.timing["interval_ticks"]
            else:
                self.next_search=frame+3
        if frame in self.baked_events:
            self.events.append(self.baked_events.pop(frame))
        if self.impact==frame:
            self.hit(frame)


def run_sbell_stage(tables,request):
    return run_with_phalanx_waves(_stage,tables,request)


def _stage(tables,request,releases):
    actions=defaultdict(list)
    deploy=next(a for a in request["actions"] if a.get("kind","deploy")=="deploy" and a["operator_id"]==SNOW)
    for a in request["actions"]:
        actions[a["frame"]].append(a)
    initial={k:v for k,v in request.items() if k not in ("battle_random_seed","enemy_status_resistance")}
    initial["actions"]=[{k:deploy[k] for k in ("operator_id","frame","row","col","direction")}]
    slot=request["squad"][0]["skill"]
    battle=prepare_channel(tables,initial,operator=SNOW,skill=slot,allow_no_module=True,allow_idle=True,
        contact_checked_during_replay=True,allow_partial_movement=True,completing_waves=releases,live_deployment=True)
    card,scene=battle.card,battle.scene
    events=[]
    world=StageReceivers(battle,request,[deploy],{SNOW:card},events,native_first_walk=slot!=1)
    wave=world.wave
    defeat_log=[]
    live=SbellSnowWave(battle,wave,set(),events,defeat_log)
    live.status_spans=request.get("enemy_status_resistance",())
    live.incoming=world.incoming
    goals=FrozenSnowGoals(live)
    goals.world=world
    field=DeploymentField(tables,battle.opening,scene)
    receiver=world.incoming.units.pop(SNOW,None) if world.incoming else None
    paid=0
    deployed=[]
    ground=set()
    exits=set()
    grid=scene.level["mapData"]["map"]
    for r,row in enumerate(grid):
        for c in range(len(row)):
            tile=scene.tile(r,c)
            if tile["heightType"]=="LOWLAND" and tile["passableMask"]=="ALL" and tile["tileKey"]!="tile_hole":
                ground.add((r,c))
                if tile["tileKey"]=="tile_end":
                    exits.add((r,c))
    random=tables.combat_random(request.get("battle_random_seed",0))
    births=defaultdict(int)
    for b in wave.births.values():
        births[b["frame"]]+=1
    owner=None
    stop="horizon"
    for f in range(request["end_frame"]+1):
        field.before_objects(f)
        random.advance_spawn_offsets(births[f])
        if live.status_spans or battle.request.get("enemy_target_flags"):
            live.advance_resistance(f)
        if world.incoming:
            world.incoming.snow_control=live
            world.incoming.advance(f,goals.taunt)
        live.advance(f)
        if goals.tokens:
            goals.advance(f,set(goals.tokens))
        world.settle_exits(f)
        if world.life==0:
            stop="stage_defeat"
            break
        if owner and owner.alive:
            if world.incoming:
                owner.defenses(f)
                for source,uid,loss,kind,meta in world.incoming.damage_events(f,world.defenses,world.resistances,world.external):
                    if uid in goals.holders:
                        tile=goals.holders[uid]
                        after=max(0,goals.tokens[tile]["hp"]-loss)
                        goals.damaged(tile,source,f,after,loss)
                    elif uid==SNOW:
                        owner.incoming_hit(f,source,loss,kind,meta)
                        if not owner.alive:
                            field.occupied.discard((owner.place["row"],owner.place["col"]))
                            field.free_slots+=field.initial["cards"][SNOW]["slots"]
                            field.available_after[SNOW]=f+(_raw_f32(card["panel_attributes"]["respawnTime"])+owner.step-1)//owner.step
            if owner.alive:
                owner.tick(f)
        for a in actions.get(f,()):
            kind=a.get("kind","deploy")
            if kind=="deploy" and a["operator_id"]==SNOW:
                cost=field.deployment_cost(SNOW)
                reason="already_deployed" if owner and owner.alive else field.place_operator(a,goals.tokens)
                if reason:
                    events.append(dict(kind="deploy_rejected",frame=f,operator_id=SNOW,reason=reason))
                    continue
                paid=cost
                deployed.append(dict(a,paid=cost))
                battle.deploy=a
                wave.start=f
                world.hp[SNOW]=_raw_f32(card["panel_attributes"]["maxHp"])
                if world.incoming:
                    world.incoming.units[SNOW]=dict(receiver,placement=dict(a),hp=world.hp[SNOW])
                    world.incoming.move_holder(SNOW,a["row"],a["col"])
                owner=SnowCaster(battle,world,live,goals,random,ground,exits,a)
                owner.log(f,"deploy",sp=owner.gauge.state.points)
            elif kind=="skill" and owner and owner.alive:
                owner.press(f,a.get("phase","after_tick"))
            elif kind=="withdraw" and owner and owner.alive:
                field.occupied.discard((owner.place["row"],owner.place["col"]))
                field.free_slots+=field.initial["cards"][SNOW]["slots"]
                field.available_after[SNOW]=f+(_raw_f32(card["panel_attributes"]["respawnTime"])+owner.step-1)//owner.step
                field.dp=min(field.cap,field.dp+min(field.initial["cards"][SNOW]["cost"],paid//2))
                owner.remove(f,"withdraw")
        if field.order=="input_before_cost":
            field.gain(f)
        for key in defeat_log:
            world.deaths.setdefault(key,f)
        defeat_log.clear()
        ended=world.stage_ended(f)
        if ended:
            stop=ended
            break
    finished=world.deaths|world.exits
    return dict(stage=battle.opening["code"],status=f"stage_sbell_s{slot}",stop=stop,through_frame=f,
        deployment=(battle.placement_event or deployed[0]) if deployed else None,deployments=deployed,events=[e for e in events if e["frame"]<=f],
        spawns=wave.spawns(f),remaining_life=world.life,defeated=list(world.deaths),escapes=world.escapes,
        battle_outcome=world.outcome(f),wave_schedule_pending=world.schedule["pending"],scheduler_release_frames=finished,
        applied_rules=wave.applied_rules(f,finished),source=tables.source,sp=owner.gauge.state.points if owner else 0,
        hp_q32=world.hp[SNOW],blessing_available=owner.blessing if owner else True,
        frozen_end_tiles=sorted(goals.tokens),tokens=[t for e in events for t in e.get("tokens",())],
        snow_tiles=[dict(row=r,col=c,layers=n) for (r,c),n in sorted(owner.snow.items())] if owner else [])
