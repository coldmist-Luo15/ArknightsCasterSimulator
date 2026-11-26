"""Ordinary stage clock and tracked receivers for Logos and Nymph."""
from math import floor
from collections import defaultdict

from .damage import HP_EPSILON
from .projectile_scene import stage_tile_positions
from .ranged_stage import MovingTargets,ranged_enemy_stats
from .stage_channel import prepare_channel
from .stage_receivers import StageReceivers
from .stage_channel import UnsupportedChannelScene
from .stage_route_cursor import ControlledRoute
from .phalanx_wave import ResumedGroundWalk
from .target_priority import remaining_route_priority
from .volley_damage import _raw_f32


class RangedStageTargets(MovingTargets):
    def __init__(self,tables,battle,request,card,deploy,*,native_walk,supports=(),ally_cards=None):
        self.receiver=receiver=StageReceivers(battle,request,[deploy,*supports],{card["operator_id"]:card,**(ally_cards or {})},[],native_first_walk=native_walk)
        wave=receiver.wave
        self.wave=wave
        self.assumptions=wave.assumptions
        self.route_clocks=wave.route_clocks
        scene=battle.scene
        stats={k:ranged_enemy_stats(scene,k,b,scene.enemies[b["enemy_key"]],True) for k,b in wave.births.items()}
        spawned={k:dict(enemy=b["enemy_key"],route=b["route_index"],spawn_frame=b["frame"],spawn_order=b["order"])
                 for k,b in wave.births.items()}
        priority=remaining_route_priority(scene,spawned,wave.history,additional_base_taunt={b["enemy_key"] for b in wave.births.values()})
        super().__init__(stats,wave.positions,wave.active,stage_tile_positions(tables,scene),priority,wave.births,deploy["frame"],
                         scene=scene,schedule=receiver.schedule,escaped=wave.escapes,history=wave.history)
        self.operator=card["operator_id"]
        self.present=battle.placement_event is not None
        self.hp=_raw_f32(card["panel_attributes"]["maxHp"]) if self.present else 0
        self.cards={card["operator_id"]:card,**(ally_cards or {})}
        self.incoming=receiver.incoming
        self.unit_templates={uid:dict(unit) for uid,unit in self.incoming.units.items()} if self.incoming else {}
        if self.incoming and not self.present:
            self.incoming.withdraw(self.operator)
        self.end=request["end_frame"]
        self.victory=None
        self.departed=set()
        self.shared_hits=defaultdict(list)
        for kind in ("ally_arts_hits","ally_physical_hits","element_hits"):
            for hit in request.get(kind,()):
                self.shared_hits[hit["frame"]].append((kind,hit))
        self.mount_rules=set()

    def enemy_before_projectile(self,key,frame):
        self.note_mount(key,frame,"same_coroutine_wave_birth_before_projectile")
        return frame

    def by_id(self,key,frame,*,projectile_born=None):
        from .stage_channel import object_position_frame
        tick=object_position_frame(self.births[key],frame,self.positions,self.active,key,
            observer_born=self.deployment_frame if projectile_born is None else projectile_born,
            observer_phase="INPUT" if projectile_born is None else "COROUTINES",same_phase_rule=self.enemy_before_projectile)
        return self.receiver_at(key,tick,observer_frame=frame)

    def receiver_at(self,key,tick,*,observer_frame=None,after_input=False):
        row=super().receiver_at(key,tick,observer_frame=observer_frame,after_input=after_input)
        if key in self.departed:
            return None
        if row is not None:
            row["receiver_mount_rule"]=lambda rule:self.note_mount(key,tick,rule)
            tile=self.tiles.get((floor(row["row"]+.5),floor(row["col"]+.5)))
            if tile:
                row["tile_z"]=tile["position"]["z"]-tile["locate_height_offset"]
        return row

    def note_mount(self,key,frame,rule):
        if (key,rule) not in self.mount_rules:
            self.assumptions.append(dict(kind="rule_applied",frame=frame,target=key,rule=rule))
            self.mount_rules.add((key,rule))

    def settle_exits(self,frame,alive):
        before=len(self.escaped)
        result=super().settle_exits(frame,alive)
        self.departed.update(r["instance"] for r in self.escaped[before:])
        self.active.get(frame,set()).difference_update(self.departed)
        return result

    def selectable(self,key,frame):
        return key not in self.departed and self.wave.targetable(key,frame)

    def allied_impacts(self,frame,states,logs,on_dark=None,arts_modifier=None):
        for kind,hit in self.shared_hits.get(frame,()):
            key=hit["target"]
            if key not in self.active[frame] or states[key].hp<=HP_EPSILON or key in getattr(self,"sleeping",()):
                continue
            state=states[key]
            prior=state.burst
            source=hit.get("operator_id",hit.get("source"))
            if kind=="ally_arts_hits":
                atk,res=_raw_f32(hit["attack"]),_raw_f32(self.stats[key]["res"])+state.res_add
                scale=1<<32
                if arts_modifier:
                    atk,res,*scales=arts_modifier(hit,key,atk,res)
                    if scales:
                        scale=scales[0]
                result=state.receive_ordinary(frame,source=source,damage_type="arts",attack_q32=atk,mitigation_q32=res,output_scale_q32=scale)
                logs.append(dict(kind="ally_arts_impact",frame=frame,target=key,source=source,
                                 damage_q32=result["damage_q32"],hp_after_q32=state.hp))
            elif kind=="ally_physical_hits":
                result=state.receive_ordinary(frame,source=source,damage_type="physical",attack_q32=_raw_f32(hit["attack"]),
                                              mitigation_q32=_raw_f32(self.stats[key]["defense"]))
                logs.append(dict(kind="ally_physical_impact",frame=frame,target=key,source=source,
                                 damage_q32=result["damage_q32"],hp_after_q32=state.hp))
            else:
                amount=hit["amount_q32"] if "amount_q32" in hit else _raw_f32(hit["amount"])
                state.apply_buildup(frame,hit["element"],amount)
                logs.append(dict(kind="ally_element_buildup",frame=frame,target=key,source=source,element=hit["element"],
                                 hp_after_q32=state.hp))
            if on_dark and state.burst=="DARK" and prior!="DARK":
                on_dark(frame,key)


    def snapshot(self,frame,*,projectile_born=None):
        return [r for r in super().snapshot(frame,projectile_born=projectile_born) if self.selectable(r["id"],frame)]

    def select(self,rows,frame,*,frame_by_target=None):
        by_key={r["id"]:r for r in rows}
        ordered=self.wave.attack_order(list(by_key),frame,observer_born=self.deployment_frame,
            after_objects=bool(frame_by_target) and all(f==frame for f in frame_by_target.values()))
        return by_key[ordered[0]]

    def remove_owner(self,frame,logs,reason):
        if self.present:
            self.present=False
            self.hp=0
            if self.incoming:
                self.incoming.withdraw(self.operator)
            logs.append(dict(kind=reason,frame=frame,operator_id=self.operator))

    def receive(self,frame,states,logs,*,res_bonus=0,field_scale=None,before_fire=None,interrupt_delay=None,hit_label=None,
                owner_damage=None,taunt=None):
        incoming=self.incoming
        if not incoming:
            return
        w=self.wave
        for key,state in states.items():
            w.attrs[key]["hp_q32"]=getattr(state,"hp",state)
        incoming.events=logs
        incoming.advance(frame,taunt or {},before_fire=before_fire,interrupt_delay=interrupt_delay)
        for key,escape in w.escapes.items():
            if escape==frame and key in incoming.enemies:
                self.exits[self.exit_index:]=sorted([pair for pair in self.exits[self.exit_index:] if pair[1]!=key]+[(frame,key)])
        if incoming.live_projectiles:
            incoming.advance_arrows(frame,field_scale or (lambda p:1))
        res=dict(self.receiver.resistances)
        res[self.operator]+=_raw_f32(res_bonus)
        for source,holder,loss,kind,details in incoming.damage_events(frame,self.receiver.defenses,res,self.receiver.external):
            if holder!=self.operator:
                unit=incoming.units[holder]
                unit["hp"]=max(0,unit["hp"]-loss)
                logs.append(dict(kind=hit_label or kind+"_hit",incoming_kind=kind+"_hit",frame=frame,source=source,holder=holder,damage_q32=loss,hp_after_q32=unit["hp"]))
                if unit["hp"]<=HP_EPSILON:
                    incoming.withdraw(holder)
                    logs.append(dict(kind="operator_defeated",frame=frame,operator_id=holder))
                continue
            if not self.present:
                continue
            if owner_damage is None:
                self.hp=max(0,self.hp-loss)
            else:
                applied=owner_damage(frame,source,loss,details.get("damage_type"))
                if applied is not None:
                    loss=applied
            incoming.units[holder]["hp"]=self.hp
            logs.append(dict(kind=hit_label or kind+"_hit",incoming_kind=kind+"_hit",frame=frame,source=source,holder=holder,
                             damage_q32=loss,hp_after_q32=self.hp,resistance_q32=res[holder],defense_q32=self.receiver.defenses[holder]))
            if self.hp<=HP_EPSILON:
                self.remove_owner(frame,logs,"operator_defeated")

    def start_walk(self,tables,key,frame,modifier):
        if self.incoming and key in self.incoming.enemies:
            self.incoming.enemies[key]["rerouted"]=True
        self.exits[self.exit_index:]=[pair for pair in self.exits[self.exit_index:] if pair[1]!=key]
        self.wave.escapes.pop(key,None)
        try:
            return ResumedGroundWalk(tables,self,key,frame,modifier,live_checkpoints=True)
        except UnsupportedChannelScene:
            return ControlledRoute(tables,self,key,frame,modifier)

    def step_walk(self,tables,walk,key,frame,modifier,*,slowed=False,cap=None,paused=False):
        if not paused and self.incoming and key in self.incoming.enemies and self.incoming.holding(key,frame):
            return walk
        try:
            moving=walk.advance(self,frame,slowed,speed_cap=cap,paused=paused)
        except UnsupportedChannelScene:
            walk=ControlledRoute(tables,self,key,frame,modifier,prior=walk)
            self.positions[frame][key]=walk.position
            self.active[frame].add(key)
            return walk
        if moving:
            return walk
        self.wave.escapes[key]=frame
        if (frame,key) not in self.exits:
            self.exits[self.exit_index:]=sorted([*self.exits[self.exit_index:],(frame,key)])
        return None

    def settled(self,frame,states,events):
        if self.victory is None and self.all_processed({k:getattr(s,"hp",s) for k,s in states.items()}):
            self._sync_outcome(self.end,events)
            self.receiver.stage_ended(self.end)
            self.victory=self.receiver.victory
        return self.victory is not None and frame>=self.victory

    def _sync_outcome(self,frame,events):
        receiver=self.receiver
        receiver.life=self.life
        receiver.escapes=self.escaped
        receiver.exits={e["instance"]:e["frame"] for e in self.escaped}
        for e in events:
            key=e.get("target")
            if key in self.stats and e.get("hp_after_q32",e.get("hp_q32",HP_EPSILON+1))<=HP_EPSILON and e["frame"]<=frame:
                receiver.deaths.setdefault(key,e["frame"])

    def stage_result(self,result,**options):
        frame=result["through_frame"]
        result["events"]=sorted((e for e in result["events"] if e["frame"]<=frame),key=lambda e:e["frame"])
        self._sync_outcome(frame,result["events"])
        finished=self.receiver.deaths|self.receiver.exits
        result.update(remaining_life=self.life,escapes=self.escaped,holder_hp_q32=self.hp,
            battle_outcome=self.receiver.outcome(frame),wave_schedule_pending=self.schedule["pending"],
            scheduler_release_frames=finished,applied_rules=self.wave.applied_rules(frame,finished))
        return result


def prepare_continuous_ranged(tables,request,operator,releases,*,native_walk=True,deck_only=False):
    if deck_only:
        # prepare_channel needs a position for its range geometry. An off-map card never enters the stage input.
        deploy=dict(operator_id=operator,frame=request["end_frame"]+1,row=0,col=0,direction="RIGHT")
    else:
        deploy=next(a for a in request["actions"] if a.get("kind","deploy")=="deploy" and a["operator_id"]==operator)
    fields=("operator_id","frame","row","col","direction")
    opening={k:v for k,v in request.items() if k in ("stage_id","squad","end_frame","clock","movement","enemy_target_flags","map_random_seed")}
    opening["actions"]=[{k:deploy[k] for k in fields}]
    unique={}
    for a in request["actions"]:
        if a.get("kind","deploy")=="deploy" and a["operator_id"]!=operator:
            unique.setdefault(a["operator_id"],{k:a[k] for k in fields})
    supports=list(unique.values())
    opening["actions"]=sorted([*opening["actions"],*supports],key=lambda a:a["frame"])
    opening["squad"]=[p for p in request["squad"] if p["operator_id"]==operator or p["operator_id"] in unique]
    from .caster_catalog import shared_barrier_panel
    ally_cards={p["operator_id"]:shared_barrier_panel(tables,p) for p in opening["squad"] if p["operator_id"] in unique}
    pick=next(p for p in request["squad"] if p["operator_id"]==operator)
    battle=prepare_channel(tables,opening,operator=operator,skill=pick["skill"],allow_idle=True,allow_no_module=True,
        contact_checked_during_replay=True,completing_waves=releases,allow_partial_movement=True,live_deployment=True,supporting_deploys=supports)
    world=RangedStageTargets(tables,battle,request,battle.card,deploy,native_walk=native_walk,supports=supports,ally_cards=ally_cards)
    grid=battle.scene.level["mapData"]["map"]
    tile=world.tiles[deploy["row"],deploy["col"]]
    view="Back" if deploy["direction"]=="UP" else "Front"
    event=dict(frame=deploy["frame"],end_frame=request["end_frame"],rows=len(grid),cols=len(grid[0]),view=view,
               placement=dict(deploy,tile_z=tile["position"]["z"]-tile["locate_height_offset"]),round_position=True,
               targets=list(world.stats.values()))
    button=next((a for a in request["actions"] if a.get("kind")=="skill"),None)
    event.update(press_frame=button["frame"] if button else None,press_phase=button.get("phase","after_tick") if button else "after_tick")
    return battle.card,battle.opening,deploy,event,world
