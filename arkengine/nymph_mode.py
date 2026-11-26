"""Deployment and projectile attacks for Nymph S1/S3 against one stationary ground target."""

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_equipment_traits import _trait_values
from .caster_cast import _after_spell_clock
from .caster_prefabs import caster_prefab_evidence
from .caster_opening import deploy_to_first_cast
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_casters import NYMPH, _board
from .elemental_projectiles import stationary_ranged_shot
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .mode_replay import _AttackCooldown
from .mystic_timing import fixed_wait_frames
from .nymph_impact import apply_s3_hit, s3_damage_scales
from .nymph_soul import SoulOnTarget, SoulRules
from .skill_recovery import READY_EPSILON, _divide
from .volley_damage import _raw_f32


def run_nymph_mode(tables,card,event):
    slot = card["skill"]
    if card["operator_id"]!=NYMPH or slot not in (1,3) or not card["module"]:
        raise ValueError("需要妮芙一或三技能及常规模组")
    start,end = event["frame"],event["end_frame"]
    enemy,place,face = event["enemy"],event["placement"],event["view"]
    if (type(start) is not int or type(end) is not int or not 0<=start<end or face not in ("Front","Back")
            or enemy["radius"]<=0 or enemy["col"]==place["col"]):
        raise ValueError("此场景需要有效的部署区间与明确的左右目标朝向")
    cells = range_cells(card["skill_range"] or card["base_range"],place,event["rows"],event["cols"])
    if not circle_in_range(enemy["row"],enemy["col"],enemy["radius"],cells,box_query=True):
        raise ValueError("目标不在所选技能的范围内")
    ev = caster_prefab_evidence(tables,NYMPH,slot,skill_level=card["skill_level"],module_id=card["module"],
                                module_level=card["module_level"])
    parts = ev["character"]["components"]
    f, = [c["fields"] for c in parts if c["path"]==f"Modes/S{slot}/Body/Attack"
          and c["script"] in ("RangedAttack","RangedAttackWithExtraActions")]
    if (f["_timeMode"]!=0 or not f["_waitForAttackEvent"] or f["_maxAnimScale"]!=1 or
            f["_mountPointType"]!=2 or f["_waitForProjectileInvalid"] or f["_resetCdStrategy"]!=1):
        raise ValueError("技能模式攻击动作需要重新核对")
    dt = tables.fixed_timestep()
    step = _raw_f32(dt)
    bb = {r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    s3_scales = s3_damage_scales(f,bb) if slot == 3 else None
    panel = card["panel_attributes"]
    base_speed = panel["attackSpeed"]+bb.get("attack_speed",0)
    interval = single(single(panel["baseAttackTime"])*100/single(base_speed))
    period = fixed_wait_frames(single(interval/dt))
    state = EnemyElements.from_scene_enemy(tables,enemy)
    # S1 retains the idle FSM. Its first idle search is one tick after leaving BORN,
    # then every third tick. This scene places its manual input one tick before a search.
    def before_idle_search(earliest,born_exit):
        return earliest+(born_exit-earliest)%3
    opening = deploy_to_first_cast(tables,card,ev,deploy_frame=start,end_frame=end,facing=face,
        direction=place["direction"],next_manual_input=before_idle_search if slot==1 else None)
    logs = [dict(kind="deploy",frame=start,sp=opening.initial_sp)]
    if opening.first_ready_frame is None:
        return dict(status="before_ready",through_frame=end,events=logs,sp=opening.sp)
    logs.append(dict(kind="first_ready",frame=opening.first_ready_frame,sp=card["skill_row"]["spData"]["spCost"]))
    cast = opening.cast
    if cast is None:
        return dict(status="before_planned_input",through_frame=end,events=logs)
    if cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("技能没有走已验证的入场与模式切换")
    logs.extend(r for r in cast["events"] if r["frame"]<=end)
    mode = cast["spell_frame"]
    if mode>end:
        return dict(status="before_mode_entry",events=logs,through_frame=end)
    attack_frame = mode+1
    # This receiver is born after the caster's preceding empty search and already
    # admits the single selected enemy. No ordinary projectile was created before the skill.
    projectile = f["_projectileKey"]
    for variant in ev["projectiles"][projectile].values():
        life, = [n["fields"] for n in variant["components"] if n["script"]=="SimpleProjectile"]
        if life["_randomDelayToBorn"] or not life["_alwaysHitTraceTargetInTheEnd"]:
            raise ValueError("技能弹体的出生或停弹命中变化")
    close = mode+fixed_wait_frames(single(single(card["skill_row"]["duration"])/dt))
    strength = _raw_f32(panel["atk"])
    talent = _board(card,1,"2")
    module = _trait_values(card)
    target_in_talent_range = circle_in_range(enemy["row"],enemy["col"],enemy["radius"],
            range_cells(card["base_range"],place,event["rows"],event["cols"]),box_query=True)
    soul = SoulOnTarget(SoulRules.from_card(tables,card,ev))
    stack_limit = int(talent["max_stack_cnt"])
    threshold = talent.get("stack_cnt_check")
    if threshold is not None:
        actions=tables.read("battle/buff_template_data.json")["nymph_e_002_t_2"]["eventToActions"]["ON_EP_BREAK_START"]
        filter_burst,stack,check,create=actions
        speed,=[r for r in create["_failNodes"] if r["_buff"]["buffKey"]=="nymph_e_002_t_2[atk_spd]"]
        mod,=speed["_buff"]["attributes"]["attributeModifiers"]
        if (filter_burst["_recoveryType"]!="DARK" or stack["_buff"]["buffKey"]!=check["_buffKey"]
                or check["_stackCountKey"]!="stack_cnt_check" or check["_condType"]!="GE"
                or speed["_buffOwner"]!="BUFF_SOURCE" or mod["attributeType"]!="ATTACK_SPEED"
                or mod["formulaItem"]!="ADDITION" or not mod["loadFromBlackboard"]):
            raise ValueError("窥心钥的攻速层数门槛或属性修饰变化")
    talent_stacks = event.get("prior_dark_stacks",0)
    if type(talent_stacks) is not int or not 0<=talent_stacks<=stack_limit or talent_stacks and threshold is None:
        raise ValueError("已有窥心钥层数需要 X2 或 X3 全场监听配置和有效整数")
    hears_burst = target_in_talent_range or threshold is not None
    withdrawal = event.get("withdraw_frame")
    if withdrawal is not None and (type(withdrawal) is not int or not mode<=withdrawal<min(end+1,close)):
        raise ValueError("撤退输入须在本次技能窗口内")
    initial_buildup = event.get("initial_external_buildup")
    if event.get("initial_external_dark_buildup_q32") is not None:
        if initial_buildup is not None:
            raise ValueError("同一笔外部损伤不能同时给两种输入")
        initial_buildup = dict(element="DARK", amount_q32=event["initial_external_dark_buildup_q32"])
    buildup_frame = initial_buildup.get("frame",mode) if initial_buildup is not None else None
    if buildup_frame is not None and (type(buildup_frame) is not int or not mode<=buildup_frame<close):
        raise ValueError("队友元素积累需在技能有效帧内")
    shot_cache={}
    next_hit=next_release=None
    cooldown=None
    cast_end=None

    def current_speed():
        return base_speed+(talent["attack_speed"] if threshold is not None and talent_stacks>=threshold else 0)

    def attack_time(speed):
        return _divide(_raw_f32(panel["baseAttackTime"])*100,_raw_f32(speed))

    def shot_for(speed):
        if speed not in shot_cache:
            seconds=single(single(panel["baseAttackTime"])*100/single(speed))
            cycle=fixed_wait_frames(single(seconds/dt))
            sample=stationary_ranged_shot(tables,ev,f,attack_frame=0,attack_interval=seconds,placement=place,
                enemy=enemy,facing=face,round_position=event["round_position"],mode_path=f"Modes/S{slot}",max_flight_ticks=cycle)
            if sample["reach_frame"]>=cycle:
                raise ValueError("该目标的飞行期超出已核对的单次攻击窗口")
            shot_cache[speed]=cycle,sample
        return shot_cache[speed]

    def gain_stack(frame):
        nonlocal talent_stacks,attack_frame
        old_speed=current_speed()
        talent_stacks=min(stack_limit,talent_stacks+1)
        speed=current_speed()
        if speed==old_speed:
            return
        log=dict(kind="talent_attack_speed",frame=frame,stacks=talent_stacks,attack_speed=speed)
        if cooldown is not None:
            before=cooldown.remaining_at(frame,step)
            cooldown.change_period(frame,step,attack_time(speed))
            after=cooldown.remaining
            ready=frame+max(0,(after-READY_EPSILON+step)//step)
            attack_frame=max(attack_frame if frame>=cast_end else frame+1,cast_end,ready)
            log.update(cooldown_before_q32=before,cooldown_after_q32=after,active_cast_end=cast_end)
        logs.append(log)

    stop = "horizon"
    _,shot=shot_for(current_speed())
    for frame in range(mode,min(end,close)+1):
        if frame==close:
            stop = "skill_end_boundary"
            break
        state.tick(frame)
        src = mul(strength,ONE+_raw_f32(bb["atk"])+talent_stacks*_raw_f32(talent["atk"]))
        factor = _raw_f32(module.get("damage_scale",1)) if state.burst else ONE
        soul.tick(frame,state,src,factor)
        if state.hp<=HP_EPSILON:
            stop = "enemy_defeated"
            break
        if frame==buildup_frame:
            prior=state.burst
            state.apply_buildup(frame,initial_buildup["element"],initial_buildup["amount_q32"])
            if prior!="DARK" and state.burst=="DARK" and hears_burst:
                gain_stack(frame)
        if frame==attack_frame:
            speed=current_speed()
            cycle,shot=shot_for(speed)
            next_release,next_hit=frame+shot["release_frame"],frame+shot["reach_frame"]
            cast_end=frame+_after_spell_clock(shot["release_frame"],shot["clock"]["cached_duration_seconds"],step)["end"]
            cooldown=_AttackCooldown(frame,attack_time(speed))
            logs.append(dict(kind="attack_start",frame=frame,attack_speed=speed,latched_end=cast_end))
            attack_frame=frame+cycle
        if frame==next_release:
            logs.append(dict(kind="projectile_release",frame=frame,baked_event_frame=frame-1,projectile=projectile))
        if frame==next_hit:
            src = mul(strength,ONE+_raw_f32(bb["atk"])+talent_stacks*_raw_f32(talent["atk"]))
            dark = state.burst=="DARK"
            factor = _raw_f32(module.get("damage_scale",1)) if state.burst else ONE
            soul.before_attack_damage()
            extra = None
            if dark and slot==1:
                extra = state.apply_elemental_hp(frame,mul(src,_raw_f32(bb["attack@extra_ep_damage_scale"])),
                                                 source="nymph_s1",output_scale_q32=factor)
            # S3 requests a zero-scale magical packet in the DARK branch before the elemental one.
            arts = 0
            if slot == 3:
                packet, extra = apply_s3_hit(state,frame,src,enemy["arts_resistance_q32"]+state.res_add,factor,s3_scales)
                arts = packet["damage_q32"]
            elif state.hp>HP_EPSILON:
                actual = ordinary_arts_hit(attack_raw=src,resistance_raw=enemy["arts_resistance_q32"]+state.res_add,hp_raw=state.hp)
                arts = mul(actual.damage_raw,factor)
                state.hp = max(0,state.hp-arts)
                if state.hp<HP_EPSILON:
                    state.hp = 0
                state.apply_buildup(frame,"DARK",mul(arts,_raw_f32(bb["attack@ep_damage_ratio"])))
                if not dark and state.burst=="DARK" and hears_burst:
                    gain_stack(frame)
            logs.append(dict(kind="skill_impact",frame=frame,arts_damage_q32=arts,elemental_damage=extra,hp_after_q32=state.hp))
            if state.hp<=HP_EPSILON:
                stop = "enemy_defeated"
                break
        if frame==withdrawal:
            soul.withdraw()
            logs.append(dict(kind="withdraw",frame=frame,phase="input"))
            stop = "caster_withdrawn"
            break
    return dict(status="nymph_active_mode_scene",through_frame=frame,stop=stop,events=logs,element_events=state.events,
                receiver=state.snapshot(),talent_stacks=talent_stacks,mode_start=mode,
                shot_travel_ticks=shot["reach_frame"]-shot["release_frame"],attack_period_frames=period,
                conditions=["one_stationary_ground_target","empty_pre_skill_target_list","default_skin",
                            "receiver_ticks_before_projectile_arrival"])
