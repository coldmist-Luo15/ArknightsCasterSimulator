"""Native ground wave, hit-mount locations and ordinary target priority shared by ranged casters."""
from .caster_catalog import resolve_caster_loadout
from .battle_outcome import ordinary_battle_outcome
from .damage import HP_EPSILON
from .health_bounds import ordinary_life_points
from .opening_replay import run_opening
from .opening_scene import load_opening_scene
from .projectile_scene import stage_tile_positions
from .stage_channel import (UnsupportedChannelScene,channel_targets,object_position_frame,
                            ordinary_melee_enemy,require_ordinary_highland)
from .target_priority import remaining_route_priority
from .volley_damage import _raw_f32


class MovingTargets:
    def __init__(self,stats,positions,active,tiles,priority,births,deployment_frame,*,scene,schedule,escaped,history):
        self.stats,self.positions,self.active=stats,positions,active
        self.tiles,self.priority=tiles,priority
        self.births,self.deployment_frame=births,deployment_frame
        self.history=history
        self.scene,self.schedule=scene,schedule
        self.exits=sorted((f,k) for k,f in escaped.items())
        self.levitations={}
        self.ground_bounds={}
        self.exit_index=0
        self.escaped=[]
        self.initial_life=self.life=scene.level["options"]["maxLifePoint"]
        self.defeated_at=None
        scheduled={e["instance_id"] for e in schedule["events"] if e["kind"]=="spawn"}
        self.complete_roster=(bool(scheduled) and not schedule["pending"] and
                              len(schedule["waves"])==len(scene.level["waves"]) and scheduled==stats.keys())

    def settle_exits(self,frame,alive):
        while self.life>0 and self.exit_index<len(self.exits):
            f,key=self.exits[self.exit_index]
            if f>frame:
                break
            self.exit_index+=1
            if alive(key):
                loss=self.stats[key]["life_cost"]
                self.life=max(0,self.life-loss)
                self.escaped.append(dict(instance=key,frame=f,scheduler_release_window=[f,f],
                    outcomes=["reached_exit"],life_loss_min=loss,life_loss_max=loss))
                if not self.life:
                    self.defeated_at=f
        return self.defeated_at

    def all_processed(self,hp):
        return self.complete_roster and sum(v>HP_EPSILON for v in hp.values())==len(self.escaped)

    def stage_result(self,result,*,fixed_timestep,target_field,hp_field,projection_horizon=None):
        through=result["through_frame"]
        deaths={e[target_field]:e["frame"] for e in result["events"] if target_field in e and hp_field in e
                and e[hp_field]<=HP_EPSILON}
        self.settle_exits(through,lambda k:k not in deaths)
        if self.defeated_at is not None:
            through=self.defeated_at
            result.update(through_frame=through,stop="stage_defeat")
            result["events"]=[e for e in result["events"] if e["frame"]<=through]
        finishes=[dict(instance=k,first_possible_frame=f,finished_by_frame=f,scheduler_killed_by_frame=f)
                  for k,f in deaths.items() if f<=through]
        horizon=projection_horizon if projection_horizon is not None and result.get("stop")=="all_enemies_processed" else through
        case=dict(through_frame=horizon,finishes=finishes,exits=self.escaped,exit_coverage=True,unresolved=[])
        life=ordinary_life_points(self.initial_life,[case],horizon)
        result.update(remaining_life=self.life,escapes=self.escaped,
                      battle_outcome=ordinary_battle_outcome(self.scene,self.schedule,[case],life,fixed_timestep))
        return result

    def by_id(self,key,frame,*,projectile_born=None):
        tick=object_position_frame(self.births[key],frame,self.positions,self.active,key,
            observer_born=self.deployment_frame if projectile_born is None else projectile_born,
            observer_phase="INPUT" if projectile_born is None else "COROUTINES")
        receiver=self.receiver_at(key,tick,observer_frame=frame)
        if receiver is not None and not self.stats[key].get("airborne",False):
            tile=self.tiles.get((round(receiver["row"]),round(receiver["col"])))
            if not tile or tile["locate_height_offset"] or tile["position"]["z"]!=0:
                raise ValueError("移动靶的路线经过高度变化，需要重取三维受击挂点")
        return receiver

    def receiver_at(self,key,tick,*,observer_frame=None,after_input=False):
        lift=self.levitations.get(key)
        state={}
        if observer_frame is None:
            observer_frame=tick
        if lift is not None:
            start,expires,samples=lift
            state["airborne"]=(observer_frame>start or after_input and observer_frame==start) and tick<expires
            tick,state["height_offset"]=samples.get(tick,(tick,0.))
        if key not in self.active[tick]:
            return None
        row,col=self.positions[tick][key]
        if any((observer_frame>lo or after_input and observer_frame==lo) and observer_frame<hi
               for lo,hi in self.ground_bounds.get(key,())):
            state.update(airborne=False,raw_airborne=self.stats[key].get("airborne",False))
        return dict(self.stats[key],row=row,col=col,**state)

    def snapshot(self,frame,*,projectile_born=None):
        return [e for key in self.stats if (e:=self.by_id(key,frame,projectile_born=projectile_born)) is not None]

    def select(self,rows,frame,*,frame_by_target=None):
        by_key={r["id"]:r for r in rows}
        if frame_by_target is None:
            frame_by_target={key:object_position_frame(self.births[key],frame,self.positions,self.active,key,
                observer_born=self.deployment_frame,observer_phase="INPUT") for key in by_key}
            for key in by_key.keys() & self.levitations.keys():
                tick=frame_by_target[key]
                frame_by_target[key]=self.levitations[key][2].get(tick,(tick,0.))[0]
        selected=self.priority(set(by_key),frame,frame_by_target=frame_by_target)
        if selected is None:
            raise UnsupportedChannelScene(f"F{frame} 的路程优先级无法唯一确定")
        return by_key[selected["target"]]

    def lift_walking_target(self,key,frame,duration,step,end):
        """Insert a levitate buff after the target's object update on a settled walking leg."""
        from .levitate import WalkingLevitate
        if key in self.levitations:
            raise UnsupportedChannelScene("需要重复浮空及异常覆盖的目标状态")
        samples=[row for kind,row in self.history.get(frame,()) if row["instance_id"]==key and kind in
                 ("free_motion_sample","post_wait_motion_sample","exit_motion_sample")]
        if len(samples)!=1 or not samples[0].get("active_ticks") or samples[0].get("leg_end"):
            raise UnsupportedChannelScene(f"F{frame} {key} 在路线节点浮空，需确定节点与异常的先后")
        lift=WalkingLevitate(frame)
        lift.apply(frame,duration,step)
        track={}
        for tick in range(frame+1,end+1):
            lift.advance(tick)
            source,height=lift.samples[tick]
            if any(kind=="checkpoint_begin" and row["instance_id"]==key and row["command"] in
                   ("WAIT_CURRENT_FRAGMENT_TIME","WAIT_CURRENT_WAVE_TIME") for kind,row in self.history.get(source,())):
                raise UnsupportedChannelScene(f"F{tick} 浮空后的路线遇到全局计时检查点")
            track[tick]=(source,height)
        self.levitations[key]=(frame,lift.expires,track)
        self.exits=sorted((f+(lift.expires-frame+1 if target==key else 0),target) for f,target in self.exits)


def ranged_enemy_stats(scene,key,b,unit,allow_flying):
    attr=unit["attributes"]
    record=dict(id=key,prefab=unit["prefabKey"]["m_value"] or b["enemy_key"],tile_z=0,enemy_key=b["enemy_key"],
                        level=scene.enemy_level(b["enemy_key"]),hp=attr["maxHp"]["m_value"],res=attr["magicResistance"]["m_value"],
                        defense=attr["def"]["m_value"],blocked=False,apply_way=unit["applyWay"]["m_value"],
                        life_cost=unit["lifePointReduce"]["m_value"],
                        taunt=attr["tauntLevel"]["m_value"],silence_immune=attr["silenceImmune"]["m_value"],
                        palsy_immune=attr["palsyImmune"]["m_value"],ep_resistance_q32=_raw_f32(attr["epResistance"]["m_value"]),
                        ep_damage_resistance_q32=_raw_f32(attr["epDamageResistance"]["m_value"]),
                        mass_level=attr["massLevel"]["m_value"],levitate_immune=attr["levitateImmune"]["m_value"])
    if allow_flying and unit["motion"]["m_value"]=="FLY":
        record["airborne"]=True
    return record


def load_ranged_wave(tables,request,stage,*,enemy_key="enemy_1007_slime_3",track_exits=False,native_first_walk=False,
                     allow_flying=False,allow_ranged=False,primary_operator=None):
    """Load living ground units, native route distances, tile heights and observer order once."""
    scene=load_opening_scene(stage_id=request["stage_id"],tables=tables)
    action=request["actions"][0]
    placements=[e for e in stage["events"] if e["kind"]=="deployed" and
                (primary_operator is None or e["operator_id"]==primary_operator)]
    if len(placements)!=1 or action["frame"]!=placements[0]["frame"] or scene.predefines or stage["movement"]["unresolved_profiles"]:
        raise UnsupportedChannelScene("需要一次成功部署以及已解析的无阻挡路线")
    deploy=placements[0]
    if enemy_key is None:
        require_ordinary_highland(scene,[deploy])
    start,end=deploy["frame"],request["end_frame"]
    births,_,positions,active,escaped,history=channel_targets(tables,scene,stage,frames=set(range(start-1,end+1)),
        first_born_tick_delay=request["movement"]["first_born_tick_delay_frames"],end=end,with_history=True,
        enemy_key=enemy_key,native_first_walk=native_first_walk)
    stats={}
    spawned={}
    for key,b in births.items():
        if track_exits:
            action=scene.level["waves"][b["wave_index"]]["fragments"][b["fragment_index"]]["actions"][b["action_index"]]
            if (not action["managedByScheduler"] or action.get("useExtraRoute") or action.get("notCountInTotal") or
                    action.get("isUnharmfulAndAlwaysCountAsKilled")):
                raise UnsupportedChannelScene("此出兵动作需要单独核对蓝门入账或波次计数")
        unit=(ordinary_melee_enemy(tables,scene,b["enemy_key"],allow_flying=allow_flying,allow_ranged=allow_ranged) if enemy_key is None
              else scene.enemies[b["enemy_key"]])
        attr=unit["attributes"]
        if unit["skills"] or unit["talentBlackboard"] or attr["hpRecoveryPerSec"]["m_value"] or (key in escaped and not track_exits):
            raise UnsupportedChannelScene("此时窗需要未离场、没有其他被动与回复的普通敌人")
        stats[key]=ranged_enemy_stats(scene,key,b,unit,allow_flying)
        spawned[key]=dict(enemy=b["enemy_key"],route=b["route_index"],spawn_frame=b["frame"],spawn_order=b["order"])
    tiles=stage_tile_positions(tables,scene)
    tile=tiles[deploy["row"],deploy["col"]]
    if tile["locate_height_offset"]:
        raise ValueError("部署点有额外高度修正")
    grid=scene.level["mapData"]["map"]
    event=dict(frame=start,end_frame=end,placement=dict(deploy,tile_z=tile["position"]["z"]),
               rows=len(grid),cols=len(grid[0]),view="Back" if deploy["direction"]=="UP" else "Front",
               round_position=True)
    verified={b["enemy_key"] for b in births.values()} if enemy_key is None else ()
    priority=remaining_route_priority(scene,spawned,history,additional_base_taunt=verified)
    world=MovingTargets(stats,positions,active,tiles,priority,births,start,scene=scene,
                        schedule=stage["wave_schedule"],escaped=escaped,history=history)
    return deploy,event,world


def prepare_ranged_stage(tables,request,*,operator,manual,portable=False,track_exits=False,simulate_before_button=False,
                         native_first_walk=False,allow_flying=False,allow_ranged=False,supporting_deploys=()):
    pick, = (p for p in request["squad"] if p["operator_id"]==operator)
    card = resolve_caster_loadout(tables,pick["operator_id"],pick["skill"],pick["module"],
                                  skill_level=pick["skill_level"],module_level=pick["module_level"])
    if ((not portable and request["stage_id"]!="sub_03-3-1") or card["operator_id"]!=operator or
            any(pick[k]!=card[k] for k in ("elite","level","potential"))):
        raise ValueError("本波次需要相符的关卡和装备常规模组的满练满潜干员")
    deploy,*buttons = request["actions"]
    if len(buttons)!=int(manual):
        raise ValueError("此配置需要部署和正确数量的手动按键")
    if buttons:
        button,=buttons
        if (button!=dict(kind="skill",operator_id=card["operator_id"],frame=button["frame"],phase="after_tick")
                or not deploy["frame"]<button["frame"]<=request["end_frame"]):
            raise ValueError("需要部署之后、观察窗内的 after_tick 技能按键")
    opening = dict(request,actions=sorted([deploy,*supporting_deploys],key=lambda a:a["frame"]),track_attack_range=True)
    opening.pop("scene_model",None)
    opening.pop("battle_random_seed",None)
    opening.pop("eyja_deployment_seed",None)
    stage = run_opening(opening,tables=tables)
    placed,event,world = load_ranged_wave(tables,request,stage,enemy_key=None if portable else "enemy_1007_slime_3",
                                          track_exits=track_exits,native_first_walk=native_first_walk,allow_flying=allow_flying,
                                          allow_ranged=allow_ranged,primary_operator=operator if supporting_deploys else None)
    if any((e["apply_way"]!="MELEE" and not (allow_flying and e.get("airborne") and e["apply_way"]=="NONE")
           and not (allow_ranged and e["apply_way"]=="RANGED"))
           or e["taunt"] for e in world.stats.values()):
        raise UnsupportedChannelScene("此无阻挡局部波次需要默认嘲讽的普通近战敌人")
    event.update(targets=list(world.stats.values()))
    if buttons:
        if not simulate_before_button and any(e["kind"]=="attack_range_enter" and e["frame"]<=button["frame"] for e in stage["events"]):
            raise UnsupportedChannelScene("开技前有敌人进入普攻范围，需要先结算普通攻击和可能的Y模组回技")
        event.update(press_frame=button["frame"],press_phase=button["phase"])
    return card,stage,placed,event,world
