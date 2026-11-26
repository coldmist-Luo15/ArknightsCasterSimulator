"""Effective release eligibility in the BORN state, then Marcille's chant on the selected input."""
from .animation_assets import read_skin_actions
from .baked_mount import single
from .caster_prefabs import caster_prefab_evidence
from .fixed import ONE, mul
from .damage import HP_EPSILON, ordinary_arts_hit
from .marcil_chant import UID
from .marcil_mana import entry_mana, field_mana, field_mana_after_blast
from .module_effects import active_board, talent_rows
from .caster_start import resolve_cast_start
from .volley_damage import _raw_f32


def companion_deck_buff(tables, card, squad):
    """Evaluate DeckBuffModifier.Preprocess and TryGetDeckBuff for an ordinary squad."""
    if card["operator_id"] != UID:
        raise ValueError("需要玛露西尔的天赋配置")
    members = [row["operator_id"] for row in squad]
    units = tables.read("excel/character_table.json")
    if UID not in members or len(set(members)) != len(members) or any(
            member not in units or units[member]["isNotObtainable"] for member in members):
        raise ValueError("需要包含玛露西尔、无重复的普通干员编队")
    spec = caster_prefab_evidence(tables, UID, card["skill"], skill_level=card["skill_level"],
                                 module_id=card["module"], module_level=card["module_level"])
    fields, = [c["fields"] for c in spec["character"]["components"] if c["script"] == "DeckBuffTalent"
               and c["path"].endswith("/Talents/2") and c["active"] and c["enabled"]]
    options = fields["_options"]
    selector = options["selector"]
    buff, = fields["_deckBuffs"]
    buff = buff["buff"]
    mods = buff["attributes"]["attributeModifiers"]
    if (options["useOtherSelectorWhenGetBuff"] or selector["categoryMask"] != 639 or selector["filterTag"] != "laios"
            or any(v for k, v in selector.items() if k not in ("categoryMask", "filterTag"))
            or buff["buffKey"] != "marcil_t_2[laios]" or
            [(m["attributeType"], m["formulaItem"], m["loadFromBlackboard"], m["fetchBaseValueFromSourceEntity"])
             for m in mods] != [(2, 1, 1, 0), (7, 0, 1, 0)]):
        raise ValueError("该编队筛选器或属性修正已变化")
    talent = talent_rows(card["module_phase"] or {}, 1, "2", [r for r in card["base_talents"] if r["prefabKey"] == "2"])
    board = active_board(talent, card["elite"], card["level"], card["potential"])
    # Fixed ordinary cards: the four relevant records have laios as their teamId.
    recipients = [uid for uid in members if units[uid]["teamId"] == selector["filterTag"]]
    active = len(recipients) >= options["minCntToTrig"] and "attack_speed" in board
    effects = {}
    for row in squad:
        uid = row["operator_id"]
        if not active or uid not in recipients:
            continue
        effect = dict(defense_multiplier_delta_q32=_raw_f32(board["def"]), attack_speed_add_q32=_raw_f32(board["attack_speed"]))
        # Optional stand-alone base values assume no other modifiers in the same bucket.
        if "base_defense_q32" in row:
            effect["defense_q32"] = mul(row["base_defense_q32"], ONE + effect["defense_multiplier_delta_q32"])
        if "base_attack_speed_q32" in row:
            effect["attack_speed_q32"] = row["base_attack_speed_q32"] + effect["attack_speed_add_q32"]
        effects[uid] = effect
    return dict(matching_members=recipients, minimum=options["minCntToTrig"], active=active, affected=effects)


def ordinary_splash(tables, card, event):
    """A committed default-mode spell around its input point, with settled ordinary enemy colliders."""
    from .eyja_ignition import ignition_circle_overlap
    if card["operator_id"] != UID:
        raise ValueError("需要玛露西尔的普通攻击")
    ev = caster_prefab_evidence(tables, UID, card["skill"], skill_level=card["skill_level"],
                               module_id=card["module"], module_level=card["module_level"])
    birth_attack = "Modes/Default/Body/Attack"
    by_id = {c["id"]:c for c in ev["character"]["components"]}
    group, = [c["fields"] for c in by_id.values() if c["path"]==birth_attack and c["script"]=="AbilitySelectableGroup"]
    branches = {}
    for conf in group["_abilityConfigs"]:
        ability = by_id[conf["_ability"]["m_PathID"]]
        trigger = by_id[conf["_trigger"]["m_PathID"]]
        mode = ability["path"].split("/")[-1]
        expected_trigger = "SelectorTriggerWithCertainCondition" if mode=="Power" else "SelectorTrigger"
        fields = ability["fields"]
        selector = by_id[fields["_selector"]["m_PathID"]]
        collider, = [c["fields"] for c in by_id.values() if c["path"]==selector["path"] and c["script"]=="CircleCollider2D"]
        if (ability["script"]!="MeleeAttack" or trigger["script"]!=expected_trigger
                or mode=="Power" and not trigger["fields"]["_checkHasSp"]
                or (fields["_selectTargetSource"],fields["_selectTargetTiming"],fields["_damageType"],fields["_atkScale"])!=(3,1,2,1)
                or selector["fields"]["_limitTargetNum"] or any(collider["m_Offset"].values())):
            raise ValueError("普通攻击的当前魔力筛选、输入点或溅射选择器发生变化")
        branches[mode] = collider["m_Radius"]
    if list(branches)!=["Power","Normal"] or group["_selectAbilitySequentially"]:
        raise ValueError("普通攻击的子技能优先级变化")
    mana = event["mana_at_search_q32"]
    checked_mana = event.get("mana_at_last_checker_q32",mana)
    if any(type(v) is not int or v<0 for v in (mana,checked_mana)):
        raise ValueError("索敌和晚更新检查时的魔力须为非负定点数")
    checker,finish = tables.read("battle/buff_template_data.json")["marcil_t[checker]"]["eventToActions"]["ON_BUFF_LATE_ENABLE"]
    gate = checker["_conditionNode"]
    talent, = [r for r in card["base_talents"] if r["prefabKey"]=="1"]
    values = {b["key"]:b["value"] for b in talent["blackboard"]}
    if (gate["_condType"]!="GE" or gate["_checkKey"]!="sp" or "+FinishBuff," not in finish["$type"]
            or checker["_failNodes"][0]["_buffKey"]!="marcil_t[power]"):
        raise ValueError("才女晚更新的增攻门槛变化")
    powered = checked_mana>=_raw_f32(values[gate["_checkKey"]])
    attack = _raw_f32(card["panel_attributes"]["atk"])
    if powered:
        attack = mul(attack,ONE+_raw_f32(values["atk"]))
    mode = "Power" if mana>0 else "Normal"
    hits = []
    for target in event["targets"]:
        if (not target["eligible"] or target["hp_q32"]<=HP_EPSILON
                or not ignition_circle_overlap(event["input_point"],target,branches[mode],target["radius"])):
            continue
        hit = ordinary_arts_hit(attack_raw=attack,resistance_raw=target["res_q32"],hp_raw=target["hp_q32"])
        hits.append(dict(target=target["id"],damage_q32=hit.damage_raw,hp_after_q32=hit.hp_after_raw))
    return dict(frame=event["frame"],ability=mode,radius=branches[mode],power_buff=powered,attack_q32=attack,hits=hits)


def _birth(tables,card,ev,facing):
    uid = card["operator_id"]
    actor, = [x["fields"] for x in ev["character"]["components"] if x["script"]=="Character" and not x["path"]]
    if actor["m_overrideBornTime"]>0:
        raise ValueError("该角色含动态出生时间覆盖")
    skin_key = ("default_birth_skin",uid)
    if skin_key not in tables._prefab_data:
        tables._prefab_data[skin_key] = read_skin_actions(tables,"chararts/"+uid+".ab","defaultskin",("Born",))
    skin = tables._prefab_data[skin_key]
    anim = skin["views"][facing.lower()]["actions"]["Born"]
    if anim["speed"]!=1 or anim["loop"] or anim["duration"]<=0:
        raise ValueError("出生时装动作需要单独核对")
    speed = 1 if actor["_useRealBornTimeFromAnim"] else anim["duration"]
    duration = single(single(anim["duration"])/single(speed))
    step = int(tables.fixed_timestep()*ONE)
    return dict(duration_q32=int(duration*ONE),step_q32=step,clip=anim,skin_source=skin["source"],
                born_exit_update=(int(duration*ONE)+step-1)//step)


def deployment_skill_gate(tables,card,*,deploy_frame,frame,facing="Front",phase="input",evidence=None):
    """Normal first BORN update after deployment. Effective attempt assumes the other resource/state gates pass."""
    if type(deploy_frame) is not int or type(frame) is not int or not 0<=deploy_frame<=frame:
        raise ValueError("部署和检查帧无效")
    if facing not in ("Front","Back") or phase not in ("input","after_tick","before_objects","auto_skill_tick"):
        raise ValueError("需要有效的朝向和检查相位")
    ev = evidence if evidence is not None else caster_prefab_evidence(tables,card["operator_id"],card["skill"],
                               skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    ctrl, = [x for x in ev["skill"]["components"] if not x["path"] and x["enabled"] and x["active"] and "_playSkillBeginAnim" in x["fields"]]
    f = ctrl["fields"]
    if "_canSwitchDuringBorn" not in f or "_canCastDuringBorn" not in f:
        raise ValueError("该控制器的出生中操作走独立逻辑")
    birth = _birth(tables,card,ev,facing)
    updates = max(0,frame-deploy_frame-int(phase=="before_objects"))
    remaining = max(0,birth["duration_q32"]-updates*birth["step_q32"])
    flag = "_canSwitchDuringBorn" if f["_switchToState"] else "_canCastDuringBorn"
    return dict(frame=frame,phase=phase,born_remaining_q32=remaining,owner_state="born" if remaining else "idle",
                can_deliver_effective_skill=bool(f[flag]) or remaining==0,active_gate=flag,gate_value=bool(f[flag]),
                born_exit_frame=deploy_frame+birth["born_exit_update"],birth=birth)


def run_marcil_deploy(tables,card,event):
    """Run the card clock through the deployment input, or resume an explicit mana snapshot."""
    if card["operator_id"]!=UID:
        raise ValueError("部署入口需要玛露西尔")
    ev = caster_prefab_evidence(tables,UID,card["skill"],skill_level=card["skill_level"],
                               module_id=card["module"],module_level=card["module_level"])
    born_ops = tables.read("battle/buff_template_data.json")["marcil_t"]["eventToActions"]["ON_OWNER_BORN"]
    if ([x["$type"].split("+")[-1].split(",")[0] for x in born_ops] !=
            ["CopyCharacterSharedBlackboard","ClearCharacterSp","TriggerBuff","ModifySpData","ModifySp"] or
            born_ops[-1]["_spString"]!="mana" or not born_ops[-1]["_forceFlag"]):
        raise ValueError("入场继承魔力的 Buff 图发生变化")
    def cast_at_deployment(op, mana):
        return _deployed_cast(tables,card,ev,op,mana)
    mana, source, clock = entry_mana(tables,card,ev,event,cast_at_deployment)
    report = cast_at_deployment(event,mana)
    report["mana_source"] = source
    if clock is not None:
        report["mana_clock"] = clock
    if "squad" in event:
        report["companion_deck_buff"] = companion_deck_buff(tables, card, event["squad"])
    return report


def _deployed_cast(tables,card,ev,event,mana):
    deploy = event["frame"]
    facing = event.get("facing","Front")
    g = deployment_skill_gate(tables,card,deploy_frame=deploy,frame=deploy,facing=facing,evidence=ev)
    gate_frame = g["born_exit_frame"]
    bb = {b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    auto = card["skill_row"]["skillType"]=="AUTO"
    probe = event.get("trigger_frame",gate_frame)
    phase = event.get("phase","auto_skill_tick" if auto else "input")
    auto_check=gate_frame
    if auto and "mostima_aura" in event and mana<bb["skill_cost_min_sp"]:
        config=event["mostima_aura"]
        # All credited points are integral. Scan past the last scheduled change
        # and long enough for the slowest positive rate among admitted sources.
        from .mostima_aura import aura_profile
        from .caster_catalog import resolve_caster_loadout
        source=resolve_caster_loadout(tables,**config["loadout"])
        rate=aura_profile(tables,source)["ally"]
        last=max([gate_frame,*[e["frame"] for e in config.get("changes",[])]])
        tail=int((bb["skill_cost_min_sp"]-mana+1)/(rate*tables.fixed_timestep()))+1 if rate>0 else 0
        first=field_mana(tables,card,config,deploy,last+tail,mana)["first_ready"]
        if first is not None:
            auto_check=max(gate_frame,first)
    if auto:
        if phase!="auto_skill_tick" or ("trigger_frame" in event and probe!=auto_check):
            raise ValueError("二技能在出生与魔力均满足后的角色更新中自动检测，不能指定手动按键")
        probe=auto_check
    if not auto and phase not in ("input","after_tick"):
        raise ValueError("手动技能需给实际输入送达阶段")
    gate = deployment_skill_gate(tables,card,deploy_frame=deploy,frame=probe,facing=facing,phase=phase,evidence=ev)
    on_field = None
    trigger_mana = mana
    first_ready = deploy if mana>=bb["skill_cost_min_sp"] else None
    if "mostima_aura" in event:
        on_field=field_mana(tables,card,event["mostima_aura"],deploy,probe,mana)
        trigger_mana=on_field["mana"]
        first_ready=on_field["first_ready"]
    report = dict(skill_id=card["skill_id"],deploy_frame=deploy,mana_at_entry=mana,
                  minimum_mana=int(bb["skill_cost_min_sp"]),birth_gate=gate,
                  earliest_effective_trigger_frame=max(gate_frame,first_ready) if first_ready is not None else None)
    if on_field is not None:
        report["on_field_mana"]=on_field
    options = dict(event.get("chant_options",{}))
    def start(opts):
        return resolve_cast_start(tables,ev,frame=probe,phase="before_coroutines" if auto else phase,
                                  owner_state=gate["owner_state"],sp=trigger_mana,chant_options=opts,facing=facing)
    if on_field is None:
        result=start(options)
    else:
        # ChantBeforeSkill runs before the root ability casts. Natural recovery remains
        # live through that frame's OBJECTS, even if INPUT ends the extra chant later.
        observed={int(f):v for f,v in options.pop("mana_changes",{}).items()}
        result=start(options)
        until=result.get("cast_frame")
        if until is None:
            until=max((row["frame"] for row in result.get("events",[]) if "frame" in row),default=probe)
        trace=field_mana(tables,card,event["mostima_aura"],deploy,until,mana,observed=observed)
        changes={e["frame"]:e["mana"] for e in trace["credits"] if e["frame"]>probe}
        changes.update(observed)
        if changes:
            result=start(dict(options,mana_changes=changes))
        actual_end=result["cast_frame"] if result["cast_frame"] is not None else max(
            (r["frame"] for r in result["events"] if "frame" in r),default=probe)
        report["chant_mana_credits"]=[e for e in trace["credits"] if probe<e["frame"]<=actual_end]
        commit=result.get("cast_frame")
        if commit is not None:
            # Entity ticks its buffs and SP controller before BasicSkill.Tick registers
            # the stop modifier. One ordinary SP update remains after the coroutine commit.
            after=field_mana(tables,card,event["mostima_aura"],deploy,commit+1,mana,
                             observed=observed,payment=result.get("mana_payment"))
            result["first_object_update_after_commit"]=dict(frame=commit+1,mana=after["mana"],
                    credited=bool(after["credits"] and after["credits"][-1]["frame"]==commit+1))
            if "blast_clock" in result and after["mana"]!=result["mana_at_commit"]:
                from .marcil_blast import blast_clock
                previous=result["blast_clock"]
                updated=blast_clock(tables,ev,result,mana_before_first_payment=after["mana"])
                result["blast_clock"]=updated
                result["events"][-len(previous["events"]):]=updated["events"]
            if "end_frame" in event and "blast_clock" in result:
                end=event["end_frame"]
                if type(end) is not int or end<probe:
                    raise ValueError("场上魔力观察终点需晚于技能输入")
                report["post_cast_mana"]=field_mana_after_blast(
                    tables,card,event["mostima_aura"],deploy,end,mana,result,observed=observed)
    if card["skill"]==3 and "blast_clock" in result:
        result["talent_first_update_frame"]=deploy+1
    return dict(**report,activation=result)
