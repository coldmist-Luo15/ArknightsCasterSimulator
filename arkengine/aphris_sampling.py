"""V079 Sampling Optimization on a deployed ally delivering ordinary melee Arts hits."""
from math import isfinite

from .animation_assets import read_skin_actions
from .aphris_relays import AphrisRelays
from .area_caster_skills import sampling_recipients
from .attack_range import range_cells
from .baked_mount import single
from .caster_prefabs import _character_events, _prefab, sample_clip_events
from .caster_runtime import current_talents
from .damage import HP_EPSILON, ordinary_arts_hit
from .mystic_timing import fixed_wait_frames
from .volley_damage import _raw_f32


def run_sampling_ally_combat(tables,card,evidence,event):
    """First acquired attack through a steady target. Relay removal updates the owner's aura."""
    ally,enemy,place = event["ally"],event["target"],event["placement"]
    first,end = event["frame"],event["end_frame"]
    if type(end) is not int or end<first:
        raise ValueError("友方法术攻击需要有效的局部观察窗")
    uid = ally["operator_id"]
    unit = tables.read("excel/character_table.json")[uid]
    if (unit["subProfessionId"]!="artsfghter" or ally["elite"]!=0 or ally["potential"]!=1 or ally["trust"]!=0 or
            ally.get("module") is not None or current_talents(unit,ally)):
        raise ValueError("此普通受益者需要无潜能信赖修正、无模组及未解锁其他天赋的精零术战者")
    phase = unit["phases"][ally["elite"]]
    attribute, = (r["data"] for r in phase["attributesKeyFrames"] if r["level"]==ally["level"])
    ability, = (c["fields"] for c in _prefab(tables,"characters",uid)["components"] if
                c["path"]=="Modes/Default/Body/Attack" and c["script"]=="MeleeAttack" and c["enabled"] and c["active"])
    if (ability["_damageType"]!=2 or ability["_atkScale"]!=1 or ability["_activeBuffs"] or ability["_extraDamageType"] or
            ability["_timeMode"]!=0 or not ability["_waitForAttackEvent"] or ability["_preDelay"] or ability["_escapeTime"]):
        raise ValueError("默认攻击需要单次普通法术近战且无额外附着能力")
    cells = range_cells(tables.read("excel/range_table.json")[phase["rangeId"]],ally,event["rows"],event["cols"])
    if ((enemy["row"],enemy["col"]) not in cells or (ally["row"],ally["col"])==(place["row"],place["col"]) or
            any(type(enemy[k]) not in (float,int) or not isfinite(enemy[k]) for k in ("hp","resistance")) or
            enemy["hp"]<=0):
        raise ValueError("局部首发需要持续在友军攻击范围内的普通存活地面目标")
    action = (ability["_upAnimKey"] if ally["direction"]=="UP" else ability["_downAnimKey"] if ally["direction"]=="DOWN"
              else "") or ability["_animKey"]
    facing = "Back" if ally["direction"]=="UP" else "Front"
    skin = read_skin_actions(tables,"chararts/"+uid+".ab","defaultskin",(action,))
    clip = skin["views"][facing.lower()]["actions"][action]
    interval = single(single(attribute["baseAttackTime"])*100/single(attribute["attackSpeed"]))
    scale = max(single(.1), single(interval/clip["duration"]))
    if ability["_maxAnimScale"]>=0:
        scale = min(scale,ability["_maxAnimScale"])
    if clip["loop"] or clip["speed"]!=1:
        raise ValueError("当前受益者的普通攻击动画需要另行取证")
    samples = sample_clip_events(_character_events(tables,uid,"defaultskin"),facing,clip["clip"],
                                 playback_speed=single(1/scale),timestep=tables.fixed_timestep())
    key, = (s["clip_tick"] for s in samples if s["event"]=="OnAttack")
    period = fixed_wait_frames(single(interval/tables.fixed_timestep()))
    relay_card = AphrisRelays(tables,card,evidence)
    occupied = {(ally["row"],ally["col"]),(place["row"],place["col"])}
    for relay in event["relays"]:
        born = relay["deployed_frame"]
        if (type(born) is not int or born>first or born<0 or born+relay_card.duration<=first or not relay_card.ready
                or (relay["row"],relay["col"]) in occupied or relay["relay"] in relay_card.active):
            raise ValueError("初始中继器状态与常态卡片、格子或持续时间冲突")
        relay_card.stock -= 1
        relay_card.active[relay["relay"]] = dict(relay,expires_frame=born+relay_card.duration)
    withdrawals = {}
    for request in event.get("withdrawals",[]):
        if type(request["frame"]) is not int or not first<=request["frame"]<=end:
            raise ValueError("中继器撤回须在局部观察窗内")
        withdrawals.setdefault(request["frame"],[]).append(request["relay"])
    target = dict(ally,id=uid)
    logs,recipients = [],{}
    hp = _raw_f32(enemy["hp"])
    attack = _raw_f32(attribute["atk"])
    resistance = _raw_f32(enemy["resistance"])
    layout = None
    for frame in range(first,end+1):
        relay_card.tick(frame)
        for ident in withdrawals.get(frame,()):
            relay_card.withdraw(frame,ident,"manual")
        live = tuple(relay_card.active)
        if layout != live:
            relays = list(relay_card.active.values())
            _,recipients = sampling_recipients(tables,card,evidence,dict(event,skill_active=False,relays=relays,
                                               allied_turns=[target,*relays],targets=[target]))
            layout = live
            logs.append(dict(frame=frame,kind="sampling_aura_recalculated",recipient=uid,
                             fixed_res_penetration=recipients[uid]))
        if (frame-first)%period==0:
            logs.append(dict(frame=frame,kind="ally_attack_start",operator_id=uid))
        if frame>=first+key and (frame-first-key)%period==0:
            penetration = recipients[uid]
            hit = ordinary_arts_hit(attack_raw=attack,resistance_raw=resistance,hp_raw=hp,
                                    fixed_res_penetration_raw=_raw_f32(penetration))
            logs.append(dict(frame=frame,kind="ally_arts_impact",operator_id=uid,fixed_res_penetration=penetration,
                             resistance=enemy["resistance"],attack_q32=attack,damage_q32=hit.damage_raw,
                             hp_before_q32=hp,hp_after_q32=hit.hp_after_raw))
            hp = hit.hp_after_raw
            if hp<=HP_EPSILON:
                break
    return dict(status="receiver_defeated" if hp<=HP_EPSILON else "through_horizon",events=logs,
                relay_events=relay_card.events,through_frame=frame,hp_after_q32=hp,attack_period=period,
                conditions=["owner_deployed_skill_inactive","accepted_first_attack_continuous_unblocked_target",
                            "allied_caster_has_no_other_active_modifiers"])
