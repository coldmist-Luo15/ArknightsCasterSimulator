"""Local skill events for Beeswax, Carnelian and 圣聆初雪.

Positions are a supplied selector snapshot. HP packets assume ordinary receivers
without shields, damage interception, immunity or external modifiers.
"""
from math import isfinite

from .attack_range import circle_in_range, range_cells
from .caster_ranges import skill_range_cells
from .damage import HP_EPSILON, ordinary_arts_hit
from .fixed import ONE, mul
from .module_effects import active_board, talent_rows, trait_board
from .opening_scene import bundle_environment
from .skill_assets import _script_classes
from .skill_recovery import NaturalSkillPoints, RecoveryTimer, _divide
from .volley_damage import _raw_f32


BEE = "char_344_beewax"
CARN = "char_426_billro"
SNOW = "char_1046_sbell2"
PHALANX = {BEE, CARN, SNOW}
PRTS = {BEE: 446562, CARN: 446498, SNOW: 446621}


def _talent(card, index, key):
    base = [r for r in card["base_talents"] if r["prefabKey"] == key]
    rows = talent_rows(card["module_phase"] or {}, index, key, base)
    return active_board(rows, card["elite"], card["level"], card["potential"])


def _controller(tables, card):
    sid = card["skill_row"]["prefabId"]
    key = ("phalanx_controller", sid)
    if key not in tables._prefab_data:
        raw, source = tables.verified_bundle("battle/prefabs/[uc]skills.ab")
        node = bundle_environment(raw).container[f"dyn/battle/prefabs/[uc]skills/{sid}.prefab"].deref()
        scripts = _script_classes(tables)
        controllers = []
        for row in node.read_typetree()["m_Component"]:
            obj = node.assets_file.objects[row["component"]["m_PathID"]]
            if obj.type.name != "MonoBehaviour":
                continue
            names = scripts.get(obj.serialized_type.old_type_hash.hex(), ())
            if len(names) == 1 and names[0]["name"] in ("CastSkill", "CastSkillWithSuspendable"):
                controllers.append(obj)
        controller, = controllers
        fields = controller.read_typetree()
        if not fields["m_Enabled"] or fields["_allowSpRecoveryWhenAffecting"]:
            raise ValueError(f"未核对的技能阻回控制器 {sid}")
        result = dict(sp_blocked_while_affecting=True, attack_board_mode=fields["_attackBlackboardModeIndex"],
                      begin_action=fields["_beginAnim"] or None,
                      play_begin=bool(fields["_playSkillBeginAnim"]), source=dict(**source, component=controller.path_id))
        tables._prefab_data[key] = result
    return tables._prefab_data[key]


def _capacity(card):
    data = card["skill_row"]["spData"]
    capacity = data["spCost"] * data["maxChargeTime"]
    return data["spCost"], capacity


def _aura_scale(card, count):
    aura = _talent(card,-1,"10")
    if "damage_scale" not in aura:
        return ONE
    if type(count) is not int or count<0:
        raise ValueError("光环计数须为非负整数")
    return ONE+_raw_f32(aura["damage_scale"])*min(count,int(aura["max_valid_stack_cnt"]))


def _carnelian_caps(tables,card,charged):
    trait = trait_board(tables.read("excel/character_table.json")[CARN],card["module_phase"] or {},card)
    gain=trait.get("billro_e_002[max_hp_enhance_buff].max_hp" if charged else "billro_e_002[max_hp_buff].max_hp",0)
    base=_raw_f32(card["panel_attributes"]["maxHp"])
    return base,mul(base,ONE+_raw_f32(gain))


def _change_hp_cap(hp,old_cap,new_cap):
    if type(hp) is not int or not HP_EPSILON<hp<=old_cap:
        raise ValueError("需要改变生命上限前的普通存活血量")
    # Entity.OnAttributeDirty(MAX_HP) saves the old ratio and sets HP against the new cap.
    return hp if old_cap==new_cap else min(new_cap,mul(_divide(hp,old_cap),new_cap))


def _heal_modifiers(tables,amount,event):
    # ApplyHeal's HP-ratio branch bypasses CalculateHeal. The resulting heal
    # modifier still passes through the source and then the receiver's buffs.
    templates=tables.read("battle/buff_template_data.json")
    for key,callback,field in (("heal_scale[out]","ON_OUTPUT_MODIFIER","outgoing_heal_scales_q32"),
                               ("heal_scale","ON_APPLYING_MODIFIER","incoming_heal_scales_q32")):
        actions=templates[key]["eventToActions"][callback]
        if [a["$type"].split(",")[0].split("+")[-1] for a in actions]!=["IsHeal","ModifierScaleUp"]:
            raise ValueError("通用治疗效果增减益的动作顺序已改变")
        for scale in event.get(field,()):
            if type(scale) is not int or scale<0:
                raise ValueError("已生效的治疗倍率需要非负 Q32 值，按该侧回调次序提供")
            amount=mul(amount,scale)
    return amount


def _cast(tables, card, event):
    uid, slot = card["operator_id"], card["skill"]
    cost, limit = _capacity(card)
    sp = event["sp"]
    if type(sp) is not int or not 0 <= sp <= limit:
        raise ValueError("当前技力超出上限")
    if sp < cost:
        return dict(status="rejected", reason="insufficient_sp", sp_after=sp)
    charged = uid == CARN and sp == limit
    result = dict(status="resolved", scope="already_accepted_cast_resource_event", charged=charged,
                  sp_after=0 if uid == CARN else sp - cost, **_controller(tables, card))
    if uid == CARN:
        talent = _talent(card, 0, "1")
        base,max_hp=_carnelian_caps(tables,card,charged)
        heal=mul(max_hp,_raw_f32(talent["billro_t_1[enhance].heal_scale" if charged else "heal_scale"]))
        adjusted=_heal_modifiers(tables,heal,event)
        result.update(skill_max_hp_q32=max_hp,heal_request_q32=heal,heal_after_modifiers_q32=adjusted)
        if "hp_before_q32" in event:
            before=_change_hp_cap(event["hp_before_q32"],base,max_hp)
            blocked=event.get("heal_free",False)
            if type(blocked) is not bool:
                raise ValueError("禁疗状态需要布尔值")
            actual=0 if blocked else min(adjusted,max_hp-before)
            result.update(owner_hp=dict(hp_after_cap_q32=before,heal_received_q32=actual,hp_after_q32=before+actual))
    if uid == BEE and slot == 2:
        result["summon_request"] = card["skill_binding"]["overrideTokenKey"]
    return result


def _sp_window(tables, card, event):
    """Advance a constant-recovery-state window. A talent's poll event is distinct from crossing its SP threshold."""
    cost, limit = _capacity(card)
    ticks = event["ticks"]
    if type(ticks) is not int or ticks < 0 or type(event["blocked"]) is not bool:
        raise ValueError("需要非负整数逻辑帧数与明确阻回状态")
    sp = event["sp"]
    if type(sp) is not int or not 0 <= sp <= limit:
        raise ValueError("起始技力超出上限")
    speed = 1
    if card["operator_id"] == CARN:
        active = event["recovery_talent_active"]
        if type(active) is not bool:
            raise ValueError("需要当前蓄势待发增益状态")
        if active:
            speed += _talent(card, 1, "2")["sp_recovery_per_sec"]
    period = _divide(ONE, _raw_f32(speed))
    timer = RecoveryTimer(period)
    if event.get("fresh_timer") is not True:
        timer.remaining = event["remaining_q32"]
        if type(timer.remaining) is not int or not 0 <= timer.remaining <= period:
            raise ValueError("技力计时器余量不合法")
    state = NaturalSkillPoints(sp, cost, limit, timer)
    step = _raw_f32(tables.fixed_timestep())
    credits = []
    for n in range(1, ticks+1):
        if state.advance(step, stopped=event["blocked"]):
            credits.append(dict(frame=event["frame"]+n, sp=state.points))
    return dict(status="resolved", scope="constant_modifier_window", credits=credits, sp_after=state.points,
                remaining_q32=state.timer.remaining, period_q32=period, recovery_blocked=event["blocked"])


def _carnelian_ramp(tables,card,ticks,maximum):
    from .caster_prefabs import caster_prefab_evidence
    if type(ticks) is not int or ticks<0:
        raise ValueError("食噬之印需要模式切入后已经过的完整逻辑帧")
    ev=caster_prefab_evidence(tables,CARN,3,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    buff,=(b for r in ev["skill"]["components"] if r["script"]=="ActiveBuffAbility" for b in r["fields"]["_buffs"]
           if b["templateKey"]=="billro_s_3[atk]")
    node,=tables.read("battle/buff_template_data.json")[buff["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    if (buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"]!=-1 or buff["triggerInterval"]<=0 or
            not node["_isInversed"] or (node["_attributeType"],node["_formulaType"])!=("ATK","MULTIPLIER")):
        raise ValueError("食噬之印的剩余时长插值或首次触发配置已变化")
    period=RecoveryTimer(_raw_f32(buff["triggerInterval"]))
    period.remaining=0
    step=_raw_f32(tables.fixed_timestep())
    lifetime=_raw_f32(card["skill_row"]["duration"])
    denominator=lifetime-_raw_f32(node["_endTime"])
    if ticks*step>lifetime or denominator<=0:
        raise ValueError("食噬之印已离开有效的伤害增长窗口")
    last=0
    for tick in range(1,ticks+1):
        if period.advance(step):
            # Buff.OnTick dispatches OnTrigger before reducing remainingTime by this tick's delta.
            last=tick
    ratio=min(ONE,max(0,_divide(denominator-max(0,last-1)*step,denominator)))
    upper=_raw_f32(maximum)
    return dict(last_update_ticks=last,bonus_q32=upper+mul(-upper,ratio))


def _hit(tables, card, event):
    uid, slot = card["operator_id"], card["skill"]
    board = {b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    area = skill_range_cells(tables, card, event)
    if area["status"] != "resolved":
        return area
    cells = area["cells"]
    attack = _raw_f32(card["panel_attributes"]["atk"])
    effect = {}
    resistance_ignore = 0
    ramp=None
    charged = event.get("charged")
    if uid == CARN and type(charged) is not bool:
        raise ValueError("卡涅利安需要本次释放的蓄力状态")
    source = event.get("source", "active_attack")
    if source not in ("active_attack", "obelisk_appearance"):
        raise ValueError("不支持此命中来源")
    if source == "obelisk_appearance":
        if uid != BEE or slot != 2:
            raise ValueError("方尖塔只属于蜜蜡二技能")
        token = tables.read("excel/character_table.json")[card["skill_binding"]["overrideTokenKey"]]
        sid = token["skills"][slot-1]["skillId"]
        level = tables.read("excel/skill_table.json")[sid]["levels"][card["skill_level"]-1]
        origin = event["obelisk_tile"]
        if (origin["row"],origin["col"]) not in cells:
            raise ValueError("方尖塔不在技能可部署范围")
        cells = range_cells(tables.read("excel/range_table.json")[level["rangeId"]],
                            dict(**origin,direction="RIGHT"), event["rows"], event["cols"])
        attack = mul(attack, _raw_f32(board["atk_scale"]))
        effect["stun_seconds"] = board["stun"]
    elif uid == CARN and slot == 3:
        if "ticks_since_mode" in event:
            ramp=_carnelian_ramp(tables,card,event["ticks_since_mode"],board["atk"])
            bonus=ramp["bonus_q32"]
        else:
            steps = event["completed_ramp_steps"]
            if type(steps) is not int or not 0 <= steps <= 20:
                raise ValueError("食噬之印已触发的攻击加成次数应在 0 至 20")
            bonus=_raw_f32(board["atk"]*steps/20)
        attack = mul(attack, ONE+bonus)
    elif uid == SNOW:
        attack = mul(attack, ONE+_raw_f32(board.get("atk",0)))
        attack = mul(attack, _raw_f32(board["atk_scale"] if slot == 1 else board[f"attack@atk_scale_s{slot}"]))
        resistance_ignore = board.get("magic_resist_penetrate_fixed",0)
        if slot == 1:
            effect["cold_seconds"] = board["cold"]
    else:
        bonus = board.get("atk", 0) if (uid,slot)!=(CARN,2) or charged else 0
        attack = mul(attack, ONE+_raw_f32(bonus))
        if uid == CARN and slot == 2:
            effect["root_seconds" if charged else "sluggish_seconds"] = board["attack@root" if charged else "attack@sluggish"]
    scale = _aura_scale(card,event.get("aura_count"))
    hits = {}
    for key, target in event["targets"].items():
        if type(target["eligible"]) is not bool or any(not isfinite(target[x]) for x in ("row","col","radius")) or target["radius"] <= 0:
            raise ValueError("需要目标的可选状态和有效碰撞圆")
        if not target["eligible"] or not circle_in_range(target["row"], target["col"], target["radius"], cells, box_query=True):
            continue
        hp = target["hp_q32"]
        art = ordinary_arts_hit(attack_raw=attack, resistance_raw=target["resistance_q32"]-_raw_f32(resistance_ignore), hp_raw=hp)
        modifier = ONE
        marks = None
        if uid == CARN and slot == 3 and charged:
            owner=event.get("source_instance",CARN)
            per_source=target.get("marks_by_source")
            if per_source is None:
                per_source={owner:target.get("prior_marks",0)}
            stacks = per_source.get(owner,0)
            if type(stacks) is not int or not 0 <= stacks <= 5:
                raise ValueError("食噬之印层数无效")
            marks = min(stacks+1,5)
            if target.get("mark_effect_active",True):
                modifier += marks*_raw_f32(board["attack@damage_scale"])
        damage = mul(mul(art.damage_raw, scale), modifier)
        after = hp - damage
        hit = dict(damage_q32=damage,hp_after_q32=after if after >= HP_EPSILON else 0,
                   status_requests=effect.copy())
        if marks is not None:
            hit["marks_after"] = marks
            hit["marks_by_source"] = {**per_source,owner:marks}
        if uid == SNOW and slot == 1 and target["ground"]:
            hit["status_requests"].update(push_force=board["force"], push_direction=event["placement"]["direction"])
        hits[key] = hit
    return dict(status="resolved", scope="one_ordinary_receiver_arts_impact", attack_q32=attack, cells=cells, hits=hits,
                **({"ramp":ramp} if ramp is not None else {}))


def _snow_tile(card, event):
    if card["operator_id"] != SNOW:
        raise ValueError("积雪为圣聆初雪的天赋")
    board = _talent(card, 0, "1")
    maximum = int(board["max_cast_cnt"])
    layers = event["layers"]
    if type(layers) is not int or not 0 <= layers <= maximum or type(event["permanent"]) is not bool:
        raise ValueError("需要有效的初始层数和常驻/临时地块类型")
    action = event["action"]
    # The caller dispatches a layer pulse only to eligible traversable non-hole
    # ground. Multiple owners have separate tiles even at the same coordinates.
    if action == "deploy":
        if not event["permanent"]:
            raise ValueError("部署只建立常驻积雪")
        layers = int(board["first_snow"])
    elif action == "pulse":
        layers = min(layers + 1, maximum)
    elif action == "affected_enemy_walked_off":
        layers = 0
    elif action != "teleport_off":
        raise ValueError("未知的积雪事件")
    interval = board["interval"]
    if card["skill"] == 3 and event["skill_active"]:
        interval = next(r["value"] for r in card["skill_row"]["blackboard"] if r["key"]=="interval")
    return dict(status="resolved", scope="one_eligible_snow_tile", layers=layers,
                exists=event["permanent"] or layers>0, next_layer_interval_seconds=interval,
                damage_attack_scale_on_entry=board["talent_magic_scale"] if layers else 0)


def execute_phalanx_event(tables, loadout, event):
    uid = loadout["operator_id"]
    if uid not in PHALANX or type(event.get("frame")) is not int or event["frame"] < 0:
        raise ValueError("需指定已接入的法阵术师及非负整数帧")
    kind = event["kind"]
    if kind == "phalanx_activate":
        result = _cast(tables, loadout, event)
    elif kind=="phalanx_deactivate" and uid==CARN:
        if type(event["charged"]) is not bool:
            raise ValueError("技能结束需要本轮蓄力状态")
        base,skill_cap=_carnelian_caps(tables,loadout,event["charged"])
        result=dict(status="resolved",max_hp_q32=base,hp_after_q32=_change_hp_cap(event["hp_before_q32"],skill_cap,base))
    elif kind == "phalanx_sp":
        result = _sp_window(tables, loadout, event)
    elif kind == "phalanx_impact":
        result = _hit(tables, loadout, event)
    elif kind=="phalanx_received_hit":
        from .carnelian_defense import settle_s1_received_hit
        result=settle_s1_received_hit(tables,loadout,event)
    elif kind == "sbell_blessing":
        from .sbell_blessing import run_blessing
        result = run_blessing(tables, loadout, event)
    elif kind == "phalanx_snow_tile":
        result = _snow_tile(loadout, event)
    elif kind == "phalanx_snow_contact":
        from .sbell_snow import run_snow_contact
        result = run_snow_contact(tables,loadout,event)
    elif kind == "phalanx_snow_forward":
        from .sbell_snow import run_s1_snow_forward
        result = run_s1_snow_forward(tables,loadout,event)
    elif kind == "phalanx_snow_outward":
        from .sbell_snow import run_s2_snow_outward
        result = run_s2_snow_outward(tables,loadout,event)
    elif kind == "phalanx_attraction":
        from .sbell_attraction import run_s3_attraction
        result = run_s3_attraction(tables,loadout,event)
    else:
        raise ValueError(f"未知的法阵术师事件 {kind!r}")
    return dict(**result, frame=event["frame"], skill_id=loadout["skill_id"], prts=f"https://prts.wiki/index.php?oldid={PRTS[uid]}")
