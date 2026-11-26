"""Nightmare S2 on an accepted target from an ordinary stage wave, CN V079."""
from math import sqrt

from .baked_mount import single
from .damage import HP_EPSILON
from .enemy_motion import _continued_move, _limits
from .enemy_speed import move_attribute
from .fixed import mul
from .opening_replay import run_opening
from .opening_scene import load_opening_scene
from .route_timing import birth_timer
from .routes import compile_route
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def rupture_packet(previous, current, damage_per_tile):
    # DamageByDistance converts each binary32 reading to FP before subtraction.
    delta = max(0, _raw_f32(current) - _raw_f32(previous))
    return delta, mul(delta, _raw_f32(damage_per_tile)) if delta <= _raw_f32(4) else 0


def _map_distance(a, b):
    dr, dc = (single(x-y) for x,y in zip(a,b))
    return single(sqrt(single(single(dr*dr) + single(dc*dc))))


def run_rupture_route(tables, board, event):
    """Run one already accepted application. The stage supplies the birth, route, speed and HP."""
    start, end = event["frame"], event["end_frame"]
    phase = event["attach_phase"]
    if type(end) is not int or end < start or phase not in ("before_enemy_tick", "after_enemy_tick"):
        raise ValueError("需要有效的截止帧和相对于敌人更新的实际挂载时相")
    stage = event["stage_id"]
    opening = run_opening(dict(stage_id=stage, squad=[], actions=[], end_frame=start, clock=dict(
        fragment_start_frame=event.get("fragment_start_frame",0), cost_input_order="cost_before_input",
        spawn_schedule="native_wave")), tables=tables)
    birth = next((b for b in opening["events"] if b["kind"]=="spawn" and b["instance_id"]==event["instance_id"]), None)
    if birth is None:
        raise ValueError("挂载帧之前的原生波次尚未创建所选实例")
    scene = load_opening_scene(stage_id=stage,tables=tables)
    key = birth["enemy_key"]
    unit = scene.enemies[key]
    attributes = unit["attributes"]
    prefab = unit["prefabKey"]["m_value"] or key
    birth_fields = tables.enemy_birth(prefab)
    timer = birth_timer(birth_fields)
    route = compile_route(scene,birth["route_index"],key)
    steering = tables.enemy_steering(prefab)
    info = dict(stage_id=stage, instance_id=birth["instance_id"], enemy_key=key, born=birth["frame"],
                route=route["source"], attach_frame=start, attach_phase=phase,
                scope="one_already_selected_target_from_full_hp_without_other_combat_effects")
    if (timer["status"]!="conditional_projection" or birth_fields["_idleWhenBorn"] or unit["skills"] or unit["talentBlackboard"]
            or attributes["hpRecoveryPerSec"]["m_value"] or scene.predefines):
        return dict(**info,status="unresolved",reason="requires_enemy_or_stage_effects")
    # Default wave spawns in COROUTINES. Its first enemy OBJECTS update is the following frame.
    first = birth["frame"]+1
    first_move = first+timer["first_default_request_update"]
    if start < first_move:
        raise ValueError("当前创伤样例入口要求敌人已进入普通移动")
    hp = _raw_f32(attributes["maxHp"]["m_value"])
    normal = move_attribute(route["base_move_speed"])
    slow = move_attribute(route["base_move_speed"],ratio_delta=board["move_speed"])
    dt = tables.fixed_timestep()
    step = _raw_f32(dt)
    remaining = _raw_f32(board["duration"])
    pulse = RecoveryTimer(_raw_f32(board["interval"]))
    command = 0
    track = iter(())
    position = tuple(route["start"][a] for a in ("row","col"))
    sampled = position
    velocity = (0.,0.)
    total = 0.
    baseline = None
    log = []
    def result(status, frame, **detail):
        return dict(**info,status=status,through_frame=frame,position=position,total_move_distance=total,
                    hp_after_raw=hp,events=log,**detail)
    def attach(frame):
        log.append(dict(frame=frame,kind="rupture_apply",total_move_distance=total,source="char_164_nightm"))
        return total
    for frame in range(first,end+1):
        if frame==start and phase=="before_enemy_tick":
            baseline = attach(frame)
            track = iter(())
        if frame>=first_move:
            sample = next(track,None)
            if sample is None:
                cmd = route["commands"][command]
                if frame==first_move:
                    reason = _limits(scene,route,steering,cmd,True)
                    if reason:
                        return result("unresolved",frame-1,reason=reason)
                leg = _continued_move(scene,dict(route,move_attribute_q32=slow if baseline is not None else normal),
                    steering,cmd,dict(row=position[0],col=position[1]),velocity,dt,1,round_position=True)
                if leg["status"]!="conditional_projection":
                    return result("unresolved",frame-1,reason=leg["reason"])
                track = iter(leg["samples"])
                sample = next(track)
                arrival = frame+leg["arrival_ticks"]-1
            position,velocity = (sample["row"],sample["col"]),sample["velocity"]
        if baseline is not None:
            # Entity.OnTick runs the MOVE state, then ticks its buffs. The odometer is later in Enemy.OnTick.
            remaining -= step
            due = pulse.advance(step)
            finished = remaining<=0
            if due or finished:
                delta,damage = rupture_packet(baseline,total,board["value"])
                baseline = total
                hp = max(0,hp-damage)
                if hp < HP_EPSILON:
                    hp = 0
                if damage:
                    log.append(dict(frame=frame,kind="rupture_damage",distance_delta_q32=delta,
                                    pure_damage_raw=damage,hp_after_raw=hp))
                if not hp:
                    return result("target_defeated",frame)
                if finished:
                    log.append(dict(frame=frame,kind="rupture_finish"))
                    return result("rupture_finished",frame)
        if (frame-first+1)%5==0:
            total = single(total+_map_distance(position,sampled))
            sampled = position
        if frame==start and phase=="after_enemy_tick":
            baseline = attach(frame)
            track = iter(())
        if frame>=first_move and frame==arrival:
            command += 1
            if command==len(route["commands"]):
                return result("route_end",frame)
            track = iter(())
    return result("through_horizon",end)
