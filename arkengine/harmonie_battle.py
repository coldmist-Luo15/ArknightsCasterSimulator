"""Continuous stationary-target fixture during Harmonie's S1 loop, from an attack spell boundary.

The snapshot provides the ongoing action's elapsed animation time, stored energy and
remaining active window. All following target selections, projectiles, impacts and HP run here.
"""
from .animation_assets import read_skin_actions
from .attack_range import circle_in_range, range_cells
from .baked_mount import baked_position, single, transform_point
from .caster_cast import run_committed_cast
from .caster_prefabs import caster_prefab_evidence
from .damage import HP_EPSILON, ordinary_arts_hit
from .five_star_skills import HAMONI, _flag, _integer, _number, _targets, execute_five_star
from .fixed import ONE, mul
from .module_effects import active_board, talent_rows, trait_board
from .mystic_cycle import baked_key_sample
from .mystic_projectiles import _ordinary_mover
from .mystic_timing import fixed_wait_frames
from .projectile_scene import projectile_profiles
from .skill_recovery import deploy_skill
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def water_targets(card, event, aura):
    """Resolve settled contact with each S2 box trigger at its own callback."""
    from .lin_output_aura import trigger_contact
    rows = _targets(event)
    if "placement" not in event:
        return [row for row in rows if not _flag(row,"airborne") and _flag(row,aura+"_aura_eligible")]
    place = event["placement"]
    height,width = _integer(event,"rows"),_integer(event,"cols")
    if _integer(place,"row")>=height or _integer(place,"col")>=width:
        raise ValueError("和弦的水域中心超出地图")
    cells = range_cells(card["base_range"],place,height,width)
    eligible = []
    ignored = {"INVISIBLE"} if aura=="slow" else set()
    for row in rows:
        if _flag(row,"airborne"):
            continue
        contact = trigger_contact((_number(row,"row"),_number(row,"col")),_number(row,"radius"),cells)
        if contact is None:
            raise ValueError("和弦水域位于物理碰撞皮边界，需要该目标实际进出触发器的记录")
        if not contact:
            continue
        # TargetOptions._VerifyAdvanced, 0x2a79404/0x2a79478. The slow validator
        # ignores INVISIBLE (flag 9) while checking an ordinary, active ground enemy.
        flags = row["active_target_flags"]
        if not isinstance(flags,list) or any(f not in ("TARGET_FREE","INVINCIBLE","INVISIBLE","TOWER_TARGET_FREE") for f in flags):
            raise ValueError("复合异常或特殊运动目标需要给出底层已判定的光环资格")
        if not set(flags)-ignored:
            eligible.append(row)
    return eligible


def _assets(tables, card, facing):
    uid = card["operator_id"]
    cache = ("harmonie_active_volley",uid,card["skill_level"],card["module"],card["module_level"],facing)
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    ev = caster_prefab_evidence(tables,uid,1,skill_level=card["skill_level"],
                                module_id=card["module"],module_level=card["module_level"])
    comp = ev["character"]["components"]
    path = "Modes/S1/Body/Attack"
    atk, = [c["fields"] for c in comp if c["path"]==path and c["script"]=="MultiChargeRangedAttack"]
    charge, = [c["fields"] for c in comp if c["id"]==atk["_chargeAbility"]["m_PathID"] and c["script"]=="ChargeRangedAttack"]
    selector, = [c["fields"] for c in comp if c["path"]==path+"/Trigger" and c["script"]=="AdvancedSelector"]
    if (selector["_targetMotion"],selector["_targetSide"],selector["_postFilter"],selector["_maxNum"],
            atk["_resetCdStrategy"],atk["_animKey"],atk["_waitForAttackEvent"],atk["_selectTargetTiming"],
            round(atk["_preDelay"],5)) != (3,2,4,1,1,"",0,1,.4):
        raise ValueError("和弦一技能索敌或攻击时钟与已验证配置不同")
    mounts = [atk["_mountPointType"],*charge["_mountPointForProjectiles"]]
    keys = (atk["_projectileKey"],charge["_projectileKey"])
    variants = projectile_profiles(tables,keys)
    if not all(_ordinary_mover(v) and v["has_only_trace_hit"] for rows in variants.values() for v in rows.values()):
        raise ValueError("未覆盖和弦的弹丸运动或终结配置")
    channel, = [c for c in ev["skill"]["components"] if c["script"]=="ThreePartChannelingAnimation"]
    action = channel["fields"]["_default"]["loopAnim"]
    mount_fields = {2:"muzzlaTransform",5:"specialPoint0",6:"specialPoint1",8:"specialPoint2",9:"specialPoint3"}
    skin = read_skin_actions(tables,f"chararts/{uid}.ab","defaultskin",[action,"Born","Attack",""],
                             [(n,mount_fields[n]) for n in mounts],allow_unbound=True)
    view = skin["views"][facing.lower()]
    if "" in view["actions"] or any(k not in view["actions"] for k in (action,"Born","Attack")):
        raise ValueError("默认皮肤的空动作或技能时序绑定已改变")
    clip = view["actions"][action]
    if not clip["loop"] or clip["speed"] != 1:
        raise ValueError("技能循环动作配置改变")
    bake = tables.read(f"bakemuzzledata/character/{uid}_defaultskin.json")["bakedAnimDatas"][facing]
    graphic = tables._component_fields("characters",uid,("m_LocalPosition","m_LocalRotation","m_LocalScale"),
                                       "hamoni_graphic",component_type="Transform",children=("Graphic",))
    root = dict(position=graphic["m_LocalPosition"],rotation=graphic["m_LocalRotation"],scale=graphic["m_LocalScale"])
    result = dict(keys=keys,mounts=mounts,clip=clip,tracks=bake["bakedMPDatas"][clip["clip"]],
                  parents={n:[*view["mount_parents"][n],root] for n in mounts}, evidence=ev,
                  bake=bake, actions=view["actions"], channel=channel["fields"]["_default"])
    tables._prefab_data[cache] = result
    return result


def _normal_opening(tables,card,scene,data,enemies,hp,log,blocks):
    """Open S1 on the first full natural gauge while continuously attacking a valid receiver."""
    start,end = scene["frame"],scene["end_frame"]
    placement = scene["placement"]
    dt = tables.fixed_timestep()
    step = int(dt*ONE)
    sign = 1 if placement["direction"]=="RIGHT" else -1
    char = data["evidence"]["character"]["components"]
    ordinary, = (c["fields"] for c in char if c["path"]=="Modes/Default/Body/Attack" and c["script"]=="MultiChargeRangedAttack")
    if (ordinary["_selectTargetSource"],ordinary["_selectTargetTiming"],ordinary["_waitForAttackEvent"],
            ordinary["_maxAnimScale"],ordinary["_mountPointType"],ordinary["_resetCdStrategy"]) != (2,0,1,1.0,2,1):
        raise ValueError("和弦普通攻击的锁定、前摇或挂点配置不同")
    shot_key = ordinary["_projectileKey"]
    variants = projectile_profiles(tables,[shot_key])[shot_key]
    if not all(_ordinary_mover(v) and v["has_only_trace_hit"] for v in variants.values()):
        raise ValueError("和弦普通攻击弹体超出支持范围")
    clock = run_committed_cast(tables,data["evidence"],horizon=500)["natural_end"]
    switch = tables.read("battle/buff_template_data.json")["switch_mode_restart_fsm"]["eventToActions"]
    ability, = (c for c in data["evidence"]["skill"]["components"] if c["script"]=="ActiveBuffAbility")
    buffs = [b for b in ability["fields"]["_buffs"] if b["templateKey"]=="switch_mode_restart_fsm"]
    if (len(buffs)!=1 or not any(b["key"]=="mode" and b["value"]==1 for b in buffs[0]["blackboard"])
            or not switch["ON_BUFF_START"][0]["_restartFSM"] or clock["spell_frame"]!=1):
        raise ValueError("和弦技能启动时的模式切换条件不符")
    # The ordinary muzzle is invariant across all three S1 animation parts.
    # No pre-skill charge is created in this continuously targeted opening.
    main_tracks = [data["bake"]["bakedMPDatas"][clip]["2"] for clip in data["channel"].values()]
    if not all(track["frameCount"]==1 and track["trsData"]==main_tracks[0]["trsData"] for track in main_tracks):
        raise ValueError("该外观 S1 的主弹发射位置需要独立动画时钟")
    birth = tables.character_birth(HAMONI)
    if birth["_useRealBornTimeFromAnim"] or birth["m_overrideBornTime"]!=-1 or data["actions"]["Born"]["duration"]!=1:
        raise ValueError("出生时钟条件改变")
    stats = card["panel_attributes"]
    period = single(stats["baseAttackTime"]*100/stats["attackSpeed"])
    action = data["actions"]["Attack"]
    track = data["bake"]["bakedEvents"][action["clip"]]
    if action["loop"] or action["speed"]!=1 or track["events"]!=["OnAttack"]:
        raise ValueError("普攻烘焙关键帧不唯一")
    scale = min(ordinary["_maxAnimScale"],max(.1,single(period/action["duration"])))
    pre,sample = baked_key_sample(track["eventTime"][0],single(1/scale),dt)
    local = baked_position(data["bake"]["bakedMPDatas"][action["clip"]]["2"],sample,dt)
    delta = transform_point((sign*local[0],local[1],sign*local[2]),data["parents"][2])
    origin = (placement["col"]+delta[0],placement["row"]+delta[1],placement["z"]+delta[2])
    unit = tables.read("excel/character_table.json")[HAMONI]
    talent = active_board(talent_rows(card["module_phase"],0,"1",unit["talents"][0]["candidates"]),
                          card["elite"],card["level"],card["potential"])
    gauge,_ = deploy_skill(tables,card,start)
    base_cells = range_cells(card["base_range"],placement,scene["rows"],scene["cols"])
    skill_cells = range_cells(card["skill_range"],placement,scene["rows"],scene["cols"])
    next_cast = start+(ONE+step-1)//step+1
    old_cast=skill=None
    shots=[]
    for frame in range(start,end+1):
        if blocks is not None:
            blocks.update(frame,enemies,hp,log)
            _refresh_geometry(enemies,skill_cells)
        if frame==start:
            log.append(dict(kind="deploy",frame=frame,sp=gauge.state.points))
        elif skill is None and gauge.state.advance(gauge.step):
            log.append(dict(kind="natural_sp",frame=frame,sp=gauge.state.points))
        for shot in shots[:]:
            key=shot["id"]
            if hp[key]<=HP_EPSILON:
                return dict(status="unresolved_prior_receiver_dead",frame=frame)
            step_result=ordinary_trace_step(shot["pos"],enemies[key]["mount"],effective_dt=dt,round_position=True)
            shot["pos"]=step_result.position
            if step_result.reached:
                power=_raw_f32(stats["atk"])
                if enemies[key]["blocked"]:
                    power=mul(power,_raw_f32(talent["atk_scale"]))
                damage=ordinary_arts_hit(attack_raw=power,resistance_raw=_raw_f32(enemies[key]["res"]),hp_raw=hp[key]).damage_raw
                hp[key]=max(0,hp[key]-damage)
                if hp[key]<HP_EPSILON:
                    hp[key]=0
                log.append(dict(kind="projectile_hit",frame=frame,target=key,packet="normal",mount=2,
                                damage_q32=damage,hp_after_q32=hp[key]))
                shots.remove(shot)
        if not any(hp.values()):
            return dict(status="all_targets_defeated",frame=frame)
        if frame==next_cast:
            candidates=[key for key,e in enemies.items() if hp[key]>HP_EPSILON
                        and circle_in_range(*e["position"],e["radius"],base_cells,box_query=True)]
            if not candidates:
                return dict(status="requires_empty_charge_cycle",frame=frame)
            target=min(candidates,key=lambda k:enemies[k]["priority"])
            target_col=enemies[target]["position"][1]
            if (target_col-placement["col"])*sign<=0:
                return dict(status="requires_independent_facing",frame=frame)
            old_cast=dict(start=frame,target=target,release=frame+pre)
            log.append(dict(kind="normal_attack_start",frame=frame,target=target))
            next_cast+=round(period/dt)
        if old_cast and frame==old_cast["release"]:
            key=old_cast["target"]
            if hp[key]<=HP_EPSILON:
                return dict(status="unresolved_prior_receiver_dead",frame=frame)
            shots.append(dict(id=key,pos=origin))
            log.append(dict(kind="projectile_release",frame=frame,target=key,projectile=shot_key,mount=2,origin=origin))
        if skill is None and gauge.state.points>=gauge.state.cost:
            skill=frame
            gauge.state.points-=gauge.state.cost
            log.append(dict(kind="manual_skill_on_ready",frame=frame,sp=gauge.state.points))
        if skill is not None and frame==skill+clock["spell_frame"]:
            if shots or not old_cast or not any(hp[k]>HP_EPSILON and e["eligible"] for k,e in enemies.items()):
                return dict(status="requires_in_flight_or_empty_mode_switch",frame=frame)
            # The old active ATTACK state restarts immediately in the late Unity
            # fixed resume. The new no-clip attack starts a fixed wait for 0.4 s.
            delay=next(c["fields"]["_preDelay"] for c in char if c["path"]=="Modes/S1/Body/Attack"
                       and c["script"]=="MultiChargeRangedAttack")
            pre_ticks=fixed_wait_frames(single(single(delay)/single(dt)))
            log.append(dict(kind="switch_mode_restart_attack",frame=frame,interrupted_start=old_cast["start"],
                            old_release_occurred=old_cast["release"]<=frame,pre_delay_ticks=pre_ticks))
            return dict(status="active",frame=frame+pre_ticks,last_active=skill+clock["end"]-1)
    return dict(status="horizon",frame=end)


def _refresh_geometry(enemies,cells):
    for e in enemies.values():
        row,col=e["position"]
        dx,dy,z=e["hit_offset"]
        e["mount"]=(single(col+dx),single(row+dy),z)
        e["eligible"]=circle_in_range(row,col,e["radius"],cells,box_query=True)


def run_harmonie_active_battle(tables,card,scene):
    if card["operator_id"] != HAMONI or card["skill"] != 1 or not card["module"]:
        raise ValueError("此技能循环靶场需要带常规模组的和弦一技能")
    start,end = _integer(scene,"frame"),_integer(scene,"end_frame")
    entry = scene.get("entry", "active_spell")
    if entry not in ("deploy", "active_spell"):
        raise ValueError("和弦靶场的开始状态无效")
    deployed = entry == "deploy"
    last_active = start if deployed else _integer(scene,"last_active_frame")
    placement = scene["placement"]
    facing = placement["spine_view"]
    if (start > end or end-start > 18000 or last_active < start or last_active-start >= 30*card["skill_row"]["duration"]
            or facing not in ("Front","Back") or placement["direction"] not in ("RIGHT","LEFT")):
        raise ValueError("技能有效区间、循环动作或水平朝向无效")
    rows,cols = _integer(scene,"rows"),_integer(scene,"cols")
    if not (0 <= _integer(placement,"row") < rows and 0 <= _integer(placement,"col") < cols):
        raise ValueError("部署位置超出靶场")
    data = _assets(tables,card,facing)
    dt = tables.fixed_timestep()
    elapsed = 0 if deployed else _number(scene,"loop_elapsed_seconds")
    if elapsed >= data["clip"]["duration"]:
        raise ValueError("当前循环动画时间应小于片段时长")
    cell = range_cells(card["skill_range"],placement,rows,cols)
    enemies, health = {},{}
    for row in _targets(scene):
        key = row["id"]
        pos = _number(row,"row"),_number(row,"col")
        if not (pos[0]<rows and pos[1]<cols) or len(row["hit_offset"])!=3:
            raise ValueError("静止靶的位置或受击挂点无效")
        offset = [_number(dict(v=v),"v",minimum=-1000) for v in row["hit_offset"]]
        health[key] = _raw_f32(_number(row,"hp"))
        if health[key] <= HP_EPSILON:
            raise ValueError("靶标需要正的初始生命")
        enemies[key] = dict(priority=_number(row,"route_remaining"),blocked=False if "blocker" in scene else _flag(row,"blocked"),
                            position=pos,hit_offset=offset,radius=_number(row,"radius"),
                            res=_number(row,"res",minimum=-1000),mount=(pos[1]+offset[0],pos[0]+offset[1],offset[2]),
                            eligible=circle_in_range(*pos,_number(row,"radius"),cell,box_query=True))
    if not enemies or len(set(e["priority"] for e in enemies.values())) != len(enemies):
        raise ValueError("等嘲讽的靶标需要各自明确的路线优先级")
    unit = tables.read("excel/character_table.json")[HAMONI]
    cap = int(trait_board(unit,card["module_phase"],card)["times"])
    stored = 0 if deployed else _integer(scene,"stored")
    if stored > cap or cap > len(data["mounts"])-1:
        raise ValueError("储能数或可用挂点超过实包限额")
    stats = card["panel_attributes"]
    board = {item["key"]:item["value"] for item in card["skill_row"]["blackboard"]}
    period = single(stats["baseAttackTime"]*board["base_attack_time"]*100/stats["attackSpeed"])
    ratio = period / dt
    if abs(abs(ratio-round(ratio))-.5) < .001 or period <= .4:
        raise ValueError("未覆盖该前摇与技能攻击间隔边界")
    shots, log = [],[]
    blocks=None
    if "blocker" in scene:
        from .blocking import StationaryBlockSearch
        if any("blocked" in t for t in scene["targets"]) or scene["blocker"]["first_search_frame"]<start:
            raise ValueError("自动阻挡的靶场需要在起点之后开始搜索，由模型计算每个靶标的阻挡状态")
        blocks=StationaryBlockSearch(tables,scene["blocker"],scene["targets"])
    if deployed:
        opening = _normal_opening(tables,card,scene,data,enemies,health,log,blocks)
        if opening["status"]!="active":
            return dict(status=opening["status"],through_frame=opening["frame"],events=log,hp_q32=health)
        start,last_active = opening["frame"],opening["last_active"]
        if start>end:
            return dict(status="horizon_before_first_skill_spell",through_frame=end,events=log,hp_q32=health)
    next_spell = start
    status = "horizon"
    sign = 1 if placement["direction"]=="RIGHT" else -1
    for frame in range(start,end+1):
        if blocks is not None:
            blocks.update(frame,enemies,health,log)
            _refresh_geometry(enemies,cell)
        if frame > last_active and shots:
            status = "requires_in_flight_skill_expiry_handling"
            break
        for shot in shots[:]:
            enemy = enemies[shot["target"]]
            if health[shot["target"]] <= HP_EPSILON:
                return dict(status="unresolved_destroyed_receiver",through_frame=frame,events=log,hp_q32=health)
            moved = ordinary_trace_step(shot["position"],enemy["mount"],effective_dt=dt,round_position=True)
            shot["position"] = moved.position
            if moved.reached:
                row = dict(id=shot["target"],hp=health[shot["target"]]/ONE,res=enemy["res"],blocked=enemy["blocked"])
                hit = execute_five_star(tables,card,dict(kind="projectile_hit",frame=frame,target=row,
                                                        projectile_kind=shot["kind"]))
                damage = hit["result"]["damage_raw"]
                remaining = health[row["id"]]-damage
                health[row["id"]] = 0 if remaining < HP_EPSILON else remaining
                log.append(dict(kind="projectile_hit",frame=frame,target=row["id"],packet=shot["kind"],
                                mount=shot["mount"],damage_q32=damage,hp_after_q32=health[row["id"]]))
                shots.remove(shot)
        if frame == next_spell and frame<=last_active:
            live = [key for key,e in enemies.items() if health[key]>HP_EPSILON and e["eligible"]]
            if not live:
                if not shots:
                    status="eligible_targets_defeated"
                    break
            else:
                target = min(live,key=lambda key:enemies[key]["priority"])
                log.append(dict(kind="select_and_release",frame=frame,target=target,stored=stored))
                for i,mount in enumerate(data["mounts"][:1+stored]):
                    local = baked_position(data["tracks"][str(mount)],elapsed,dt)
                    delta = transform_point((sign*local[0],local[1],sign*local[2]),data["parents"][mount])
                    origin = (placement["col"]+delta[0],placement["row"]+delta[1],
                              _number(placement,"z",minimum=-1000)+delta[2])
                    shots.append(dict(target=target,position=origin,kind="primary" if i==0 else "stored",mount=mount))
                    log.append(dict(kind="projectile_release",frame=frame,projectile=data["keys"][int(i>0)],
                                    target=target,mount=mount,origin=origin))
                stored=0
            next_spell+=round(ratio)
        if not shots and not any(health[key]>HP_EPSILON and e["eligible"] for key,e in enemies.items()):
            status="eligible_targets_defeated"
            break
        if frame>last_active and not shots:
            status="skill_window_ended"
            break
        elapsed = single(elapsed + single(dt)) % data["clip"]["duration"]
    return dict(status=status,through_frame=frame,hp_q32=health,events=log,stored=stored,
                cycle_ticks=round(ratio),source_frame="deployment" if deployed else "active_S1_attack_spell_during_loop",
                conditions=["静止、等嘲讽、路线优先级无并列",
                            "确定性战斗补间先于对象更新；阻挡者先于和弦和飞行中的弹体更新，受击点跟随站位" if blocks else "靶标阻挡状态固定",
                            ("部署后持续普攻有效靶，技力满时在固定更新后开启一技能，切换时无旧弹在途"
                             if deployed else "首次技能攻击已进入释放阶段，提供循环动画时间、储能与技能剩余窗口"),
                            "默认皮肤，单倍速持续对有目标的范围发动攻击，各弹独立追踪并在命中时结算"])
