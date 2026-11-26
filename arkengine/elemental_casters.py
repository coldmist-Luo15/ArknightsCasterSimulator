"""Elementalist and ritualist skill events on an explicitly supplied battle snapshot.

Damage packets here are attack values before target mitigation. Elemental buildup
and elemental HP damage are distinct. The parent battle owns receiver defenses,
burst thresholds, splash overlap and projectile arrival.
"""
from .fixed import mul
from .module_effects import active_board, talent_rows
from .volley_damage import _raw_f32


NYMPH = "char_4146_nymph"
BLAZE = "char_1040_blaze2"
MANTRA = "char_4204_mantra"
NECRAS = "char_450_necras"
REVISIONS = {NYMPH:446578, BLAZE:446744, MANTRA:446746, NECRAS:446634}


def _nat(value, name, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{name}超出范围")
    return value


def _board(card, talent_index, prefab):
    rows = talent_rows(card["module_phase"] or {}, talent_index, prefab,
                       [r for r in card["base_talents"] if r["prefabKey"] == prefab])
    return active_board(rows,card["elite"],card["level"],card["potential"])


def _packet(event, kind, scale):
    # Includes this source's skill, talent and other attack-stat changes at its
    # sampling point. Blaze S1 uses the ATK cached when the ally obtained the ring.
    attack = _nat(event["effective_atk_q32"], "此次攻击取样的攻击力")
    return dict(type=kind, power_q32=mul(attack,_raw_f32(scale)))


def _delta(event, ratio, element):
    before = _nat(event["hp_before_arts_q32"], "命中前生命值")
    after = _nat(event["hp_after_arts_q32"], "命中后生命值")
    return dict(type="buildup", element=element, amount_before_receiver_modifiers_q32=mul(abs(before-after),_raw_f32(ratio)))


def _nymph(tables, card, event, bb):
    slot = card["skill"]
    action = event["action"]
    if action == "burst_start":
        talent = _board(card,1,"2")
        count = _nat(event["prior_stacks"], "窥心钥层数",int(talent["max_stack_cnt"]))
        # The global trigger replaces the range-limited base talent at X level 2.
        global_trigger = card["module"] == "uniequip_002_nymph" and card["module_level"] >= 2
        if (global_trigger or event["in_attack_range"]) and event["burst_type"]=="DARK":
            count = min(int(talent["max_stack_cnt"]),count+1)
        return dict(stacks_after=count, atk_add_ratio=count*talent["atk"],
                    attack_speed=talent.get("attack_speed",0) if count>=talent.get("stack_cnt_check",0) else 0)
    if slot == 2 and action == "after_arts":
        node = tables.read("battle/buff_template_data.json")["nymph_s_2[damage]"]["eventToActions"]["ON_BUFF_START"]
        if not any(n.get("_assignRealDamageToBB") for n in node):
            raise ValueError("未找到伤害前后生命差的取样")
        return dict(packets=[_delta(event, bb["ep_damage_ratio"], "DARK")])
    if action != "hit":
        raise ValueError("未知的妮芙技能事件")
    dark = event["burst_type"] == "DARK"
    if slot == 1:
        packets = [_packet(event,"elemental_hp",bb["attack@extra_ep_damage_scale"])] if dark else []
        packets.append(_packet(event,"arts",1))
        return dict(packets=packets, buildup_ratio_from_arts=bb["attack@ep_damage_ratio"])
    if slot == 2:
        stage = event["stage"]
        if stage not in ("primary","splash"):
            raise ValueError("需区分主目标与后续溅射")
        return dict(packets=[_packet(event,"arts",bb["atk_scale"])],
                    fear_request_seconds=bb["fear"] if stage=="primary" and event["enemy_kind"]=="enemy" else 0,
                    secondary_delay_seconds=.33 if stage=="primary" else None,
                    secondary_radius=bb["projectile_range"] if stage=="primary" else None,
                    enhanced_talent_scale_if_dark_after_hit=bb["element_atk_scale"])
    from .caster_prefabs import caster_prefab_evidence
    from .nymph_impact import s3_damage_scales
    spec = caster_prefab_evidence(tables, NYMPH, 3, skill_level=card["skill_level"],
                                 module_id=card["module"], module_level=card["module_level"])
    ability, = [c["fields"] for c in spec["character"]["components"] if c["path"] == "Modes/S3/Body/Attack"
                and c["script"] == "RangedAttackWithExtraActions"]
    normal, dark_magic, dark_element = s3_damage_scales(ability, bb)
    attack = _nat(event["effective_atk_q32"], "此次攻击取样的攻击力")
    packets = [dict(type="arts", power_q32=mul(attack, dark_magic if dark else normal))]
    if dark:
        packets.append(dict(type="elemental_hp", power_q32=mul(attack, dark_element)))
    return dict(packets=packets, maximum_targets=int(bb["attack@max_target"]))


def _blaze(tables, card, event, bb):
    slot, action = card["skill"], event["action"]
    if action == "burn_burst_start":
        talent = _board(card,0,"1")
        out = dict(packets=[_packet(event,"elemental_hp",talent["ep_damage_scale"])], heal_max_hp_ratio=talent["hp_ratio"])
        if slot == 3:
            maximum = int(bb["attack@trigger_time"])
            ammo = _nat(event["ammo"], "技能剩余弹药", maximum)
            out["ammo_after"] = min(ammo+int(bb["ammo_recover"]),maximum)
        boost = _board(card,-1,"10")
        if "atk" in boost:
            out.update(atk_buff_ratio=boost["atk"], atk_buff_seconds=boost["duration"])
        return out
    if slot in (1,2) and action in ("field_hit","after_field_arts"):
        if slot==2 and event["airborne"]:
            return dict(packets=[])
        if action=="after_field_arts":
            key = "element_multiplier" if slot==1 else "element_damage_scale"
            return dict(packets=[_delta(event,bb[key],"FIRE")])
        packet = _packet(event,"arts",bb["atk_scale"])
        if slot==1:
            node, = tables.read("battle/buff_template_data.json")["blaze2_s_1"]["eventToActions"]["ON_BUFF_TRIGGER"]
            return dict(packets=[packet], buff_circle_radius=node["_rangeRadius"], prts_center_radius=1.7,
                        geometry_status="effective_contact_unresolved", interval_seconds=bb["interval"],
                        duration_seconds=bb["max_duration"], attack_snapshot="on_ring_application")
        return dict(packets=[packet], move_speed_modifier=bb["move_speed"])
    if slot==3 and action=="hit":
        packets = [_packet(event,"elemental_hp",bb["attack@atk_scale"])] if event["burst_type"]=="FIRE" else []
        return dict(packets=packets+[_packet(event,"arts",1)], splash_radius=1.7)
    if slot==2 and action=="attack_start" or slot==3 and action=="hp_loss_tick":
        ratio = bb["attack@hp_ratio"] if slot==2 else bb["lose_hp_scale"]
        loss = mul(_nat(event["max_hp_q32"],"当前生命上限"),_raw_f32(ratio))
        return dict(hp_loss_q32=loss,hp_after_q32=max(0,_nat(event["hp_q32"],"当前生命")-loss))
    raise ValueError("未知的烛煌技能事件")


def _mantra(card, event, bb):
    slot, action = card["skill"], event["action"]
    if action=="palsy_trigger":
        board = _board(card,0,"1")
        return dict(packets=[_packet(event,"elemental_hp",board["atk_scale"])], keep_stack_probability=board["prob"])
    if slot in (1,2) and action=="hit":
        bounce = _nat(event.get("bounce",0),"跳跃序号",0 if slot==1 else int(bb["chain_times"]))
        scale = bb["atk_scale"] if slot==1 else bb["attack@chain.atk_scale" if bounce else "attack@atk_scale"]
        result = dict(packets=[_packet(event,"arts",scale)], buildup_element="SANITY",
                      buildup_ratio_from_arts=bb["ep_damage_ratio" if slot==1 else "attack@ep_damage_ratio"])
        if event["burst_type_after_buildup"]=="SANITY":
            ratio = bb["element_atk_scale" if slot==1 else "attack@element_atk_scale"]
            result["following_packet"] = _packet(event,"elemental_hp",ratio)
        return result
    if slot==3 and action=="grant_palsy":
        used = _nat(event["previous_grants"],"已付与次数",int(bb["max_trigger_cnt"]))
        levels = {k:_nat(v,"既有麻痹层数") for k,v in event["eligible_in_grid"].items()}
        apply = bool(levels) and used<int(bb["max_trigger_cnt"])
        per = int(bb["per_active"])
        return dict(grants_after=used+int(apply), debuffs={k:dict(stacks=min(v+per,2),overflow_added=max(0,v+per-2))
                    for k,v in levels.items()} if apply else {})
    if slot==3 and action=="overflow_pulse":
        outstanding = _nat(event["overflow"],"溢出麻痹")
        return dict(projectile_requests=int(outstanding>0), overflow_after=max(0,outstanding-1),
                    interval_seconds=bb["interval_projectile_trigger"], radius=2.0,
                    packet=_packet(event,"elemental_hp",bb["atk_scale"]) if outstanding else None)
    raise ValueError("未知的真言技能事件")


def _necras(card, event, bb):
    slot, action = card["skill"],event["action"]
    if slot==1 and action=="resummon":
        tokens=event["ordinary_tokens_upgraded"]
        if not isinstance(tokens,list) or not all(type(v) is bool for v in tokens):
            raise ValueError("需要每个普通仆役是否升级")
        return dict(summon_attempts=sum(2 if v else 1 for v in tokens) or 1,interval_seconds=.1,
                    stop_on_summon_and_upgrade_cap=True)
    if slot==1 and action=="servant_created_or_upgraded":
        return dict(packets=[_packet(event,"arts",bb["atk_scale"])], radius=bb["range_radius"], center_check=True)
    if slot==2 and action=="tether_tick":
        return dict(packets=[_packet(event,"arts",bb["atk_scale"])] if event["link_active"] else [],
                    interval_seconds=bb["interval"],maximum_targets=int(bb["max_target"]))
    if slot==2 and action=="tethered_target_defeated":
        return dict(additional_summon_attempts=int(bb["additional_token_cnt"]))
    if slot==3 and action=="consume_servant":
        level=_nat(event["special_level"],"特殊仆役等级",int(bb["attack@max_stack_cnt"]))
        consumed=event["consumed"]
        if consumed not in ("none","ordinary","upgraded"):
            raise ValueError("无效的普通仆役状态")
        steps={"none":0,"ordinary":1,"upgraded":2}[consumed]
        maximum=int(bb["attack@max_stack_cnt"])
        after=min(maximum,level+steps)
        return dict(special_level_after=after, block_and_forward_extension_added=after//3-level//3,
                    heal_attempts=steps, heal_max_hp_ratio_each=bb["attack@hp_ratio"], ignores_heal_block=True)
    if slot==3 and action=="skill_attack":
        return dict(packets=[_packet(event,"arts",bb["attack@atk_scale"])],
                    repeat_limit=int(bb["attack@necras_s_3[attack_cnt].max_stack_cnt"]))
    raise ValueError("未知的死芒技能事件")


def execute_elemental_event(tables, loadout, event, *, stream=None,map_stream=None):
    if event["kind"] == "mantra_enemy_attack":
        from .mantra_interrupt import run_mantra_interrupt
        return run_mantra_interrupt(tables, loadout, event, stream)
    """Dispatch a specific ability event, leaving world/elemental settlement to its owner."""
    if event["kind"]=="nymph_s2_scene":
        from .nymph_battle import run_nymph_s2
        return run_nymph_s2(tables,loadout,event,stream=stream,map_stream=map_stream)
    if event["kind"]=="nymph_mode_scene":
        from .nymph_mode import run_nymph_mode
        return run_nymph_mode(tables,loadout,event)
    if event["kind"]=="blaze_s3_scene":
        from .blaze_ammo import run_blaze_ammo
        return run_blaze_ammo(tables,loadout,event)
    if event["kind"]=="blaze_s2_scene":
        from .blaze_field import run_blaze_field
        return run_blaze_field(tables,loadout,event)
    if event["kind"]=="mantra_s2_scene":
        from .mantra_battle import run_mantra_bounce
        return run_mantra_bounce(tables,loadout,event)
    if event["kind"]=="mantra_s3_scene":
        from .mantra_palsy import run_mantra_palsy
        return run_mantra_palsy(tables,loadout,event)
    if event["kind"]=="mantra_impacts":
        from .mantra_battle import run_mantra_impacts
        return run_mantra_impacts(tables,loadout,event)
    if event["kind"]!="elemental_skill" or type(event["frame"]) is not int or event["frame"]<0:
        raise ValueError("需要合法的元素术师技能事件和帧")
    uid=loadout["operator_id"]
    bb={r["key"]:r["value"] for r in loadout["skill_row"]["blackboard"]}
    if uid==NYMPH:
        result=_nymph(tables,loadout,event,bb)
    elif uid==BLAZE:
        result=_blaze(tables,loadout,event,bb)
    elif uid==MANTRA:
        result=_mantra(loadout,event,bb)
    elif uid==NECRAS:
        result=_necras(loadout,event,bb)
    else:
        raise ValueError("干员未绑定此事件")
    return dict(status="resolved_local_event", frame=event["frame"], skill_id=loadout["skill_id"],
                source_module=loadout["module"], prts=f"https://prts.wiki/index.php?oldid={REVISIONS[uid]}",
                packet_stage="before_receiver_and_output_damage_modifiers", **result)
