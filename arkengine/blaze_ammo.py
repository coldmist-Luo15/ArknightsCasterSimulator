"""Blaze S3 from deployment through the first delayed ammunition impact on one ground enemy."""
from .attack_range import circle_in_range, range_cells
from .caster_ammo import CasterAmmoCounter
from .caster_equipment_traits import _trait_values
from .caster_prefabs import caster_prefab_evidence
from .caster_opening import deploy_to_first_cast
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_casters import BLAZE, _board
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .volley_damage import _raw_f32


def run_blaze_ammo(tables,card,event):
    if card["operator_id"]!=BLAZE or card["skill"]!=3 or not card["module"]:
        raise ValueError("需要烛煌三技能和所选常规模组")
    start,enemy,place = event["frame"],event["enemy"],event["placement"]
    cells = range_cells(card["skill_range"],place,event["rows"],event["cols"])
    if not circle_in_range(enemy["row"],enemy["col"],enemy["radius"],cells,box_query=True):
        raise ValueError("目标在三技能攻击范围外")
    panel = card["panel_attributes"]
    source_hp_max = _raw_f32(panel["maxHp"])
    source_hp = event.get("source_hp_q32",source_hp_max)
    if type(source_hp) is not int or not HP_EPSILON<source_hp<=source_hp_max:
        raise ValueError("需要存活、未倒地的烛煌和有效的当前生命值")
    ev = caster_prefab_evidence(tables,BLAZE,3,skill_level=card["skill_level"],module_id=card["module"],
                                module_level=card["module_level"])
    board = {v["key"]:v["value"] for v in ev["skill_blackboard"]}
    attack, = [c["fields"] for c in ev["character"]["components"] if c["path"]=="Modes/S3/Body/Attack" and c["script"]=="RangedAttack"]
    if (attack["_waitForAttackEvent"] or attack["_useMountGroup"] or attack["_animKey"] or attack["_preDelay"]
            or attack["_waitForProjectileInvalid"] or attack["_atkScale"]!=1 or not attack["_useCachedAtkOnly"]
            or [b["templateKey"] for b in attack["_activeBuffs"]]!=["blaze2_s_3[additional_damage]"]):
        raise ValueError("三技能首发的空动作或弹体伤害变化")
    projectile = attack["_projectileKey"]
    delays = set()
    for variant in ev["projectiles"][projectile].values():
        parts = {r["script"]:r["fields"] for r in variant["components"] if r["active"] and r["enabled"]}
        body,move,hit = parts["SimpleProjectile"],parts["AttachToTarget"],parts["HitBehaviour"]
        if (not move["_immediatelyReach"] or move["_delayAfterReached"] or move["_followTarget"]
                or body["_lifeTimeType"]!=1 or body["_getLifeTimeFromBB"] or body["_lifeTime"]!=move["_delayToStart"]
                or not hit["_onlyCheckHitWhenStop"] or hit["_exceptTraceTarget"] or not body["_alwaysHitTraceTargetInTheEnd"]):
            raise ValueError("三技能附着弹体的寿命、延时或主目标命中变化")
        delays.add((body["_lifeTime"],parts["CircleCollider2D"]["m_Radius"]))
    (life,radius), = delays
    counter = CasterAmmoCounter(ev)
    opening = deploy_to_first_cast(tables,card,ev,deploy_frame=start,direction=place["direction"],facing=event.get("view","Front"))
    rows = [dict(kind="deployment",frame=start),dict(kind="first_ready",frame=opening.first_ready_frame)]
    cast = opening.cast
    if cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("没有到达三技能模式")
    rows.extend(cast["events"])
    mode = cast["spell_frame"]
    release = mode+1
    state = EnemyElements.from_scene_enemy(tables,enemy)
    stop = release+(_raw_f32(life)+state.step-1)//state.step
    extra_atk = 0
    trait = _trait_values(card)
    talent = _board(card,0,"1")
    attack_base = _raw_f32(panel["atk"])
    module_scale = _raw_f32(trait.get("damage_scale",1))
    # The optional external packet lands immediately before the first cast starts.
    # This keeps live and projectile-cached ATK equal throughout the first flight.
    for frame in range(mode,stop+1):
        state.tick(frame)
        if frame==release:
            if "external_fire_before_first_attack_q32" in event:
                state.apply_buildup(frame,"FIRE",event["external_fire_before_first_attack_q32"])
                if state.burst=="FIRE":
                    counter.recover(frame,int(board["ammo_recover"]),skip_limit_check=True)
                    extra_atk = _raw_f32(trait.get("atk",0))
                    effective = mul(attack_base,ONE+_raw_f32(board["atk"])+extra_atk)
                    source_hp = min(source_hp_max,source_hp+mul(source_hp_max,_raw_f32(talent["hp_ratio"])))
                    state.apply_elemental_hp(frame,mul(effective,_raw_f32(talent["ep_damage_scale"])),
                                             source="blaze_burst_talent",output_scale_q32=module_scale)
                    rows.append(dict(kind="external_fire_break",frame=frame,source_hp_after_q32=source_hp))
                if state.hp<=HP_EPSILON:
                    break
            counter.ability_event(frame,"spell_on")
            counter.ability_event(frame,"spell_end")
            rows.append(dict(kind="projectile_release",frame=frame,projectile=projectile,remaining=counter.remaining))
        if frame==stop:
            effective = mul(attack_base,ONE+_raw_f32(board["atk"])+extra_atk)
            burning = state.burst=="FIRE"
            extra = None
            if burning:
                extra = state.apply_elemental_hp(frame,mul(effective,_raw_f32(board["attack@atk_scale"])),
                                                 source="blaze_s3",output_scale_q32=module_scale)
            arts = 0
            if state.hp>HP_EPSILON:
                hit = ordinary_arts_hit(attack_raw=effective,resistance_raw=max(0,enemy["arts_resistance_q32"]+state.res_add),
                                        hp_raw=state.hp)
                arts = mul(hit.damage_raw,module_scale if burning else ONE)
                state.hp = max(0,state.hp-arts)
                if state.hp<HP_EPSILON:
                    state.hp = 0
            rows.append(dict(kind="first_impact",frame=frame,center=(enemy["row"],enemy["col"]),radius=radius,
                             elemental=extra,arts_damage_q32=arts,hp_after_q32=state.hp))
    return dict(status="blaze_s3_first_ammo_scene",through_frame=frame,events=rows,receiver=state.snapshot(),
                element_events=state.events,ammunition_at_first_release=counter.snapshot(),source_hp_q32=source_hp,
                conditions=["primary_enemy_enters_on_first_ready","stationary_single_receiver","through_first_impact_in_objects",
                            "no_external_healing_or_modifiers","before_first_caster_hp_loss_tick"])
