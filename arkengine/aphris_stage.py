"""Aphris's channel and magazine share the same beam, token card and combat receivers."""
from collections import defaultdict
from dataclasses import replace

from .aphris_magazine import aphris_magazine_profile, charge_schedule, relay_beam_hit
from .aphris_relays import AphrisRelays, RELAY_INPUTS, RelayField
from .aphris_wave import AphrisWave
from .area_caster_skills import APHRIS, sampling_penetration
from .caster_ammo import CasterAmmoCounter
from .caster_cast import run_committed_cast
from .caster_catalog import resolve_caster_loadout, shared_barrier_panel
from .caster_ranges import skill_range_cells
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON, ordinary_arts_hit
from .ebenholz_stage import AlliedEbenholz
from .fixed import mul
from .lin_interactions import EBENHOLZ
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import deploy_skill
from .stage_channel import prepare_channel
from .volley_damage import _raw_f32


def run_aphris_stage(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


class Beam:
    def __init__(self,battle,field):
        self.battle,self.field=battle,field
        self.present={}
        self.channel=False
        self.geometry={}
        self.cells={}
        self.previous=None
        self.penetration=sampling_penetration(battle.card,battle.evidence)
        grid=battle.scene.level["mapData"]["map"]
        self.rows,self.cols=len(grid),len(grid[0])

    def update(self):
        relays=list(self.field.card.active.values())
        allies=[p for k,p in self.present.items() if k!=APHRIS and p.get("layer",0)==self.battle.deploy.get("layer",0)]
        turns=[*relays,*allies] if self.channel else relays
        layout=(self.battle.deploy["row"],self.battle.deploy["col"],self.battle.deploy["direction"],self.channel,tuple((u["row"],u["col"],u["direction"],"relay" in u) for u in turns))
        if layout!=self.previous:
            self.previous=layout
            self.geometry=skill_range_cells(self.battle.tables,self.battle.card,dict(placement=self.battle.deploy,
                rows=self.rows,cols=self.cols,relays=relays,allied_turns=turns,area="sampling_aura",skill_active=self.channel))
            self.cells=dict(zip(self.geometry["cells"],zip(self.geometry["dir_change_times"],self.geometry["extend_times"])))

    def penetration_for(self,uid):
        place=self.present.get(uid)
        if APHRIS not in self.present or not place or place.get("layer",0)!=self.battle.deploy.get("layer",0):
            return 0
        self.update()
        return self.penetration if (place["row"],place["col"]) in self.cells else 0


class AphrisCombat:
    def __init__(self,battle,request,field,world,beam,events):
        self.battle,self.field,self.world,self.beam,self.events=battle,field,world,beam,events
        self.tables,self.card,self.evidence=battle.tables,battle.card,battle.evidence
        self.end=request["end_frame"]
        self.slot=self.card["skill"]
        self.deploy=battle.deploy
        ordinary=aphris_magazine_profile(self.tables,self.card,self.evidence,self.deploy,ordinary=True)
        self.board,self.view=ordinary[:2]
        self.profile={"ordinary":ordinary[4:]}
        if self.slot==3:
            self.profile["magazine"]=aphris_magazine_profile(self.tables,self.card,self.evidence,self.deploy)[4:]
        self.cast_args=dict(direction=self.deploy["direction"],facing=self.view)
        self.poll=self.deploy["frame"]+_birth(self.tables,self.card,self.evidence,self.view)["born_exit_update"]+1
        self.born_until=self.poll-1
        self.gauge,_=deploy_skill(self.tables,self.card,self.deploy["frame"])
        self.ready=self.deploy["frame"] if self.gauge.state.points==self.gauge.state.cost else None
        self.recovery=self.deploy["frame"]+1
        self.step=_raw_f32(self.tables.fixed_timestep())
        interval=next(c["fields"]["_hitInterval"] for c in self.evidence["character"]["components"] if c["script"]=="AphrisMeleeAttack")
        self.bend_wait=fixed_wait_frames(interval/self.tables.fixed_timestep())
        self.mode="ordinary"
        self.shot=self.counter=self.charge=self.first_beam=None
        self.attach=self.switch=self.finish=None
        self.pulses=set()
        self.cycles,self.charges,self.buttons=[],[],[]
        self.beam_changes,self.pulse_targets,self.hits=[],[],[]
        self.ordinary_events,self.ordinary_hits=[],[]

    def log(self,kind,frame,**data):
        e=dict(kind=kind,frame=frame,**data)
        self.events.append(e)
        if kind.startswith("ordinary_"):
            self.ordinary_events.append(e)
        return e

    def drop(self,frame,cause):
        self.shot=None
        self.pulses.clear()
        self.beam.channel=False
        self.attach=self.switch=self.finish=None
        self.poll=None
        if self.field.card.boosted:
            self.field.card.switch(frame,False)
        else:
            for key in list(self.field.card.active):
                self.field.card.withdraw(frame,key,cause)
        self.mode="gone"

    def press(self,frame):
        points=self.gauge.state.points
        if self.mode=="gone" or APHRIS not in self.beam.present:
            self.buttons.append(dict(frame=frame,sp=points,outcome="not_deployed"))
            return
        if self.mode=="magazine":
            self.counter.discard(frame)
            self.log("discard_ammo",frame)
            return
        if self.mode!="ordinary" or frame<self.recovery-1:
            if self.slot==3:
                self.log("discard_rejected",frame,reason="inactive_or_charging")
            self.buttons.append(dict(frame=frame,sp=points,outcome="skill_active"))
            return
        state="born" if frame<self.born_until else "attack" if self.shot else "idle"
        cast=resolve_cast_start(self.tables,self.evidence,frame=frame,phase="after_tick",sp=points,owner_state=state,**self.cast_args)
        self.buttons.append(dict(frame=frame,sp=points,outcome=cast["status"]))
        if cast["cast_frame"] is None:
            return
        self.gauge.state.points=cast["sp_after"]
        self.events.extend(e for e in cast["events"] if e["frame"]==frame)
        if self.slot==2:
            timeline=run_committed_cast(self.tables,self.evidence,horizon=fixed_wait_frames(self.card["skill_row"]["duration"]*30)+5,**self.cast_args)
            self.finish=frame+timeline["end_frame"]
            self.recovery=frame+timeline["first_unblocked_recovery_tick"]
            self.pulses.update(frame+f for f in timeline["channel_pulse_frames"])
            self.cycles.append(dict(cast=cast,end_frame=self.finish,first_recovery_tick=self.recovery))
            if self.shot:
                self.log("ordinary_beam_interrupted",frame,attack_frame=self.shot["start"])
            self.shot=self.poll=None
            self.mode="channel"
            self.beam.channel=True
            self.beam.update()
            if self.first_beam is None:
                self.first_beam=self.beam.geometry
        else:
            self.charge=charge_schedule(self.tables,self.evidence,dict(frame=self.deploy["frame"],placement=self.deploy),
                self.board,cast,self.ready,self.gauge.step)
            self.charges.append(self.charge)
            self.attach=next(e["frame"] for e in cast["events"] if e["kind"]=="attach_wrapped_abilities")
            self.switch=self.charge["mode_switch_frame"]
            self.mode="starting"
            self.counter=CasterAmmoCounter(self.evidence)
        return cast["cast_frame"]

    def objects(self,frame):
        if APHRIS not in self.beam.present:
            return
        if self.mode=="magazine" and self.counter.ready_to_reset and frame>self.counter.frame and self.counter.owner_tick(frame):
            self.field.card.switch(frame,False)
            self.log("restore_default_mode",frame)
            self.mode="ordinary"
            self.shot=None
            self.poll=self.recovery=frame+1
            self.ready=None
        if self.mode=="ordinary" and frame>=self.recovery:
            self.gauge.state.advance(self.gauge.step)
            if self.ready is None and self.gauge.state.points==self.gauge.state.cost:
                self.ready=frame
        if self.poll is None or frame<self.poll or self.mode not in ("ordinary","starting","magazine"):
            return
        if self.mode=="magazine" and not self.counter.remaining:
            return
        self.beam.update()
        targets=self.world.targets(frame,self.beam.cells,search=True)
        if not targets:
            self.poll=frame+3
            return
        ordinary=self.mode!="magazine"
        delay,period,tail=self.profile["ordinary" if ordinary else "magazine"]
        self.shot=dict(start=frame,spell=frame+delay,finish=frame+tail,targets=targets,ordinary=ordinary,segments={})
        self.poll=frame+period
        if not ordinary and self.charge["accepted_attack_frame"] is None:
            self.charge["accepted_attack_frame"]=frame
        self.log("ordinary_attack_start" if ordinary else "attack_start",frame,targets=targets)

    def coroutines(self,frame):
        if self.mode=="channel" and frame==self.finish:
            self.mode="ordinary"
            self.beam.channel=False
            self.poll=self.recovery
            self.log("channel_ended",frame)
        if frame==self.attach:
            if self.shot:
                pending=[k for t,keys in self.shot["segments"].items() if t>=frame for k in keys]
                self.log("ordinary_beam_interrupted",frame,attack_frame=self.shot["start"],
                         before_spell=frame<=self.shot["spell"],cancelled_segments=pending)
            self.shot=self.attach=self.poll=None
            self.log("attach_wrapped_abilities",frame)
            self.field.card.switch(frame,True)
            self.mode="charging"
        if frame==self.switch:
            self.switch=None
            self.mode="magazine"
            self.poll=frame+1
            self.log("mode_switch",frame)
        self.beam.update()
        if frame in self.pulses:
            self.channel_hit(frame)
        shot=self.shot
        if shot is None:
            return
        ordinary=shot["ordinary"]
        if frame==shot["spell"]:
            targets=self.world.targets(frame,self.beam.cells)
            if not targets:
                self.log("ordinary_beam_cast_failed" if ordinary else "beam_cast_failed",frame,attack_frame=shot["start"],
                    finish_reason="TARGET_DEAD",remaining_ammo=None if ordinary else self.counter.remaining)
                if not ordinary:
                    self.counter.ability_event(frame,"cast_end")
                self.poll,self.shot=frame+3,None
                return
            segments={}
            for key in targets:
                tile=tuple(round(v) for v in self.world.wave.positions[frame][key])
                bends=self.beam.cells[tile][0]
                segments[key]=bends
                shot["segments"].setdefault(frame+self.bend_wait*bends,[]).append(key)
            if not ordinary:
                self.counter.ability_event(frame,"spell_on")
            self.log("ordinary_beam_spell" if ordinary else "beam_spell",frame,attack_frame=shot["start"],
                baked_on_attack_frame=frame-1,trigger_targets=shot["targets"],targets=targets,segment_by_target=segments,
                remaining_ammo=None if ordinary else self.counter.remaining)
        for key in shot["segments"].get(frame,()):
            self.beam_hit(key,frame,ordinary)
        if frame>=shot["finish"] and not any(f>frame for f in shot["segments"]):
            if not ordinary:
                self.counter.ability_event(frame,"cast_end")
            self.shot=None

    def beam_hit(self,key,frame,ordinary):
        wave=self.world.wave
        if key not in wave.active[frame] or key in self.world.deaths or "INVINCIBLE" in wave.flags.at(key,frame):
            self.log("relay_segment_target_invalid",frame,target=key)
            return
        tile=tuple(round(v) for v in wave.positions[frame][key])
        extent=self.beam.cells.get(tile,(0,0))[1]
        atk=self.field.card.attack(self.card["panel_attributes"]["atk"],0 if ordinary else self.board["atk"])
        attrs=wave.attrs[key]
        res=max(0,attrs["resistance_q32"]-_raw_f32(self.beam.penetration_for(APHRIS)))
        packets,slow=relay_beam_hit(self.board,atk,attrs["hp_q32"],res,extent,ordinary=ordinary)
        for kind,hit in packets:
            before=attrs["hp_q32"]
            self.world.damage(key,frame,hit.hp_after_raw)
            e=self.log("ordinary_beam_impact" if ordinary else kind,frame,target=key,id=key,tile=tile,
                extension_count=extent,attack_q32=atk,damage_q32=hit.damage_raw,hp_before_q32=before,hp_after_q32=hit.hp_after_raw)
            if ordinary:
                self.ordinary_hits.append(e)
        if slow and key not in self.world.deaths:
            ticks=(slow+self.step-1)//self.step
            self.log("apply_sluggish",frame,target=key,duration_q32=slow,duration_ticks=ticks)
            self.world.sluggish(key,frame,ticks)

    def channel_hit(self,frame):
        cells=self.beam.geometry["cells"]
        if not self.beam_changes or cells!=self.beam_changes[-1]["cells"]:
            self.beam_changes.append(dict(frame=frame,cells=cells))
        keys=self.world.targets(frame,self.beam.cells)
        self.pulse_targets.append(dict(frame=frame,targets=keys))
        atk=self.field.card.attack(self.card["panel_attributes"]["atk"])
        for key in keys:
            wave=self.world.wave
            attrs=wave.attrs[key]
            before=attrs["hp_q32"]
            hit=ordinary_arts_hit(attack_raw=mul(atk,_raw_f32(self.board["atk_scale"])),hp_raw=before,
                resistance_raw=attrs["resistance_q32"],fixed_res_penetration_raw=_raw_f32(self.beam.penetration_for(APHRIS)))
            self.world.damage(key,frame,hit.hp_after_raw)
            self.hits.append(self.log("channel_hit",frame,id=key,target=key,hp_before_q32=before,
                damage_q32=hit.damage_raw,hp_after_q32=hit.hp_after_raw,attack_q32=atk,
                enemy=wave.births[key]["enemy_key"],tile=[round(v) for v in wave.positions[frame][key]]))

def _simulate(tables,request,releases):
    pick=next(p for p in request["squad"] if p["operator_id"]==APHRIS)
    actions=[dict(a,operator_id=a.get("operator_id",APHRIS)) for a in request["actions"]]
    ordered=defaultdict(list)
    placements=[]
    for action in actions:
        ordered[action["frame"]].append(action)
        if action.get("kind","deploy")=="deploy":
            placements.append({k:action[k] for k in ("operator_id","frame","row","col","direction")})
    first={}
    for place in sorted(placements,key=lambda p:p["frame"]):
        first.setdefault(place["operator_id"],place)
    placements=list(first.values())
    allies=[p for p in placements if p["operator_id"]!=APHRIS]
    base={k:v for k,v in request.items() if k not in ("ally_arts_hits","battle_random_seed")}
    selected={p["operator_id"] for p in placements}
    battle=prepare_channel(tables,dict(base,actions=placements,squad=[p for p in request["squad"] if p["operator_id"] in selected]),
        operator=APHRIS,skill=pick["skill"],supporting_deploys=allies,range_options=dict(relays=[],allied_turns=[]),
        allow_no_module=True,allow_idle=True,contact_checked_during_replay=True,allow_partial_movement=True,completing_waves=releases,live_deployment=True)
    field=RelayField(tables,battle.card,battle.evidence,battle.opening,battle.scene,battle.deploy)
    beam=Beam(battle,field)
    predefined={}
    for unit in (battle.scene.level.get("predefines") or {}).get("characterInsts") or ():
        key=unit["alias"] or unit["inst"]["characterKey"]
        predefined[key]=dict(unit["position"],direction=unit["direction"],operator_id=unit["inst"]["characterKey"])
        if not unit["hidden"]:
            beam.present[key]=predefined[key]
    stage_inputs=defaultdict(list)
    for event in battle.opening["wave_schedule"]["events"]:
        if event["kind"]=="stage_action" and event["enemy_key"] in predefined:
            action=battle.scene.level["waves"][event["wave_index"]]["fragments"][event["fragment_index"]]["actions"][event["action_index"]]
            stage_inputs[event["frame"]].append((action["actionType"],event["enemy_key"]))
    events=[]
    cards={APHRIS:battle.card}
    for cfg in request["squad"]:
        uid=cfg["operator_id"]
        if uid==APHRIS or uid not in selected:
            continue
        if uid==EBENHOLZ and cfg["skill"]==3:
            cards[uid]=resolve_caster_loadout(tables,uid,3,cfg["module"],module_level=cfg["module_level"],skill_level=cfg["skill_level"])
        else:
            cards[uid]=shared_barrier_panel(tables,cfg)
    world=AphrisWave(battle,request,placements,cards,events)
    combat=AphrisCombat(battle,request,field,world,beam,events)
    allied=None
    if EBENHOLZ in cards and cards[EBENHOLZ]["skill"]==3:
        deployed=next(p for p in allies if p["operator_id"]==EBENHOLZ)
        allied=AlliedEbenholz(battle,dict(request,actions=actions),world.wave,deployed,cards[EBENHOLZ],events,world.deaths)
        allied.world.fixed_penetration=lambda:_raw_f32(beam.penetration_for(EBENHOLZ))
        if world.incoming:
            world.incoming.attack_ratio=lambda k:allied.world.receivers[k].atk_ratio if k in allied.world.receivers else 0
    packets=defaultdict(list)
    for packet in request.get("ally_arts_hits",()):
        packets[packet["frame"]].append(packet)
    receivers={uid:dict(unit) for uid,unit in world.incoming.units.items()} if world.incoming else {}
    if world.incoming:
        world.incoming.units.clear()
    paid={}
    opening_deployments={e["operator_id"]:e for e in battle.opening["events"] if e["kind"]=="deployed"}
    deployments=[]
    withdrawals=[]
    aura={}
    path=None
    def leave(uid,frame,cause):
        place=beam.present.pop(uid,None)
        if place is None:
            return
        field.occupied.discard((place["row"],place["col"]))
        if uid in field.initial["cards"]:
            field.free_slots+=field.initial["cards"][uid]["slots"]
            cooldown=_raw_f32(cards[uid]["panel_attributes"]["respawnTime"])
            field.available_after[uid]=frame+(cooldown+combat.step-1)//combat.step
        if world.incoming:
            world.incoming.withdraw(uid)
        if uid==APHRIS:
            combat.drop(frame,cause)
        if uid==EBENHOLZ and allied:
            allied.world.hp=0
        events.append(dict(kind=cause,frame=frame,operator_id=uid))

    def player(action,frame):
        nonlocal combat,allied
        kind=action.get("kind","deploy")
        uid=action["operator_id"]
        if kind in RELAY_INPUTS:
            field.relay_input(frame,action,APHRIS in beam.present)
        elif kind=="deploy":
            p={k:v for k,v in action.items() if k in ("frame","row","col","direction","operator_id","layer")}
            cost=field.deployment_cost(uid)
            reason="already_deployed" if uid in beam.present else field.place_operator(p)
            if reason is not None:
                events.append(dict(kind="deploy_rejected",frame=frame,operator_id=uid,reason=reason))
                return
            beam.present[uid]=p
            paid[uid]=dict(opening_deployments.get(uid,{}) if frame==first[uid]["frame"] else {},**p,
                paid=cost,cost_before=field.dp+cost,cost_after=field.dp,cost_bar=field.timer.bar(field.dp,field.cap),
                cost_units_before=field.timer.display_units(field.dp+cost,field.cap),
                cost_units_after=field.timer.display_units(field.dp,field.cap),
                slots_before=field.free_slots+field.initial["cards"][uid]["slots"],slots_after=field.free_slots)
            deployments.append(paid[uid])
            world.hp[uid]=_raw_f32(cards[uid]["panel_attributes"]["maxHp"])
            if world.incoming:
                world.incoming.units[uid]=dict(receivers[uid],hp=world.hp[uid],placement=p,order=len(deployments))
                world.incoming.move_holder(uid,p["row"],p["col"])
            if uid==APHRIS:
                if frame!=battle.deploy["frame"] or combat.mode=="gone":
                    battle.deploy=p
                    field.host=p
                    prior=combat
                    combat=AphrisCombat(battle,request,field,world,beam,events)
                    for attr in ("cycles","charges","buttons","beam_changes","pulse_targets","hits","ordinary_events","ordinary_hits","first_beam"):
                        setattr(combat,attr,getattr(prior,attr))
                    history=field.card.events
                    field.card=AphrisRelays(tables,battle.card,battle.evidence)
                    field.card.events=history
                battle.deploy=field.host=p
            elif uid==EBENHOLZ and allied:
                if frame!=allied.first:
                    previous=allied.world
                    current_actions=[a for a in actions if a["frame"]>=frame]
                    allied=AlliedEbenholz(battle,dict(request,actions=current_actions),world.wave,p,cards[uid],events,world.deaths)
                    allied.world.shots=previous.shots
                    allied.world.receivers=previous.receivers
                    allied.world.fixed_penetration=previous.fixed_penetration
                allied.world.battle=replace(allied.world.battle,deploy=p)
                allied.world.update_range()
            events.append(dict(kind="deploy",frame=frame,operator_id=uid,paid=cost))
        elif kind=="withdraw":
            if uid in beam.present and uid not in paid:
                leave(uid,frame,"withdraw")
            elif uid in beam.present:
                branch=cards[uid]["branch"]
                rate={"charger":2,"merchant":0}.get(branch,1)
                refund=min(field.initial["cards"][uid]["cost"],paid[uid]["paid"]*rate//2)
                field.dp=min(field.cap,field.dp+refund)
                leave(uid,frame,"withdraw")
                withdrawals.append(action)
        elif kind=="move_ally" and uid in beam.present:
            p=beam.present[uid]
            field.occupied.discard((p["row"],p["col"]))
            p.update(row=action["row"],col=action["col"],direction=action.get("direction",p["direction"]))
            field.occupied.add((p["row"],p["col"]))
            if world.incoming and uid in world.incoming.units:
                world.incoming.move_holder(uid,p["row"],p["col"])
            if uid==EBENHOLZ and allied:
                allied.world.update_range()
            events.append(dict(kind="ally_moved",frame=frame,operator_id=uid,row=p["row"],col=p["col"]))
        elif kind=="skill" and uid==APHRIS:
            combat.press(frame)

    slot_three=combat.slot==3
    stop="horizon"
    for frame in range(request["end_frame"]+1):
        field.before_objects(frame)
        world.advance(frame)
        if world.life==0:
            stop="stage_defeat"
            break
        if world.incoming:
            for source,uid,loss,family,_ in world.incoming.damage_events(frame,world.defenses,world.resistances,world.external):
                if uid not in beam.present:
                    continue
                world.hp[uid]=max(0,world.hp[uid]-loss)
                world.incoming.units[uid]["hp"]=world.hp[uid]
                events.append(dict(kind=family+"_hit",frame=frame,source=source,holder=uid,hp_after_q32=world.hp[uid]))
                if world.hp[uid]<=HP_EPSILON:
                    leave(uid,frame,"operator_defeated")
        combat.objects(frame)
        later=[]
        for action in ordered.get(frame,()):
            if action.get("phase")=="after_tick":
                later.append(action)
            else:
                player(action,frame)
        if field.order=="input_before_cost":
            field.gain(frame)
        for kind,key in stage_inputs.get(frame,()):
            p=predefined[key]
            if kind=="ACTIVATE_PREDEFINED":
                beam.present[key]=p
                field.occupied.add((p["row"],p["col"]))
            elif kind=="WITHDRAW_PREDEFINED":
                beam.present.pop(key,None)
                field.occupied.discard((p["row"],p["col"]))
        combat.coroutines(frame)
        if slot_three and APHRIS in beam.present and beam.previous!=path:
            path=beam.previous
            by_tile={(r["row"],r["col"]):name for name,r in field.card.active.items()}
            events.append(dict(kind="relay_path",frame=frame,cells=list(beam.cells),
                relays=[by_tile[cell] for cell in beam.cells if cell in by_tile],
                deployed=list(field.card.active),bend_counts=list(beam.geometry["dir_change_times"]),
                extension_counts=list(beam.geometry["extend_times"])))
        if allied:
            if EBENHOLZ not in beam.present:
                allied.world.hp=0
            elif not world.incoming:
                allied.world.hp=world.hp[EBENHOLZ]
            allied.step(frame,world.incoming)
        for packet in packets.get(frame,()):
            uid,key=packet["operator_id"],packet["target"]
            if uid not in beam.present or key not in world.wave.active[frame] or not world.valid(key,frame):
                continue
            pen=beam.penetration_for(uid)+packet.get("fixed_res_penetration",0)
            attrs=world.wave.attrs[key]
            hit=ordinary_arts_hit(attack_raw=_raw_f32(packet["attack"]),hp_raw=attrs["hp_q32"],
                resistance_raw=attrs["resistance_q32"],fixed_res_penetration_raw=_raw_f32(pen))
            world.damage(key,frame,hit.hp_after_raw)
            events.append(dict(kind="ally_arts_impact",frame=frame,operator_id=uid,target=key,
                fixed_res_penetration=pen,damage_q32=hit.damage_raw,hp_after_q32=hit.hp_after_raw))
        for action in later:
            player(action,frame)
        current={uid:beam.penetration_for(uid) for uid in beam.present}
        if current!=aura:
            events.append(dict(kind="sampling_aura",frame=frame,recipients=current))
            aura=current
        ended=world.stage_ended(frame)
        if ended:
            stop=ended
            break
    finished=world.deaths|world.exits
    all_events=sorted([*events,*field.card.events,*world.wave.applied_rules(frame,finished)],key=lambda e:e["frame"])
    result=dict(status="aphris_s3_moving_wave" if combat.slot==3 else "aphris_s2_channel",stop=stop,
        operator_id=APHRIS,skill=combat.slot,stage=battle.opening["code"],events=all_events,through_frame=frame,
        deployment=next((p for p in deployments if p["operator_id"]==APHRIS),None),
        remaining_hp_q32={k:a["hp_q32"] for k,a in world.wave.attrs.items() if world.wave.births[k]["frame"]<=frame},
        remaining_life=world.life,escapes=world.escapes,spawns=world.wave.spawns(frame),holder_hp_q32=world.hp,
        relay_events=field.card.events,relay_card=field.card.snapshot(),cost=field.dp,sp=combat.gauge.state.points,
        battle_outcome=world.outcome(frame),wave_schedule_pending=world.schedule["pending"],scheduler_release_frames=finished,
        allied_deployments=[p for p in deployments if p["operator_id"]!=APHRIS],allied_withdrawals=withdrawals,
        source=tables.source,squad=request["squad"],button_results=combat.buttons)
    if combat.slot==3:
        return dict(result,magazine=combat.counter.snapshot() if combat.counter else None,charge=combat.charge,charges=combat.charges)
    first=combat.cycles[0] if combat.cycles else {}
    result.update(cast=first.get("cast"),cycles=combat.cycles,beam=combat.first_beam,beam_changes=combat.beam_changes,
        hits=combat.hits,pulse_targets=combat.pulse_targets,pulse_frames=[r["frame"] for r in combat.pulse_targets],
        ordinary_events=combat.ordinary_events,ordinary_hits=combat.ordinary_hits,button_results=combat.buttons[1:] if first and combat.buttons[0]["frame"]==first["cast"]["cast_frame"] else combat.buttons)
    if first:
        result.update(end_frame=first["end_frame"],first_recovery_tick=first["first_recovery_tick"])
    if stop=="stage_defeat":
        result.update(status=stop,end_frame=frame)
        result.pop("first_recovery_tick",None)
    return result
