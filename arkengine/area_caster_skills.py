"""Skill callbacks of the V079 splash and blast casters in the requested roster.

Target lists are accepted selector/impact results. actual_attack and target.res
are the effective values at that damage callback, including applicable talents,
modules and resistance changes. No target or clock is invented here.
"""
from math import isfinite

from .caster_prefabs import caster_prefab_evidence
from .caster_equipment_traits import blast_distance_factor
from .caster_ranges import skill_range_cells
from .damage import ordinary_arts_hit
from .enemy_speed import move_attribute, movement_cap
from .fixed import ONE,mul
from .module_effects import active_board, talent_rows, trait_board
from .volley_damage import _raw_f32


IFRIT, DUSK, MOSTIMA = "char_134_ifrit", "char_2015_dusk", "char_213_mostma"
MARCILLE, APHRIS = "char_4141_marcil", "char_4229_aphris"
PRTS_REVISIONS = {IFRIT:446705, DUSK:446667, MOSTIMA:442621, MARCILLE:446552, APHRIS:446565}
AREA_CALLBACKS = {
    IFRIT:(("activate","arts_output","talent_sp_tick","ifrit_delta_hits"),
           ("attack_hit","burn_tick","arts_output","talent_sp_tick","ifrit_delta_hits"),
           ("channel_hit","self_hp_tick","arts_output","talent_sp_tick","ifrit_delta_hits")),
    DUSK:(("attack_hit",),("activate","attack_hit"),("activate","select","attack_hit","summon_at_impact")),
    MOSTIMA:(("activate","slow_aura"),("channel_hit","slow_aura"),("activate","attack_hit","slow_aura")),
    MARCILLE:(("chant_start","chant_check","attack_hit","heal_output"),("chant_start","chant_check","familiar_cast","familiar_hit"),
                ("chant_start","chant_check","explosion","marcil_blast")),
    APHRIS:(("activate","sampling_aura"),("channel_hit","sampling_aura"),("activate","attack_hit","relay_effect","finish","sampling_aura")),
}


def _num(row,key,minimum=0):
    value = row[key]
    if type(value) not in (int,float) or not isfinite(value) or value < minimum:
        raise ValueError(f"{key} 需要有效数值")
    return value


def _int(row,key):
    value = row[key]
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} 需要非负整数")
    return value


def _flag(row,key):
    value = row[key]
    if type(value) is not bool:
        raise ValueError(f"{key} 需要明确真值")
    return value


def _targets(event):
    rows = event["targets"]
    if (not isinstance(rows,list) or any(not isinstance(r.get("id"),str) or not r["id"] for r in rows)
            or len({r["id"] for r in rows}) != len(rows)):
        raise ValueError("需要经过索敌去重的目标 id 列表")
    return rows


def _hit(event,row,scale=1,output_scale_q32=ONE):
    hp = _int(row,"hp_q32") if "hp_q32" in row else _raw_f32(_num(row,"hp"))
    attack = _int(event,"actual_attack_q32") if "actual_attack_q32" in event else _raw_f32(_num(event,"actual_attack"))
    packet = ordinary_arts_hit(attack_raw=mul(attack,_raw_f32(scale)),
                               hp_raw=hp,resistance_raw=_raw_f32(_num(row,"res",-1000)),output_scale_raw=output_scale_q32)
    return dict(id=row["id"], damage_q32=packet.damage_raw,hp_after_q32=packet.hp_after_raw)


def _component(asset,script):
    match, = [c for c in asset["components"] if c["script"]==script and c["enabled"]]
    return match["fields"]


def _activation(board):
    return dict(attack_ratio=board.get("atk",0),attack_speed_delta=board.get("attack_speed",0),
                base_attack_time_delta=board.get("base_attack_time",0))


def sampling_penetration(card,evidence):
    aura, = (c["fields"] for c in evidence["character"]["components"]
             if c["script"]=="CharacterNonPhysicsAuraAbility" and c["path"].endswith("/Talents/2"))
    validator, = (c["fields"]["_targetOptions"] for c in evidence["character"]["components"]
                  if c["id"]==aura["_targetValidator"]["m_PathID"])
    modifier, = aura["_buffs"][0]["attributes"]["attributeModifiers"]
    if (not aura["_useOwnerAttackRange"] or aura["_selfOption"]!=0 or aura["_interval"] or
            (validator["professionMask"],validator["targetSide"],validator["targetCategory"])!=(639,1,1) or
            (modifier["attributeType"],modifier["formulaItem"],modifier["loadFromBlackboard"])!=(27,0,1)):
        raise ValueError("取样优化的职业、范围或法术穿透加算条件变化")
    talent = next((r for r in card["base_talents"] if r["prefabKey"]=="2"), None)
    if talent is None:
        return 0
    value, = (r["value"] for r in talent["blackboard"] if r["key"]=="magic_resist_penetrate_fixed")
    return value


def sampling_recipients(tables,card,evidence,event):
    geometry=skill_range_cells(tables,card,dict(event,area="sampling_aura"))
    if geometry["status"]!="resolved":
        return geometry,{}
    units=tables.read("excel/character_table.json")
    cells=set(geometry["cells"])
    penetration=sampling_penetration(card,evidence)
    recipients={}
    for row in _targets(event):
        operator=units[row["operator_id"]]
        tile=(_int(row,"row"),_int(row,"col"))
        if tile[0]>=event["rows"] or tile[1]>=event["cols"]:
            raise ValueError("取样优化的友方单位不在地图内")
        eligible=(operator["profession"] not in ("TOKEN","TRAP") and row.get("targetable",True)
                  and row.get("layer",0)==event["placement"].get("layer",0))
        recipients[row["id"]]=penetration if eligible and tile in cells else 0
    return geometry,recipients


def _ifrit(slot, board, evidence, event):
    kind = event["kind"]
    trait = evidence["selected_trait"]
    if kind == "talent_sp_tick":
        talent = evidence["selected_talent"]
        gain = talent["sp"]
        if "ifrit_e_002[dice_sp].prob" in talent and _flag(event,"extra_sp_roll_succeeded"):
            gain += talent["ifrit_e_002[dice_sp].sp"]
        return dict(sp_gain_attempt=gain,period_seconds=talent["interval"])
    if kind == "arts_output":
        # Run from ON_AFTER_OUTPUT_DAMAGE after the actual magical modifier has supplied its value.
        ratio = trait.get("ep_damage_ratio",0)
        return dict(burn_buildup_q32=mul(_raw_f32(_num(event,"resolved_magic_damage")),_raw_f32(ratio)))

    def hit(row,scale):
        factor = ONE
        if "max_dist" in trait and (kind != "burn_tick" or _flag(event,"owner_on_field")):
            factor = blast_distance_factor(trait,_num(row,"distance"))
        return _hit(event,row,scale,factor)
    if slot == 1 and kind == "activate":
        return _activation(board)
    if slot == 2 and kind in ("attack_hit","burn_tick"):
        ability = _component(evidence["skill"],"MeleeAttack")
        buff, = ability["_activeBuffs"]
        rule, = evidence["templates"][buff["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
        if kind == "burn_tick":
            return dict(hits=[hit(row,board[rule["_atkScaleVar"]]) for row in _targets(event)])
        return dict(hits=[hit(row,board[ability["_atkScaleKey"]]) for row in _targets(event)],
                    defense_delta=board["def"], burn_period_seconds=buff["triggerInterval"],
                    burn_buff_duration_seconds=board["duration"])
    if slot == 3 and kind == "channel_hit":
        a = _component(evidence["skill"],"ChannelingMeleeAttack")
        (buff,) = a["_activeBuffs"]
        return dict(hits=[hit(row,board[a["_atkScaleKey"]]) for row in _targets(event)
                          if not _flag(row,"airborne")], resistance_delta=board["magic_resistance"],
                    debuff_seconds=buff["lifeTime"], interval_seconds=a["_triggerDelta"])
    if slot == 3 and kind == "self_hp_tick":
        hp = _int(event,"current_hp_q32") if "current_hp_q32" in event else _raw_f32(_num(event,"current_hp"))
        loss = mul(_raw_f32(_num(event,"max_hp")),_raw_f32(board["hp_ratio"]))
        return dict(pure_damage_q32=loss,hp_after_q32=max(0,hp-loss))
    raise ValueError(f"伊芙利特 S{slot} 不支持 {kind}")


def _dusk(slot, board, evidence, event):
    kind = event["kind"]
    if slot == 3 and kind == "summon_at_impact":
        if _flag(event,"has_freeling"):
            return dict(refresh_lifetime_seconds=25, heal_ratio_of_max_hp=1, ignore_healing_block=True,
                        reset_shield=False)
        return dict(summon="token_10015_dusk_drgn" if _flag(event,"can_deploy_ground") else None)
    if kind == "select" and slot == 3:
        # Candidates arrive in ordinary taunt/hatred order within each priority tier.
        rows = sorted(_targets(event),key=lambda row:_flag(row,"blocked"))
        return dict(primary=rows[0]["id"] if rows else None)
    if kind == "attack_hit":
        hits = []
        for row in _targets(event):
            factor = 1
            if slot == 2:
                condition, modifier = evidence["templates"]["dusk_s_2"]["eventToActions"]["ON_OUTPUT_DAMAGE"]
                if condition["_condType"] != "LT" or not modifier["$type"].startswith("Torappu.Battle.Action.Nodes+DamageScale,"):
                    raise ValueError("夕二技能血线或伤害修正配置不符")
                if _raw_f32(_num(row,"hp")) < mul(_raw_f32(_num(row,"max_hp")),_raw_f32(board["hp_ratio"])):
                    factor = board["damage_scale"]
            hits.append(_hit(event,row,board["atk_scale"] if slot == 1 else 1,_raw_f32(factor)))
        return dict(hits=hits)
    if kind == "activate" and slot in (2,3):
        return _activation(board)
    raise ValueError(f"夕 S{slot} 不支持 {kind}")


def _mostima(slot,board,evidence,event):
    if event["kind"] == "slow_aura":
        talent = evidence["selected_talent"]
        scale = board["talent_scale"] if slot == 3 and _flag(event,"skill_affecting") else 1
        delta = talent["move_speed"]*scale if _flag(event,"in_range") else talent.get("mostma_e_t_2[global].move_speed",0)
        result = dict(move_speed_ratio_delta=delta)
        if "enemy_movement" in event:
            enemy = event["enemy_movement"]
            bounds = {k:enemy[k] for k in ("min_q32","max_q32") if k in enemy}
            attr = move_attribute(enemy["base_speed"],ratio_delta=delta,**bounds)
            result.update(attribute_q32=attr,movement_cap=movement_cap(attr,enemy["level_multiplier"]))
        return result
    if event["kind"] == "activate" and slot in (1,3):
        result = _activation(board)
        if slot == 3:
            result["second_talent_scale"] = board["talent_scale"]
        return result
    if slot == 2 and event["kind"] == "channel_hit":
        a = _component(evidence["skill"],"ChannelingMeleeAttack")
        (buff,) = a["_activeBuffs"]
        if buff["templateKey"]!="empty" or buff["attributes"]["abnormalFlags"]!=[0]:
            raise ValueError("荒时之锁不再是只有眩晕标志的定时状态")
        hits=[]
        for row in _targets(event):
            hit=dict(_hit(event,row,board[a["_atkScaleKey"]]),stun_applied=not _flag(row,"stun_immune"))
            if "enemy_action" in row:
                # The pulse is in COROUTINES. The enemy's next object tick switches
                # ATTACK/COMBAT to STUN and interrupts the still-issued ability.
                action=row["enemy_action"]
                frame=event["frame"]
                spell=action["spell_frame"]
                if (action["state"] not in ("ATTACK","COMBAT") or type(spell) is not int or
                        type(action["start_frame"]) is not int or not action["start_frame"]<=frame or
                        spell<action["start_frame"] or not hit["hp_after_q32"]):
                    raise ValueError("敌方中断回放须为本次命中后存活、已起手的普通攻击或接敌攻击")
                interrupted=hit["stun_applied"]
                # Already emitted attacks, including this frame's normal spell,
                # survive state exit. Palsy has a separate spell gate; stun does not.
                hit["enemy_action"]=dict(state=action["state"],spell_allowed=not interrupted or spell<=frame,
                    windup_interrupted=interrupted and spell>frame,stun_state_frame=frame+1 if interrupted else None)
            hits.append(hit)
        return dict(interval_seconds=a["_triggerDelta"],stun_seconds=buff["lifeTime"],hits=hits)
    if slot == 3 and event["kind"] == "attack_hit":
        return dict(hits=[_hit(event,row) for row in _targets(event)], push_force=board["attack@force"])
    raise ValueError(f"莫斯提马 S{slot} 不支持 {event['kind']}")


def _marcille(slot,board,evidence,event):
    kind = event["kind"]
    if kind in ("chant_check","chant_start"):
        a = _component(evidence["skill"],"ChantBeforeSkill")
        return dict(can_continue=_num(event,"mana")>=board["skill_cost_min_sp"],
                    main_chant_seconds=board["chant_duration"],extra_chant_seconds=board.get("extra_chant_duration",0),
                    recheck_interval=a["_buffsWhenStartChant"][0]["triggerInterval"])
    if slot == 1 and kind in ("attack_hit","heal_output"):
        mana = _num(event,"mana")
        if mana < board["skill_cost_min_sp"]:
            return dict(status="insufficient_mana",stop_skill=True)
        after = mana-board["sp_cost"]
        output = dict(mana_after=after,stop_skill=after<board["skill_cost_min_sp"])
        if kind == "heal_output":
            output["heal_request_q32"] = _raw_f32(_num(event,"actual_attack"))
        else:
            output["hits"] = [_hit(event,row) for row in _targets(event)]
        return output
    if slot == 2 and kind == "familiar_cast":
        mana = _num(event,"mana")
        prior = _int(event,"uses_since_deployment")
        if prior > 1:
            raise ValueError("使魔第二次开启后不能再以此方式升级")
        if mana < board["skill_cost_min_sp"]:
            return dict(status="insufficient_mana")
        return dict(mana_after=mana-board["sp_cost"], upgraded=bool(prior), uses_since_deployment=prior+1,
                    can_manually_stop=not prior,attack_speed_delta=board["attack_speed"] if prior else 0)
    if slot == 2 and kind == "familiar_hit":
        upgraded = _flag(event,"upgraded")
        return dict(hits=[dict(_hit(event,row),sluggish_seconds=board["attack@sluggish"],
                               stun_seconds=board["attack@stun"] if upgraded and row["id"]==event["primary"]
                               and not _flag(row,"stun_immune") else 0) for row in _targets(event)])
    if slot == 3 and kind == "explosion":
        mana = _num(event,"mana")
        full = _flag(event,"extra_chant_completed")
        cost = board["sp_cost_extra"] if full else board["skill_cost_min_sp"]
        if mana < cost:
            return dict(status="insufficient_mana",more_explosions=False)
        return dict(hits=[_hit(event,row,board["atk_scale"]) for row in _targets(event)],mana_after=mana-cost,
                    more_explosions=full and mana-cost>=cost, interval_seconds=board["interval"],
                    shard_stun_seconds=board["stun"])
    raise ValueError(f"玛露西尔 S{slot} 不支持 {kind}")


def _aphris(slot,board,evidence,event):
    kind = event["kind"]
    if slot == 1 and kind == "activate":
        return dict(_activation(board),withdraw_relay_on_timeout=False)
    if slot == 2 and kind == "channel_hit":
        a = _component(evidence["skill"],"ChannelingMeleeAttack")
        return dict(hits=[_hit(event,row,board[a["_atkScaleKey"]]) for row in _targets(event)],
                    interval_seconds=board["interval"])
    if slot == 3 and kind == "activate":
        return dict(_activation(board),forced_disarm_seconds=board["charge_time"],
                    ammo=int(board["attack@trigger_time"]),withdraw_existing_relays=True,
                    extra_relay_slots=int(board["token_recharge_cnt"]),reset_redeploy=True)
    if slot == 3 and kind == "attack_hit":
        ammo = _int(event,"ammo")
        if not 1<=ammo<=int(board["attack@trigger_time"]):
            raise ValueError("中继器技能弹药越界")
        hits = [_hit(event,row,board["attack@atk_scale"]) for row in _targets(event)]
        return dict(hits=hits,ammo_after=ammo-1,finish_when_attack_ends=ammo==1)
    if slot == 3 and kind == "relay_effect":
        # ON_BUFF_START on the receiver is a separate callback from the main
        # melee spell. Do not assume which packet first sees the receiver alive.
        row = event["target"]
        extensions = min(_int(event,"extension_count"),int(board["attack@max_times"]))
        if extensions not in (0,1):
            raise ValueError("当前版本额外伤害次数超出已支持范围")
        return dict(sluggish_seconds=board["attack@origin_sluggish"]+extensions*board["attack@extra_sluggish"],
                    extra_hit=_hit(event,row,board["attack@extra_atk_scale"]) if extensions else None)
    if slot == 3 and kind == "finish":
        return dict(withdraw_all_relays=True)
    raise ValueError(f"谬因 S{slot} 不支持 {kind}")


HANDLERS = {IFRIT:_ifrit,DUSK:_dusk,MOSTIMA:_mostima,MARCILLE:_marcille,APHRIS:_aphris}


def execute_area_skill(tables, loadout, event):
    """Return one selected callback and its new state. Inputs are immutable."""
    uid, slot = loadout["operator_id"],loadout["skill"]
    if uid not in HANDLERS:
        raise ValueError("该干员不使用扩散/轰击术师效果处理器")
    frame = _int(event,"frame")
    evidence = caster_prefab_evidence(tables,uid,slot,skill_level=loadout["skill_level"],module_id=loadout["module"],
                                     module_level=loadout["module_level"])
    if uid==APHRIS and event["kind"]=="sampling_ally_combat":
        from .aphris_sampling import run_sampling_ally_combat
        return run_sampling_ally_combat(tables,loadout,evidence,event)
    if uid==APHRIS and event["kind"]=="sampling_aura":
        geometry,recipients=sampling_recipients(tables,loadout,evidence,event)
        if geometry["status"]!="resolved":
            return dict(frame=frame,**geometry)
        return dict(frame=frame,kind=event["kind"],operator_id=uid,skill_id=loadout["skill_id"],
                    result=dict(cells=geometry["cells"],fixed_res_penetration=recipients),
                    conditions=["deployed_targetable_allies_on_same_layer_and_current_attack_range"])
    if uid==IFRIT and event["kind"]=="ifrit_delta_hits":
        from .ifrit_delta import run_ifrit_delta
        return run_ifrit_delta(tables,loadout,evidence,event)
    if uid==IFRIT and event["kind"]=="ifrit_s2_scorch":
        from .ifrit_scorch import run_ifrit_scorch
        return run_ifrit_scorch(tables,loadout,evidence,event)
    if uid==MARCILLE and event["kind"]=="marcil_blast":
        from .marcil_blast import run_marcil_blast
        return run_marcil_blast(tables,loadout,evidence,event)
    if uid==APHRIS and event["kind"]=="aphris_s3_magazine":
        from .aphris_magazine import run_aphris_magazine
        return run_aphris_magazine(tables,loadout,evidence,event)
    if uid in (IFRIT,MOSTIMA):
        unit = tables.read("excel/character_table.json")[uid]
        phase = loadout["module_phase"] or {}
        rows = talent_rows(phase,1,"2",unit["talents"][1]["candidates"])
        evidence["selected_talent"] = active_board(rows,loadout["elite"],loadout["level"],loadout["potential"])
        if uid == IFRIT:
            evidence["selected_trait"] = trait_board({"trait":unit["trait"] or {"candidates":[]}},phase,loadout)
    board = {b["key"]:b["value"] for b in evidence["skill_blackboard"]}
    result = HANDLERS[uid](slot,board,evidence,event)
    return dict(frame=frame,kind=event["kind"],operator_id=uid,skill_id=evidence["skill_id"],
                module_id=loadout["module"],module_level=loadout["module_level"],result=result,
                prts_revision=PRTS_REVISIONS[uid], scope="single_accepted_effect_callback")
