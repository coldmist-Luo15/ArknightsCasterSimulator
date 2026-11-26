"""Mantra S3 against a single visible waiting enemy, through the first skill attack."""
from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_prefabs import caster_prefab_evidence
from .caster_opening import deploy_to_first_cast
from .damage import ordinary_arts_hit
from .elemental_casters import MANTRA
from .elemental_projectiles import stationary_ranged_shot
from .fixed import ONE, mul
from .enemy_elements import EnemyElements
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def run_mantra_palsy(tables,card,event):
    if card["operator_id"]!=MANTRA or card["skill"]!=3 or not card["module"]:
        raise ValueError("需要真言三技能和所选常规模组")
    enemy,place,face = event["enemy"],event["placement"],event.get("view","Front")
    cells = range_cells(card["skill_range"],place,event["rows"],event["cols"])
    radius = enemy.get("radius") or tables.enemy_circle(enemy["prefab"])
    if (type(enemy["palsy_immune"]) is not bool or not circle_in_range(enemy["row"],enemy["col"],radius,cells,box_query=True)
            or enemy.get("initial_palsy_stacks",0)!=0):
        raise ValueError("此场景须用技能范围内初始未叠麻痹的静止目标")
    ev = caster_prefab_evidence(tables,MANTRA,3,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    parts = [r for r in ev["skill"]["components"] if r["active"] and r["enabled"]]
    active, = [r["fields"] for r in parts if r["script"]=="ActiveBuffAbility" and not r["path"]]
    grant, = [b for b in active["_buffs"] if b["templateKey"]=="mantra_s_3[self]"]
    if not grant["waitFirstTriggerInterval"] or grant["triggerCnt"]!=1 or grant["firstTriggerInterval"]<=0:
        raise ValueError("真言自身施法后的首次麻痹授予时钟变化")
    templates = tables.read("battle/buff_template_data.json")
    add,loop = templates["mantra_s_3[self]"]["eventToActions"]["ON_BUFF_TRIGGER"][0]["_succeedNodes"]
    if (add["_blackboardKey"]!="all_cnt" or loop["_loopCntKey"]!="per_active"
            or loop["_loopBody"][0]["_buffs"][0]["buffKey"]!="palsy[stack]"):
        raise ValueError("范围麻痹的层数或次数黑板变化")
    cap, = templates["mantra_s_3[enemy]"]["eventToActions"]["ON_BUFF_TRIGGER"]
    if not cap["_isLimitAppend"]:
        raise ValueError("范围内的麻痹上限不再使用追加覆盖")
    bb = {row["key"]:row["value"] for row in ev["skill_blackboard"]}
    body = [r for r in ev["character"]["components"] if r["active"] and r["enabled"]]
    a, = [r["fields"] for r in body if r["script"]=="RangedAttack" and r["path"]=="Modes/S3/Body/Attack"]
    projectile = a["_projectileKey"]
    for variant in ev["projectiles"][projectile].values():
        chain, = [r["fields"] for r in variant["components"] if r["script"]=="ChainLightningHitBehaviour" and r["active"]]
        if (not chain["_onlyCheckHitWhenReachTarget"] or chain["_exceptTraceTarget"] or chain["_waitFirstPeriod"]
                or chain["_onlyCheckHitWhenStop"] or chain["_interval"]):
            raise ValueError("主弹不再在到达时查询命中目标")
    opening = deploy_to_first_cast(tables,card,ev,deploy_frame=event["frame"],direction=place["direction"],facing=face)
    cast = opening.cast
    mode = cast["spell_frame"]
    first = mode+1
    panel = card["panel_attributes"]
    interval = single(single(panel["baseAttackTime"])*100/single(panel["attackSpeed"]))
    shot = stationary_ranged_shot(tables,ev,a,attack_frame=first,attack_interval=interval,placement=place,enemy=enemy,
                                  facing=face,round_position=event["round_position"],mode_path="Modes/S3",max_flight_ticks=32)
    release,flight = shot["release_frame"],shot["flight"]
    state = EnemyElements.from_scene_enemy(tables,enemy)
    timer = RecoveryTimer(_raw_f32(grant["firstTriggerInterval"]))
    grants = stacks = 0
    events = [dict(kind="deployment",frame=event["frame"]),dict(kind="first_ready",frame=opening.first_ready_frame),*cast["events"]]
    for frame in range(first,flight["reach_frame"]+1):
        state.tick(frame)
        if frame==first:
            events.append(dict(kind="attack_start",frame=frame))
        if not grants and timer.advance(state.step):
            grants = 1
            stacks = 0 if enemy["palsy_immune"] else min(2,int(bb["per_active"]))
            events.append(dict(kind="self_skill_palsy_grant",frame=frame,grants_used=grants,
                               stacks_after=stacks,immune=enemy["palsy_immune"]))
        if frame==release:
            events.append(dict(kind="projectile_release",frame=frame,projectile=projectile))
    effective = mul(_raw_f32(panel["atk"]),ONE+_raw_f32(bb["atk"]))
    hit = ordinary_arts_hit(attack_raw=effective,hp_raw=state.hp,resistance_raw=enemy["arts_resistance_q32"])
    state.hp = hit.hp_after_raw
    events.append(dict(kind="primary",frame=frame,damage_q32=hit.damage_raw,hp_after_q32=state.hp))
    return dict(status="mantra_s3_first_skill_attack",through_frame=frame,events=events,receiver=state.snapshot(),
                grants_used=grants,grant_limit=int(bb["max_trigger_cnt"]),palsy_stacks=stacks,flight=flight,
                conditions=["one_stationary_visible_enemy_with_no_attack_or_palsy_trigger","no_other_ally_skill_starts",
                            "no_prior_palsy_stacks_or_elemental_break","through_first_attack"])
