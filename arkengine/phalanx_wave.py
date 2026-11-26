"""Object-phase searches, spell-phase snapshots and PLX-Y contacts for an ordinary wave."""
from math import floor,hypot
from .enemy_motion import ROUTE_REACH_DISTANCE,_straight_walk,advance_direct_move,advance_wait_coast,_hidden_sequence
from .enemy_speed import move_attribute,movement_cap
from .routes import compile_route
from .route_timing import TIMED_WAITS,checkpoint_sequence,route_clock_snapshots
from .attack_range import circle_in_range
from .lin_output_aura import trigger_contact
from .stage_channel import UnsupportedChannelScene,channel_targets,object_position_frame,ordinary_melee_enemy,require_ordinary_highland
from .target_priority import remaining_route_priority
from .volley_damage import _raw_f32


def run_with_phalanx_waves(simulate,tables,request):
    """Reschedule later ordinary waves using finish callbacks from the admitted combat."""
    clock={"stage_played_state":"THREE_STAR",**request["clock"]}
    requested=request.get("end_frame")
    horizon=requested if requested is not None else max(1800,clock["fragment_start_frame"],
                *(a.get("frame",0) for a in request["actions"]))
    releases={}
    while True:
        result=simulate(tables,dict(request,clock=clock,end_frame=horizon),releases)
        pending=result.get("wave_schedule_pending") or {}
        if pending.get("reason")=="wave_enemy_release_frames_unknown":
            finished=result["scheduler_release_frames"]
            updates={key:finished[key] for key in pending["instances"] if key in finished and key not in releases}
            # Once the native timeout has elapsed, survivors cannot hold this
            # barrier any longer. Their actual escape/kill callbacks remain live.
            latest=pending.get("latest_fragment_start_frame")
            if latest is not None and latest<=result["through_frame"]:
                updates.update({k:latest for k in pending["instances"] if k not in releases and k not in updates})
            if updates:
                releases.update(updates)
                continue
        if requested is not None or result["stop"]!="horizon":
            return result
        horizon*=2


class PhalanxWave:
    def __init__(self,battle,through,*,allow_ranged=False,allow_flying=False,allow_thrower=False,
                 native_first_walk=False,allow_later_waves=False,first_sample=None,absolute_route_times=False,
                 allow_partial_movement=False):
        self.scene=battle.scene
        self.start=battle.deploy["frame"]
        self.equipped=bool(battle.card["module"])
        self.cells=set(battle.geometry["cells"])
        schedule=battle.opening["wave_schedule"] if absolute_route_times else None
        self.route_clocks=route_clock_snapshots(self.scene.level,schedule,battle.tables.fixed_timestep()) if schedule else {}
        self.continuous=allow_partial_movement
        if not self.continuous:
            require_ordinary_highland(battle.scene,[battle.deploy])
        self.births,self.radii,self.positions,self.active,self.escapes,self.history=channel_targets(
            battle.tables,battle.scene,battle.opening,
            frames=range((self.start if first_sample is None else first_sample)-1,through+1),end=through,with_history=True,
            first_born_tick_delay=battle.request["movement"]["first_born_tick_delay_frames"],enemy_key=None,
            native_first_walk=allow_ranged or native_first_walk,allow_later_waves=allow_later_waves,route_clocks=self.route_clocks,
            allow_partial_movement=allow_partial_movement)
        self.assumptions=[]
        self.flags=None
        if self.continuous:
            from .enemy_target_flags import EnemyTargetFlags
            from .lin_incoming import ordinary_ranged
            self.flags=EnemyTargetFlags(battle.tables,self.scene,self.births,battle.request.get("enemy_target_flags",()))
            for event in schedule["events"]:
                if event["frame"]>through:
                    continue
                if event["kind"]=="stage_action":
                    action=self.scene.level["waves"][event["wave_index"]]["fragments"][event["fragment_index"]]["actions"][event["action_index"]]
                    self.assumptions.append(dict(kind="rule_applied",frame=event["frame"],target=action["key"],
                        action=action["actionType"],rule="advance_stage_action_on_battle_clock",source=event["source"]))
                elif event["kind"]=="story_skipped":
                    self.assumptions.append(dict(kind="rule_applied",frame=event["frame"],target=event["story_key"],
                        rule="skip_story_presentation_on_battle_clock"))
                elif event["kind"]=="spawn":
                    action=self.scene.level["waves"][event["wave_index"]]["fragments"][event["fragment_index"]]["actions"][event["action_index"]]
                    if action.get("randomSpawnGroupKey") or action.get("randomSpawnGroupPackKey"):
                        self.assumptions.append(dict(kind="rule_applied",frame=event["frame"],target=event["instance_id"],
                            rule="weighted_stage_group_and_linked_pack",seed=schedule["battle_random_seed"]))
            for key,birth in self.births.items():
                enemy=self.scene.enemies[birth["enemy_key"]]
                if enemy["skills"] or enemy["applyWay"]["m_value"]=="RANGED" and not ordinary_ranged(self.scene,birth["enemy_key"]):
                    self.assumptions.append(dict(kind="rule_applied",frame=birth["frame"],target=key,
                        enemy=birth["enemy_key"],rule="base_receiver_and_route_without_special_enemy_actions"))
        self.attrs={}
        self.exits=sorted((frame,key) for key,frame in self.escapes.items())
        self.exit_index=0
        ordinary={enemy:self.scene.enemies[enemy] if self.continuous else ordinary_melee_enemy(battle.tables,battle.scene,enemy,allow_ranged=allow_ranged,
                                            allow_flying=allow_flying,allow_thrower=allow_thrower)
                  for enemy in {birth["enemy_key"] for birth in self.births.values()}}
        self._rank=None
        self._ordinary_types=ordinary.keys()
        for key,birth in self.births.items():
            unit=ordinary[birth["enemy_key"]]
            attr=unit["attributes"]
            self.attrs[key]=dict(radius=self.radii[key],eligible=True,ground=unit["motion"]["m_value"]=="WALK",
                                 immune_to_induction=attr["attractImmune"]["m_value"],
                                 hp_q32=_raw_f32(attr["maxHp"]["m_value"]),resistance_q32=_raw_f32(attr["magicResistance"]["m_value"]))

    def targetable(self,key,frame):
        return self.flags is None or not self.flags.at(key,frame).intersection(
            ("TARGET_FREE","INVINCIBLE","INVISIBLE","TOWER_TARGET_FREE"))

    def applied_rules(self,through,releases):
        applied=[e for e in self.assumptions if e["frame"]<=through]
        seen=set()
        for frame,rows in self.history.items():
            if frame>through:
                continue
            for kind,row in rows:
                if kind!="route_rule" or frame>releases.get(row["instance_id"],through):
                    continue
                identity=row["instance_id"],row["frame"],row["source"],row["rule"]
                if identity not in seen:
                    applied.append(dict(row,frame=frame))
                    seen.add(identity)
        return applied

    def snapshot(self,frame):
        return {k:dict(self.attrs[k],row=self.positions[frame][k][0],col=self.positions[frame][k][1])
                for k in sorted(self.active[frame]) if self.attrs[k]["hp_q32"]>0 and self.targetable(k,frame)}

    def unresolved_motion(self,frame):
        return next((dict(enemy=row["instance_id"],route_source=row.get("source"),reason=row.get("reason",kind))
                     for kind,row in self.history.get(frame,()) if kind in ("range_motion_unresolved","birth_timer_unresolved")
                     and self.attrs[row["instance_id"]]["hp_q32"]>0),None)

    def search(self,frame,*,cells=None,observer_born=None):
        cells=self.cells if cells is None else cells
        observer_born=self.start if observer_born is None else observer_born
        chosen=[]
        for k,b in self.births.items():
            tick=object_position_frame(b,frame,self.positions,self.active,k,observer_born=observer_born,observer_phase="INPUT")
            if (k in self.active[tick] and self.attrs[k]["hp_q32"]>0 and self.targetable(k,frame) and
                    circle_in_range(*self.positions[tick][k],self.radii[k],cells,box_query=True)):
                chosen.append(k)
        return sorted(chosen)

    def attack_order(self,keys,frame,*,after_objects=False,observer_born=None):
        """Order the saved targets of an unrestricted HATRED_DES selector."""
        observer_born=self.start if observer_born is None else observer_born
        if self._rank is None:
            origins={key:dict(enemy=birth["enemy_key"],route=birth["route_index"],
                              spawn_frame=birth["frame"],spawn_order=birth["order"])
                     for key,birth in self.births.items()}
            self._rank=remaining_route_priority(self.scene,origins,self.history,additional_base_taunt=self._ordinary_types)
        positions={key:frame if after_objects else object_position_frame(self.births[key],frame,self.positions,self.active,key,
                   observer_born=observer_born,observer_phase="INPUT") for key in keys}
        remaining=set(keys)
        order=[]
        while len(remaining)>1:
            ranked=self._rank(remaining,frame,frame_by_target=positions)
            if ranked is None:
                if not self.continuous:
                    return None
                def remaining_distance(key):
                    end=self.scene.level["routes"][self.births[key]["route_index"]]["endPosition"]
                    row,col=self.positions[positions[key]][key]
                    taunt=self.scene.enemies[self.births[key]["enemy_key"]]["attributes"]["tauntLevel"]["m_value"]
                    return -(taunt or 0),hypot(row-end["row"],col-end["col"]),self.births[key]["order"]
                order.extend(sorted(remaining,key=remaining_distance))
                self.assumptions.append(dict(kind="rule_applied",frame=frame,targets=order.copy(),
                    rule="distance_to_exit_then_spawn_order_at_unresolved_route_tie"))
                return order
            key=ranked["target"]
            order.append(key)
            remaining.remove(key)
        return [*order,*remaining]

    def aura_members(self,frame):
        """Require definite and unchanged contacts around the completed physics update."""
        if not self.equipped:
            return []
        memberships=[]
        for f in range(frame-2,frame+1):
            contacts={k:trigger_contact(self.positions[f][k],self.radii[k],self.cells) for k in self.active[f] if self.attrs[k]["hp_q32"]>0}
            if None in contacts.values():
                return None
            memberships.append({k for k,v in contacts.items() if v})
        if memberships[0]==memberships[1]==memberships[2]:
            return sorted(memberships[-1])
        return None

    def spawns(self,through):
        return {k:dict(frame=b["frame"],enemy=b["enemy_key"],route=b["route_index"]) for k,b in self.births.items() if b["frame"]<=through}


def resume_move_leg(tables,scene,wave,key,hit,*,already_retimed=False):
    """Recover position, velocity and goal from an ordinary wave before changing its speed."""
    birth=wave.births[key]
    route=compile_route(scene,birth["route_index"],birth["enemy_key"])
    rows=[r for _,r in wave.history.get(hit,()) if r["instance_id"]==key]
    if len(rows)!=1 or rows[0].get("leg_end"):
        return None
    row,=rows
    goal=next((leg.get("target") for leg in route["commands"] if leg["source"]==row.get("leg_source",route["commands"][0]["source"])),None)
    if not goal or row.get("active_ticks",0)<=0:
        return None
    target=goal["row"],goal["col"]
    normal=movement_cap(move_attribute(route["base_move_speed"]),route["level_move_multiplier"])
    dt=tables.fixed_timestep()
    unit=scene.enemies[birth["enemy_key"]]
    steering=tables.enemy_steering(unit["prefabKey"]["m_value"] or birth["enemy_key"])
    pos=wave.positions[hit][key]
    velocity=row.get("velocity")
    if velocity is None:
        prior=wave.positions.get(hit-1,{}).get(key)
        if not already_retimed or prior is None or "speed" not in row:
            return None
        velocity=tuple((cur-old)/dt for cur,old in zip(pos,prior))
    # The shared route solver already validated the original leg and its geometry. Match the
    # untouched next update before replacing it, to catch hidden WAIT/turn/offset transitions.
    if not already_retimed:
        control=advance_direct_move(pos,velocity,target,normal,steering,dt,True)
        if not control or control[0]!=wave.positions.get(hit+1,{}).get(key):
            return None
    return route,target,normal,steering,pos,velocity


class ResumedGroundWalk:
    """Resume a speed-modified ground or aerial route from its live steering cursor."""

    def __init__(self, tables, world, key, frame, ratio, *, already_retimed=False, live_checkpoints=False):
        prior = resume_move_leg(tables, world.scene, world, key, frame, already_retimed=already_retimed)
        self.key=key
        self.live_checkpoints=live_checkpoints
        self.checkpoint=None
        self.dt = tables.fixed_timestep()
        birth=world.births[key]
        self.clocks=getattr(world,"route_clocks",{}).get((birth.get("wave_index"),birth.get("fragment_index")))
        if prior is not None:
            self.route,self.goal,self.normal,self.steering,self.position,self.velocity=prior
            (self.kind,self.sample),=(e for e in world.history[frame] if e[1]["instance_id"]==key)
            commands=self.route["commands"]
            source=self.sample.get("leg_source",commands[0]["source"])
            self.index=next(i for i,c in enumerate(commands) if c["source"]==source)
        elif live_checkpoints:
            self._resume_checkpoint(tables,world,frame)
        else:
            raise UnsupportedChannelScene(f"F{frame} {key} 改速时需恢复路线节点或等待游标")
        self.slow = movement_cap(move_attribute(self.route["base_move_speed"], ratio_delta=ratio),
                                 self.route["level_move_multiplier"])
        world.exits = [pair for i,pair in enumerate(world.exits) if i < world.exit_index or pair[1] != key]

    def _resume_checkpoint(self,tables,world,frame):
        key=self.key
        birth=world.births[key]
        self.route=compile_route(world.scene,birth["route_index"],birth["enemy_key"])
        commands=self.route["commands"]
        move_kinds={"free_motion_sample","post_wait_motion_sample","exit_motion_sample"}
        records=[(kind,row) for kind,row in world.history.get(frame,())
                 if row["instance_id"]==key and kind in move_kinds and "velocity" in row]
        if len(records)!=1 or key not in world.active[frame]:
            raise UnsupportedChannelScene(f"F{frame} {key} 改速时缺少路线节点的速度样本")
        (self.kind,self.sample),=records
        self.position=world.positions[frame][key]
        self.velocity=tuple(self.sample["velocity"])
        self.normal=movement_cap(move_attribute(self.route["base_move_speed"]),self.route["level_move_multiplier"])
        unit=world.scene.enemies[birth["enemy_key"]]
        self.steering=tables.enemy_steering(unit["prefabKey"]["m_value"] or birth["enemy_key"])
        source=self.sample.get("leg_source",commands[0]["source"])
        self.index=next(i for i,c in enumerate(commands) if c["source"]==source)
        if self.sample.get("leg_end") and self.index+1<len(commands):
            self.index+=1
            self._enter_next(world,frame,record_start=False)
            return
        if commands[self.index]["kind"] in TIMED_WAITS:
            for origin in range(frame,birth["frame"],-1):
                start=next((row for kind,row in world.history.get(origin,()) if kind=="checkpoint_begin"
                            and row["instance_id"]==key and row["source"]==source),None)
                if start:
                    self._enter_next(world,frame,record_start=False,checkpoint_origin=start["position"],checkpoint_started=origin)
                    self.checkpoint["elapsed"]=frame-origin
                    if self.checkpoint["elapsed"]>=self.checkpoint["sequence"]["next_offset"]:
                        following=self.checkpoint["sequence"]["next_command"]
                        self.checkpoint=None
                        self.index=commands.index(following)
                        self._enter_next(world,frame,record_start=False)
                    return
        raise UnsupportedChannelScene(f"F{frame} {key} 改速时需核对路线节点当前时钟")

    def advance(self, world, frame, slowed, *, speed_cap=None, paused=False, temporary_goal=None,
                forced_position=None, navigation=None):
        cap = speed_cap if speed_cap is not None else self.slow if slowed else self.normal
        if self.checkpoint is not None:
            self._advance_checkpoint(world,frame,cap,paused=paused)
            return True
        goal = temporary_goal if temporary_goal is not None else self.goal
        if forced_position is not None:
            self.position, self.velocity = forced_position, (0.,0.)
        elif not paused:
            target=goal
            if navigation is not None:
                cell=tuple(floor(v+.5) for v in self.position)
                distances,following=navigation.get_map(goal,self.route["flags"]["allowDiagonalMove"] or temporary_goal is not None)
                if cell not in distances:
                    raise UnsupportedChannelScene(f"F{frame} {self.key} 无法到达行动地块 {goal}")
                target=following.get(cell,goal)
            if temporary_goal is None or hypot(self.position[0]-goal[0],self.position[1]-goal[1])>ROUTE_REACH_DISTANCE:
                self.position, self.velocity = advance_direct_move(
                    self.position, self.velocity, target, cap, self.steering, self.dt, True)
        reached=(not paused and forced_position is None and temporary_goal is None and
                 hypot(self.position[0]-goal[0],self.position[1]-goal[1])<=ROUTE_REACH_DISTANCE)
        key = self.key
        row, col = self.position
        world.positions[frame][key] = self.position
        world.active[frame].add(key)
        history = [(k,p) for k,p in world.history.get(frame, ()) if p["instance_id"] != key]
        self.sample = dict(self.sample, row=row, col=col, velocity=self.velocity,
                           active_ticks=self.sample["active_ticks"]+1, leg_end=reached)
        world.history[frame] = [*history, (self.kind, self.sample)]
        if not reached:
            return True
        self.index += 1
        commands = self.route["commands"]
        if self.index == len(commands):
            world.active[frame].discard(key)
            world.exits = world.exits[:world.exit_index] + sorted([*world.exits[world.exit_index:], (frame, key)])
            return False
        self._enter_next(world,frame,navigation)
        return True

    def _enter_next(self, world, frame, navigation=None, *, record_start=True, checkpoint_origin=None,checkpoint_started=None):
        command = self.route["commands"][self.index]
        kind = command["kind"]
        if self.live_checkpoints and kind in ("DISAPPEAR",*TIMED_WAITS):
            position=checkpoint_origin or dict(zip(("row","col"),self.position))
            sequence=checkpoint_sequence(self.route["commands"],self.index,position,timestep=self.dt,clocks=self.clocks,
                                         clock_frame=frame if checkpoint_started is None else checkpoint_started)
            entries=[e for e in sequence["events"] if e["event"]=="checkpoint_begin"]
            hidden=_hidden_sequence(sequence)
            if ((hidden and any(v!=round(v) for v in entries[-1]["position"].values()))
                    or (not hidden and any(e["command"] not in TIMED_WAITS for e in entries))
                    or sequence["next_command"]["kind"] not in ("MOVE","END")):
                raise UnsupportedChannelScene(f"F{frame} {self.key} 的检查点序列需要进一步核对")
            self.checkpoint=dict(elapsed=0,sequence=sequence,hidden=hidden,
                                 cell=tuple(round(position[k]) for k in ("row","col")))
            if record_start:
                self._advance_checkpoint(world,frame,self.normal,first=True)
            return
        row,col=self.position
        origin = dict(row=floor(row+.5),col=floor(col+.5))
        if (kind not in ("MOVE","END") or command.get("randomize_reach_offset") or
                command.get("reach_distance") or any(command.get("reach_offset",{}).values()) or
                navigation is None and _straight_walk(world.scene, self.route, origin, command, allow_same_cell=True)):
            raise UnsupportedChannelScene(f"F{frame} {self.key} 改速后遇到未结算的路线节点")
        self.goal = command["target"]["row"], command["target"]["col"]
        self.kind = "exit_motion_sample" if kind == "END" else "post_wait_motion_sample"
        self.sample = dict(self.sample, active_ticks=0, leg_source=command["source"])

    def _advance_checkpoint(self, world, frame, cap, *, first=False, paused=False):
        state=self.checkpoint
        sequence=state["sequence"]
        if not first:
            state["elapsed"]+=1
        offset=state["elapsed"]
        key=self.key
        following=sequence["next_command"]
        history=world.history.setdefault(frame,[])
        if not first:
            history[:]=[(k,r) for k,r in history if r["instance_id"]!=key]
            if not state["hidden"]:
                if not paused:
                    self.position,self.velocity=advance_wait_coast(self.position,self.velocity,cap,self.steering,self.dt)
                goal=following["target"]
                if (tuple(map(round,self.position))!=state["cell"]
                        or hypot(self.position[0]-goal["row"],self.position[1]-goal["col"])<=ROUTE_REACH_DISTANCE):
                    raise UnsupportedChannelScene(f"F{frame} {key} 等待时跨格或触及下一检查点")
                history.append(("free_motion_sample",dict(self.sample,row=self.position[0],col=self.position[1],
                    velocity=self.velocity,active_ticks=offset,leg_source=sequence["events"][0]["source"],leg_end=False)))
        for event in sequence["events"]:
            if event["offset"]!=offset:
                continue
            if event["event"]=="checkpoint_begin" and event["command"]=="APPEAR_AT_POS":
                self.position=tuple(event["position"][axis] for axis in ("row","col"))
            row={k:v for k,v in event.items() if k not in ("event","offset","position")}
            history.append((event["event"],dict(row,instance_id=key,position=dict(zip(("row","col"),self.position)))))
        world.positions[frame][key]=self.position
        if state["hidden"]:
            world.active[frame].discard(key)
        else:
            world.active[frame].add(key)
        if offset==sequence["next_offset"]:
            self.checkpoint=None
            self.index=self.route["commands"].index(following)
            self._enter_next(world,frame)


def apply_sluggish_walk(battle,wave,key,hit,ticks,end):
    """Continue the same raw MOVE leg at the two speed caps, up to its checkpoint callback."""
    t=battle.tables
    leg=resume_move_leg(t,battle.scene,wave,key,hit)
    if not leg:
        return hit+1
    route,target,normal,steering,pos,velocity=leg
    slow=t.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"]
    if len(slow)!=1 or (slow[0]["attributeType"],slow[0]["formulaItem"],slow[0]["loadFromBlackboard"])!=("MOVE_SPEED","FINAL_SCALER",False):
        raise ValueError("停顿移速修正定义已变化")
    reduced=movement_cap(move_attribute(route["base_move_speed"],ratio_delta=slow[0]["value"]),route["level_move_multiplier"])
    dt=t.fixed_timestep()
    wave.escapes.pop(key,None)
    (kind,sample),=(e for e in wave.history[hit] if e[1]["instance_id"]==key)
    for f in range(hit+1,end+1):
        cap=reduced if f<=hit+ticks else normal
        if hypot(pos[0]-target[0],pos[1]-target[1])<=max(ROUTE_REACH_DISTANCE,cap*dt):
            return f
        pos,velocity=advance_direct_move(pos,velocity,target,cap,steering,dt,True)
        wave.positions[f][key]=pos
        wave.active[f].add(key)
        wave.history[f]=[e for e in wave.history.get(f,()) if e[1]["instance_id"]!=key]
        wave.history[f].append((kind,dict(sample,row=pos[0],col=pos[1],velocity=velocity,speed=hypot(*velocity),
                                         active_ticks=sample["active_ticks"]+f-hit,leg_end=False)))
    return None
