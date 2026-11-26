"""Blaze S2's first attack and its ground field, before her next attack begins."""
from math import floor

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_equipment_traits import _trait_values
from .caster_prefabs import caster_prefab_evidence
from .caster_opening import deploy_to_first_cast
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_casters import BLAZE, _board
from .elemental_projectiles import stationary_ranged_shot
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .mystic_timing import fixed_wait_frames
from .skill_recovery import READY_EPSILON
from .volley_damage import _raw_f32


def fire_tile(row, col):
    return floor(row + .5), floor(col + .5)


def run_blaze_field(tables,card,event):
    if card["operator_id"]!=BLAZE or card["skill"]!=2 or not card["module"]:
        raise ValueError("需要烛煌二技能和所选常规模组")
    enemy,place,face = event["enemy"],event["placement"],event.get("view","Front")
    if any(type(enemy[v]) is not int for v in ("row","col")):
        raise ValueError("单人火场场景要求未移动的敌人处于格心")
    radius = enemy.get("radius") or tables.enemy_circle(enemy["prefab"])
    cells = range_cells(card["skill_range"] or card["base_range"],place,event["rows"],event["cols"])
    if not circle_in_range(enemy["row"],enemy["col"],radius,cells,box_query=True):
        raise ValueError("火场目标没有进入攻击范围")
    ev = caster_prefab_evidence(tables,BLAZE,2,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    parts = [c for c in ev["character"]["components"] if c["active"] and c["enabled"]]
    attack, = [r["fields"] for r in parts if r["script"]=="RangedAttack" and r["path"]=="Modes/S2/Body/Attack"]
    self_buff, = [r["fields"] for r in parts if r["script"]=="BuffToOwner" and r["path"]=="Modes/S2/Body/Attack"]
    bb = {r["key"]:r["value"] for r in ev["skill_blackboard"]}
    if (self_buff["_runActionOnEvent"]!=2 or self_buff["_buffs"][0]["buffKey"]!="blaze2_s_2[self_damage]" or
            attack["_activeBuffs"][0]["templateKey"]!="blaze2_s_2[tile_trigger]" or not attack["_waitForAttackEvent"]):
        raise ValueError("普通能力的自损回调或生成火场的命中动作变化")
    listener, = [r["fields"] for r in parts if r["script"]=="BuffToEnemyTileListener" and
                 r["path"]=="Modes/S2/Body/Talents/3" and r["fields"]["_buffsWhenCasted"]]
    buff, = listener["_buffsWhenCasted"]
    interval, = [r["value"] for r in buff["blackboard"] if r["key"]=="interval"]
    tile_ability, = [r["fields"] for r in parts if r["script"]=="AttachListenerToTileAbility" and r["path"]=="Modes/S2/Body/Talents/3"]
    shape, = [r["fields"] for r in parts if r["script"]=="ConstBoxRange" and r["path"]=="Modes/S2/Body/Talents/3/Selector"]
    if (buff["templateKey"]!="blaze2_s_2[on_tile]" or buff["waitFirstTriggerInterval"] or tile_ability["_fixCastToTarget"]
            or shape["_rangeId"]!="0-1" or tile_ability["_metadata"]["namedAsAlias"]!="DoTile"):
        raise ValueError("火场监听或首跳时钟变化")
    t = tables.read("battle/buff_template_data.json")
    trigger, = t["blaze2_s_2[tile_trigger]"]["eventToActions"]["ON_BUFF_START"]
    field = t["blaze2_s_2[on_tile]"]["eventToActions"]["ON_BUFF_TRIGGER"]
    if trigger["_abilityName"]!="DoTile" or trigger["_castDirectly"] or not field[0]["_assignRealDamageToBB"]:
        raise ValueError("火场生成或真实法伤回读变化")
    panel = card["panel_attributes"]
    dt = tables.fixed_timestep()
    period = single((single(panel["baseAttackTime"])+single(bb["base_attack_time"]))*100/single(panel["attackSpeed"]))
    opening = deploy_to_first_cast(tables,card,ev,deploy_frame=event["frame"],direction=place["direction"],facing=face)
    cast = opening.cast
    first = cast["spell_frame"]+1
    shot = stationary_ranged_shot(tables,ev,attack,attack_frame=first,attack_interval=period,placement=place,enemy=enemy,
                                  facing=face,round_position=event["round_position"],mode_path="Modes/S2",max_flight_ticks=32)
    release,hit,flight = shot["release_frame"],shot["reach_frame"],shot["flight"]
    end = first+fixed_wait_frames(single(period/dt))-1
    state = EnemyElements.from_scene_enemy(tables,enemy)
    hp_max = _raw_f32(panel["maxHp"])
    source_hp = hp_max-mul(hp_max,_raw_f32(bb["attack@hp_ratio"]))
    strength = _raw_f32(panel["atk"])
    trait,talent = _trait_values(card),_board(card,0,"1")
    bonus = 0
    events = [dict(kind="deployment",frame=event["frame"]),dict(kind="first_ready",frame=opening.first_ready_frame),*cast["events"],
              dict(kind="attack_start",frame=first,hp_after_q32=source_hp), dict(kind="projectile_release",frame=release)]
    if "initial_external_fire_q32" in event:
        state.apply_buildup(first,"FIRE",event["initial_external_fire_q32"])
        if state.burst:
            raise ValueError("初始外来源只支持尚未爆发的灼燃条")
    timer = 0
    for frame in range(hit,end+1):
        state.tick(frame)
        main = frame==hit
        if not main:
            timer -= state.step
            if timer>=READY_EPSILON:
                continue
            timer += _raw_f32(interval)
        before = state.hp
        effective = mul(strength,ONE+_raw_f32(bb["atk"])+bonus)
        damage = ordinary_arts_hit(attack_raw=effective if main else mul(effective,_raw_f32(bb["atk_scale"])),
                    resistance_raw=max(0,enemy["arts_resistance_q32"]+state.res_add),hp_raw=before)
        burn = state.burst=="FIRE"
        amount = mul(damage.damage_raw,_raw_f32(trait.get("damage_scale",1)) if burn else ONE)
        state.hp = max(0,before-amount)
        if state.hp<HP_EPSILON:
            state.hp = 0
        row = dict(kind="primary" if main else "field_pulse",frame=frame,arts_damage_q32=amount)
        if main:
            row.update(ignited_tile=(enemy["row"],enemy["col"]),move_speed_ratio=bb["move_speed"])
        else:
            state.apply_buildup(frame,"FIRE",mul(before-state.hp,_raw_f32(bb["element_damage_scale"])))
            if not burn and state.burst=="FIRE":
                bonus = _raw_f32(trait.get("atk",0))
                boosted = mul(strength,ONE+_raw_f32(bb["atk"])+bonus)
                source_hp = min(hp_max,source_hp+mul(hp_max,_raw_f32(talent["hp_ratio"])))
                row["burst_talent"] = state.apply_elemental_hp(frame,mul(boosted,_raw_f32(talent["ep_damage_scale"])),
                    source="blaze_burst_talent",output_scale_q32=_raw_f32(trait.get("damage_scale",1)))
        events.append(dict(row,hp_after_q32=state.hp))
        if state.hp<=HP_EPSILON:
            break
    return dict(status="blaze_s2_first_field_scene",through_frame=frame,events=events,element_events=state.events,
                receiver=state.snapshot(),source_hp_q32=source_hp,flight=flight,
                conditions=["first_volley_only","ground_enemy_at_tile_center","enemy_updates_before_new_projectile",
                            "no_external_healing_or_damage","ends_before_second_attack"])
