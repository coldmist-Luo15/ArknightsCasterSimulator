"""First callback of a cast selected by its bound ability and timing fields."""
from fractions import Fraction
from math import isfinite

from .baked_mount import single
from .caster_catalog import resolve_caster_loadout
from .caster_prefabs import sample_clip_events
from .caster_start import _begin_clip
from .fixed import ONE
from .mystic_cycle import baked_key_sample
from .mystic_timing import fixed_wait_frames


def _selected_clip(tables, evidence, key, facing):
    clip, source = _begin_clip(tables,evidence,key,facing)
    if not clip or clip.get("status") or clip["duration"]<=0 or clip["speed"]!=1:
        raise ValueError(f"技能动作 {key} 的绑定或播放速度需要核对")
    return clip,source


def attack_key_clock(tables,evidence,facing,action,interval):
    """One OnAttack track, timeMode=BY_PERIOD and maximum animation scale 1."""
    clip,_=_selected_clip(tables,evidence,action,facing)
    key,=(q["seconds"] for q in clip["events"] if q["name"]=="OnAttack")
    speed=single(1/min(1,max(single(.1),single(interval/single(clip["duration"])))))
    ticks,_=baked_key_sample(key,speed,tables.fixed_timestep())
    return ticks,dict(action=action,first_key_seconds=key,playback_speed=speed)


def _directional(fields, direction, name):
    if direction=="DOWN" and fields.get("_overrideDownAnimation"):
        return fields["_down"][name]
    if direction=="UP" and fields.get("_overrideUpAnimation"):
        return fields["_up"][name]
    return fields["_default"][name]


def _ordinary_interval(tables, evidence, supplied):
    if supplied is not None:
        if type(supplied) not in (float,int) or not isfinite(supplied) or supplied<=0:
            raise ValueError("实时攻击间隔必须为正数")
        return single(supplied),"supplied_effective_interval"
    card = resolve_caster_loadout(tables,evidence["operator_id"],evidence["slot"],evidence["module_id"],
                                  skill_level=evidence["rank"],module_level=evidence["module_level"])
    stats = card["panel_attributes"]
    return single(single(stats["baseAttackTime"])*100/single(stats["attackSpeed"])),"max_progression_equipped_panel"


def first_ability_spell(tables,evidence,roots,*,cast,phase,facing,direction,effective_attack_interval):
    """One uninterrupted cast in which the target and animation read remain valid."""
    sid = evidence["skill_id"]
    branches = {"RangedAttack":"projectile_release","ActiveAbilityWrapper":"attach_wrapped_abilities",
                "ChannelingMeleeAttack":"channel_hit","MeleeAttack":"melee_spell",
                "AnimatedActionToTargetAbility":"target_action"}
    dt = tables.fixed_timestep()
    values = {b["key"]:b["value"] for b in evidence["skill_blackboard"]}
    row = tables.read("excel/skill_table.json")[sid]["levels"][evidence["rank"]-1]
    all_components = {c["id"]:c for c in evidence["skill"]["components"]}
    abilities = [c for c in roots if "_resetCdStrategy" in c["fields"] or c["script"]=="AbilitySequenceGroup"]
    if len(abilities)!=1:
        return None
    ability, = abilities
    events = []
    if ability["script"]=="AbilitySequenceGroup":
        seq = ability["fields"]
        if seq["_enableLoop"] or not seq["_alwaysNext"] or seq["_checkCanUseSubAbility"]:
            raise ValueError("多段技能的起始选择条件变化")
        ability = all_components[seq["_abilities"][0]["m_PathID"]]
        events.append(dict(kind="sequence_child_start",frame=cast,path=ability["path"],script=ability["script"]))
    f = ability["fields"]
    kind = branches.get(ability["script"])
    if kind is None or f["_interuptIfTargetDead"]:
        return None
    if not f["_waitForAttackEvent"] and f["_preDelay"] and kind!="channel_hit":
        return None
    info = dict(component=ability["id"],script=ability["script"],phase=phase)
    params = {}
    if kind=="attach_wrapped_abilities":
        params["abilities"] = [dict(path=all_components[r["m_PathID"]]["path"],script=all_components[r["m_PathID"]]["script"])
                               for r in f["_wrappedAbilities"]]
    elif kind=="projectile_release":
        params["projectile"] = f["_projectileKey"]
    elif kind=="channel_hit":
        params["repeat_interval_seconds"] = f["_triggerDelta"]
    if f["_waitForAttackEvent"]:
        channel = next((c["fields"] for c in roots if c["script"]=="ThreePartChannelingAnimation"),None)
        if channel:
            action = _directional(channel,direction,"beginAnim")
        else:
            action = f["_animKey"]
        clip, src = _selected_clip(tables,evidence,action,facing)
        speed = 1.0
        scale = 1.0
        if channel:
            end,_ = _selected_clip(tables,evidence,_directional(channel,direction,"endAnim"),facing)
            duration = row["duration"]
            sides = single(single(clip["duration"])+single(end["duration"]))
            if duration<=0 or channel["_onlyPlayBeginAnimWhenFirstAttack"] or channel["_dontPlayBeginAnimFromInterrupted"]:
                raise ValueError("当前三段动画的首段需要独立状态")
            speed = single(1/min(1,single(duration/sides)))
        elif f["_timeMode"]==0:
            interval,origin = _ordinary_interval(tables,evidence,effective_attack_interval)
            scale = max(single(.1),single(interval/single(clip["duration"])))
            if f["_maxAnimScale"]>=0:
                scale=min(scale,f["_maxAnimScale"])
            speed=single(1/scale)
            info.update(attack_interval=interval,interval_origin=origin)
        elif f["_timeMode"]!=1:
            raise ValueError("该攻击动画时间模式需要独立解析")
        if not channel:
            info["cached_duration_seconds"] = single(single(clip["duration"])*scale)
        sampled = sample_clip_events(evidence["animations"],facing,clip["clip"],playback_speed=speed,timestep=dt)
        first = next(k for k in sampled if k["event"]=="OnAttack")
        # The INPUT frame still has LATE_OBJECTS. A frozen input first samples at f+1,
        # but its Unity fixed waiter resumes after that sample instead of before LATE_OBJECTS.
        sample = cast + first["clip_tick"] - int(phase!="after_tick")
        spell = sample + int(phase!="after_tick")
        events.append(dict(kind="baked_OnAttack",frame=sample,key=action,clip=clip["clip"],seconds=first["seconds"]))
        info.update(source=src,action=action,baked_sha256=evidence["animations"]["sha256"],playback_speed=speed,
                    first_key_seconds=first["seconds"],key_sample=first["clip_tick"],on_attack_keys=len([k for k in sampled if k["event"]=="OnAttack"]))
    else:
        delay = single(f["_preDelay"])
        if delay:
            if (f["_timeMode"]!=3 or f["_specifiedPredelay"] or f["_maxAnimScale"]!=1 or
                    f["_cooldownKey"]!="duration" or f["_cooldown"]):
                raise ValueError("所选计时前摇需要额外的攻速与黑板参数")
            key = (f["_downAnimKey"] if direction=="DOWN" and f["_downAnimKey"] else f["_animKey"])
            clip,_ = _selected_clip(tables,evidence,key,facing)
            duration = values.get("duration",row["duration"])
            if duration < clip["duration"]:
                raise ValueError("谬因波束的技能时长不能覆盖原始动作")
        ticks = fixed_wait_frames(Fraction(int(delay*ONE),int(dt*ONE)))
        spell = cast+ticks-int(phase!="after_tick")
        info.update(pre_delay_seconds=delay,wait_resumes=ticks)
    events.append(dict(kind=kind,frame=spell,**params))
    return dict(spell_frame=spell,events=events,clock=info)
