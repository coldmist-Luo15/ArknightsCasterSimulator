"""Frame-driven Qanipalaat S1 battle against stationary ordinary targets in a local fixture.

The training board supplies geometry, fixed receiver mounts, spawn times, current
route priority and starting HP. It has no opposing attacks, pathing or external buffs.
The policy uses the manual skill once at the first ready idle window after UPDATE.
"""
from .animation_assets import read_skin_actions
from .attack_range import circle_in_range, range_cells
from .baked_mount import baked_position, single, transform_point
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_prefabs import caster_prefab_evidence
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_projectiles import ranged_shot_release,stationary_cast_mounts
from .five_star_skills import QANIK, _flag, _integer, _number, _targets, qanik_priority_group
from .fixed import ONE
from .levitate import changeable_airborne
from .lin_output_aura import trigger_contact
from .mystic_cycle import baked_key_sample
from .mystic_projectiles import _ordinary_mover
from .projectile_scene import projectile_profiles
from .qanik_aura import QanikAura, fragility_profile
from .skill_recovery import deploy_skill
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def _timing(tables, loadout, facing):
    uid = loadout["operator_id"]
    ev = caster_prefab_evidence(tables, uid, 1, skill_level=loadout["skill_level"],
                                module_id=loadout["module"], module_level=loadout["module_level"])
    cast = run_committed_cast(tables, ev, horizon=10000, facing=facing)
    clock = cast.get("natural_end")
    if not clock or not cast["blocks_sp_while_affecting"] or clock["spell_frame"] != 1:
        raise ValueError("冻风的施法与阻回时钟无法绑定")
    roots=[c["fields"] for c in ev["skill"]["components"] if not c["path"] and c["active"] and c["enabled"]]
    controller, = [f for f in roots if "_switchToState" in f]
    buff_ability, = [f for f in roots if "_waitForCasting" in f]
    switch = tables.read("battle/buff_template_data.json")["switch_mode_after_attack"]["eventToActions"]
    if (set(switch) != {"ON_AFTER_ATTACK", "ON_BUFF_FINISH"}
            or controller["_switchToState"] or controller["_playSkillBeginAnim"] or buff_ability["_waitForCasting"]
            or not switch["ON_AFTER_ATTACK"][0]["_loadModeFromBlackboard"]
            or not switch["ON_BUFF_FINISH"][0]["_restoreDefault"]
            or any(actions[0]["_restartFSM"] for actions in switch.values())):
        raise ValueError("冻风后的模式切换时机发生变化")
    character = ev["character"]["components"]
    birth = tables.character_birth(uid)
    if birth["_useRealBornTimeFromAnim"] or birth["m_overrideBornTime"] != -1:
        raise ValueError("出生时间发生变化")
    modes,abilities = {},{}
    for mode in ("Default", "S1"):
        path = f"Modes/{mode}/Body/Attack"
        attack, = [c["fields"] for c in character if c["path"] == path and c["script"] == "RangedAttack"]
        trigger, = [c["fields"] for c in character if c["path"] == path+"/Selector" and c["script"] == "SelectorTrigger"]
        selector_type = "AdvancedSelector" if mode == "Default" else "SecondaryFilterAdvancedSelector"
        selector, = [c["fields"] for c in character if c["path"] == path+"/Selector" and c["script"] == selector_type]
        if (any(selector[k] != v for k,v in {"_targetSide":2,"_targetMotion":3,"_targetCategory":1,
                "_postFilter":4,"_limitTargetNum":1,"_maxNum":1,"_ignoreHitRange":0}.items())
                or mode == "S1" and selector["_secondaryFilter"] != 0
                or trigger["_keepTarget"] or trigger["_minTargetNum"] != 1):
            raise ValueError("雪绒的普攻目标过滤、数量或目标保持配置发生变化")
        if (not attack["_waitForAttackEvent"] or attack["_escapeTime"] or attack["_waitForProjectileInvalid"]
                or attack["_interruptAbilityOnDetach"]
                or attack["_preDelay"] or attack["_resetCdStrategy"] != 1 or attack["_animKey"] != "Attack"
                or attack["_mountPointType"] != 2 or trigger["_overrideSearchTargetTick"] != -1):
            raise ValueError("冻风攻击需要独立时钟")
        modes[mode] = attack["_projectileKey"]
        abilities[mode] = attack
    flight = projectile_profiles(tables, modes.values())
    if not all(_ordinary_mover(v) and v["has_only_trace_hit"] for variants in flight.values() for v in variants.values()):
        raise ValueError("弹道到达与结算条件未覆盖")
    decoded = read_skin_actions(tables, f"chararts/{uid}.ab", "defaultskin", ("Born","Attack"), mounts=[(2,"muzzlaTransform")])
    view = decoded["views"][facing.lower()]
    clip = view["actions"]["Attack"]
    baked = tables.read(f"bakemuzzledata/character/{uid}_defaultskin.json")["bakedAnimDatas"][facing]
    track = baked["bakedEvents"][clip["clip"]]
    if clip["loop"] or clip["speed"] != 1 or track["events"] != ["OnAttack"]:
        raise ValueError("攻击烘焙关键帧不唯一")
    graphic = tables._component_fields("characters",uid,("m_LocalPosition","m_LocalRotation","m_LocalScale"),
                                      "qanik_graphic",component_type="Transform",children=("Graphic",))
    parents = [*view["mount_parents"][2],dict(position=graphic["m_LocalPosition"],
               rotation=graphic["m_LocalRotation"],scale=graphic["m_LocalScale"])]
    return dict(modes=modes,abilities=abilities,evidence=ev,clock=clock,duration=clip["duration"],key=track["eventTime"][0],
                parents=parents, mount=baked["bakedMPDatas"][clip["clip"]]["2"])


def run_qanik_battle(tables, loadout, scene, *, world=None,ordinary_only=False,continuation=None):
    if loadout["operator_id"] != QANIK or loadout["skill"] != (2 if ordinary_only else 1):
        raise ValueError("此入口需要雪绒一技能")
    start = _integer(scene, "frame")
    end = _integer(scene, "end_frame")
    if not start < end <= start + 18000:
        raise ValueError("靶场需要有效的帧区间（最多十分钟）")
    placement = scene["placement"]
    facing = placement["spine_view"]
    rows, cols = _integer(scene, "rows"), _integer(scene, "cols")
    if not (0 <= _integer(placement,"row") < rows and 0 <= _integer(placement,"col") < cols):
        raise ValueError("部署位置超出场地")
    assets = _timing(tables, loadout, facing)
    cells = range_cells(loadout["base_range"], placement, rows, cols)
    enemies, hp = {}, {}
    for row in _targets(scene):
        if world is not None:
            key=row["id"]
            motion = world.scene.enemies[row["enemy_key"]]["motion"]["m_value"]
            if motion not in ("WALK", "FLY"):
                raise ValueError("该敌人的初始行动方式需要单独解析")
            enemies[key]=dict(spawn=world.births[key]["frame"],air=motion=="FLY",level=row["level"],
                              res=_raw_f32(row["res"]),radius=tables.enemy_circle(row["prefab"]))
            hp[key]=_raw_f32(row["hp"])
            continue
        pos = (_number(row,"row"),_number(row,"col"))
        if not (0 <= pos[0] < rows and 0 <= pos[1] < cols) or row["level"] not in ("NORMAL","ELITE","BOSS"):
            raise ValueError("靶标位置或敌人等级无效")
        if not isinstance(row["hit_offset"], list) or len(row["hit_offset"]) != 3:
            raise ValueError("靶标的三维受击挂点偏移无效")
        mount = tuple(_number(dict(v=x),"v",minimum=-1000) for x in row["hit_offset"])
        key = row["id"]
        health = _raw_f32(_number(row, "hp"))
        if health <= HP_EPSILON:
            raise ValueError("靶标初始生命必须为正")
        airborne = changeable_airborne(_flag(row,"airborne"),row.get("levitate_sequence",0),row.get("ground_bound_sequence",0))
        radius = _number(row,"radius")
        enemies[key] = dict(spawn=_integer(row,"spawn"), air=airborne, rank=_number(row,"route_remaining"),
                            level=row["level"], res=_raw_f32(_number(row,"res",minimum=-1000)),
                            col=pos[1], position=pos, radius=radius, mount=(pos[1]+mount[0],pos[0]+mount[1],mount[2]),
                            in_range=circle_in_range(*pos,radius,cells,box_query=True))
        hp[key] = health
    if not enemies and world is None:
        raise ValueError("靶场至少需要一名靶标")
    if world is None and len({e["rank"] for e in enemies.values()}) != len(enemies):
        raise ValueError("等嘲讽的静止靶标需要可区分的剩余路线距离")
    stats = loadout["panel_attributes"]
    aura_profile=fragility_profile(tables,loadout,assets["evidence"])
    auras={key:QanikAura(aura_profile) for key,e in enemies.items()
           if e["air"] or world is not None and key in world.levitations}
    elite_sp=int(_trait_values(loadout).get("sp",0))
    board = {b["key"]:b["value"] for b in loadout["skill_row"]["blackboard"]}
    dt = tables.fixed_timestep()
    gauge, _ = deploy_skill(tables, loadout, start)
    state = gauge.state
    shots, log = [], []
    first_update=start
    if continuation is not None:
        hp.update(continuation["hp"])
        auras.update(continuation["auras"])
        state.points=continuation["sp"]
        shots=continuation["shots"]
        first_update=continuation["first_update"]
    def emit(kind, **fields):
        log.append(dict(frame=frame, kind=kind, **fields))

    def update_auras():
        retry=(frame-start-1)%10==0
        for key,aura in auras.items():
            if hp[key]<=HP_EPSILON or frame<=enemies[key]["spawn"] or frame<=start:
                continue
            if world is None:
                aura.tick(gauge.step,enemies[key]["air"])
                continue
            row=world.receiver_at(key,frame)
            if row is None:
                continue
            airborne=row.get("airborne",enemies[key]["air"])
            if retry and start<enemies[key]["spawn"]:
                observed=world.by_id(key,frame)
                aura.retry(bool(observed and observed.get("airborne",enemies[key]["air"])))
            aura.tick(gauge.step,airborne)
            if retry and start>=enemies[key]["spawn"]:
                aura.retry(airborne)

    def update_contacts():
        for key,aura in auras.items():
            enemy=enemies[key]
            if hp[key]<=HP_EPSILON or frame<enemy["spawn"]:
                continue
            airborne=enemy["air"]
            if world is None:
                pos=enemy["position"]
            else:
                row=world.receiver_at(key,frame,after_input=True)
                pos=(row["row"],row["col"]) if row else None
                airborne=bool(row and row.get("airborne",airborne))
            contact=trigger_contact(pos,enemy["radius"],cells) if pos is not None else False
            if contact is None:
                if airborne or aura.timer is not None:
                    return key
                continue
            if contact!=aura.touching:
                aura.contact(contact,airborne)
        return None

    press=scene.get("press_frame")
    skill = None
    mode = "Default"
    preparing = None
    next_attack = start + (ONE+gauge.step-1)//gauge.step + 1
    if continuation is not None:
        next_attack=continuation["resume"]
    busy_until = start
    sign=1 if placement["direction"]!="LEFT" else -1
    stop = "horizon"
    frame=end
    for frame in range(first_update,end+1):
        clock = assets["clock"]
        if world is not None and world.settle_exits(frame-1,lambda k:hp[k]>HP_EPSILON) is not None:
            stop="stage_defeat"
            break
        # The skill's first fixed resume follows OBJECTS. If an earlier attack
        # completes in that same resume pass, its finish callback runs first.
        active = skill is not None and skill+clock["spell_frame"] < frame < skill+clock["end"]
        stopped = (skill is not None and frame <= skill+clock["first_recovery_tick"]-1 or
                   continuation is not None and frame<continuation["resume"])
        if skill is not None and frame == skill+clock["end"]:
            mode = "Default"
            emit("skill_end",retained_projectiles=len(shots),old_release=preparing["release"] if preparing else None)
        if frame == start:
            emit("deploy",sp=state.points)
        elif state.advance(gauge.step,stopped=stopped):
            emit("natural_sp",sp=state.points)
        for key, enemy in enemies.items():
            if enemy["spawn"] == frame:
                emit("spawn",target=key)
        update_auras()
        for shot in shots[:]:
            key = shot["target"]
            if hp[key] <= HP_EPSILON:
                emit("projectile_target_lost",target=key,projectile=shot["key"])
                shots.remove(shot)
                continue
            enemy = enemies[key]
            if world is None:
                step=ordinary_trace_step(shot["position"],enemy["mount"],effective_dt=dt,round_position=True)
                shot["position"]=step.position
                reached=step.reached
            else:
                row=world.by_id(key,frame,projectile_born=shot["born"])
                if row is None:
                    emit("projectile_target_left",target=key)
                    shots.remove(shot)
                    continue
                dx,dy,z=shot["hit_offset"]
                mount=(row["col"]+dx,row["row"]+dy,z+row.get("height_offset",0))
                step=ordinary_trace_step(shot["position"],mount,effective_dt=dt,round_position=True)
                shot["position"]=step.position
                reached=step.reached
            if reached:
                aura=auras.get(key)
                current=world.receiver_at(key,frame) if world is not None else None
                result = ordinary_arts_hit(attack_raw=_raw_f32(stats["atk"]),resistance_raw=enemy["res"],hp_raw=hp[key],
                                           received_scale_raw=ONE if aura is None else aura.multiplier)
                hp[key] = result.hp_after_raw
                before = state.points
                if enemy["level"] in ("ELITE","BOSS") and not stopped:
                    state.points = min(state.maximum,state.points+elite_sp)
                emit("projectile_hit",target=key,projectile=shot["key"],damage_q32=result.damage_raw,
                     hp_after_q32=hp[key],air_fragile=bool(aura and aura.weak),
                     target_airborne=current.get("airborne",enemy["air"]) if current else enemy["air"],
                     module_sp=state.points-before)
                shots.remove(shot)
        if stop.startswith("unresolved"):
            break
        if preparing and frame == preparing["release"]:
            target = preparing["target"]
            launch=preparing["shot"]
            if hp[target] <= HP_EPSILON:
                emit("attack_target_lost",target=target,projectile=launch["key"])
            else:
                emit("projectile_release",target=target,projectile=launch["key"],**({"origin":launch["position"]} if "position" in launch else {}))
                shots.append(launch)
            preparing = None
        if frame==busy_until and busy_until>start and active and mode=="Default":
            # The registered finish callback sends ON_AFTER_ATTACK before
            # _NextAttackOrExit looks up the current mode's selector.
            mode = "S1"
            emit("attack_mode_changed",mode=mode)
        if frame == next_attack:
            if world is None:
                live=[(key,e) for key,e in enemies.items() if hp[key]>HP_EPSILON and e["in_range"] and e["spawn"]<=frame]
                choice=min(live,key=lambda pair:(mode=="S1" and not pair[1]["air"],pair[1]["rank"]),default=None)
            else:
                live=[e for e in world.snapshot(frame) if hp[e["id"]]>HP_EPSILON and
                      circle_in_range(e["row"],e["col"],enemies[e["id"]]["radius"],cells,box_query=True)]
                if mode == "S1":
                    live = qanik_priority_group([dict(row, airborne=row.get("airborne",enemies[row["id"]]["air"])) for row in live])
                selected=world.select(live,frame) if len(live)>1 else next(iter(live),None)
                choice=(selected["id"],selected) if selected else None
            if choice:
                target,enemy=choice
                speed = stats["attackSpeed"] + (board["attack_speed"] if active else 0)
                interval = single(stats["baseAttackTime"]*100/speed)
                ratio = interval/dt
                if abs(abs(ratio-round(ratio))-.5)<.001:
                    stop = "unresolved_half_frame_boundary"
                    break
                if enemy["col"]!=placement["col"]:
                    sign=1 if enemy["col"]>placement["col"] else -1
                shot=dict(target=target,key=assets["modes"][mode])
                if world is None:
                    ticks,sample=baked_key_sample(assets["key"],single(1/single(interval/assets["duration"])),dt)
                    local=baked_position(assets["mount"],sample,dt)
                    offset=transform_point((sign*local[0],local[1],sign*local[2]),assets["parents"])
                    shot["position"]=(placement["col"]+offset[0],placement["row"]+offset[1],_number(placement,"z",minimum=-1000)+offset[2])
                    release=frame+ticks
                else:
                    trace=ranged_shot_release(tables,assets["evidence"],assets["abilities"][mode],attack_frame=frame,
                                              attack_interval=interval,facing=facing,mode_path=f"Modes/{mode}")
                    release=trace["release_frame"]
                    origin,mount=stationary_cast_mounts(tables,QANIK,trace["clock"],placement,enemy,facing,caster_face_sign=sign)
                    shot.update(born=release,position=origin,
                                hit_offset=(mount[0]-enemy["col"],mount[1]-enemy["row"],mount[2]))
                next_attack = busy_until = frame+round(ratio)
                preparing = dict(release=release,target=target,shot=shot)
                emit("attack_start",target=target,mode=mode,release_frame=release)
            else:
                next_attack += 3
        if frame==press and state.points<state.cost:
            emit("skill_rejected",reason="sp_not_ready",sp=state.points)
        if not ordinary_only and skill is None and state.points==state.cost and (frame==press or press is None and frame>=busy_until):
            skill = frame
            state.points -= state.cost
            emit("manual_skill_on_ready" if press is None else "manual_skill",buff_frame=frame+clock["spell_frame"],
                 end_frame=frame+clock["end"],sp=state.points)
        if (uncertain:=update_contacts()) is not None:
            emit("unresolved_aura_contact",target=uncertain)
            stop="unresolved_aura_contact"
            break
        if (world.all_processed(hp) if world else all(v<=HP_EPSILON for v in hp.values())) and not shots and not preparing:
            stop = "all_enemies_processed" if world else "all_targets_defeated"
            break
    result=dict(status=stop, through_frame=frame, hp_q32=hp, events=log, sp=state.points,
                conditions=["普通地面敌人按原生路线移动" if world else "静止靶标，既定出生帧和受击挂点，无地形阻隔或外部战斗加成",
                            "指定帧手动开启冻风" if world else "场景为等嘲讽、剩余路线距离无并列的普通索敌；仅一次空闲时手动开启冻风",
                            "默认外观，输入给定前后视图；固定更新后开技能，普通速度、无动画混合"])
    if ordinary_only:
        result.update(shots=shots,windup=preparing,owner_state="attack" if busy_until>frame else (
            "born" if frame<start+(ONE+gauge.step-1)//gauge.step else "idle"))
    return result
