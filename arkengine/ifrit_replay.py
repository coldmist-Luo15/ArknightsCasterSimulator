"""Shared support, deployment checks and ordinary attack timing for Ifrit stages."""

from .animation_assets import read_skin_actions
from .area_caster_skills import IFRIT
from .baked_mount import single
from .caster_catalog import resolve_caster_loadout
from .caster_prefabs import caster_prefab_evidence, sample_clip_events
from .eyja_aura import EYJA,FlameBreath
from .marcil_deploy import _birth
from .module_effects import active_board,talent_rows
from .stage_channel import UnsupportedChannelScene


def ifrit_flame_support(tables,request,owner_frame):
    pick=next((row for row in request["squad"] if row["operator_id"]==EYJA),None)
    if pick is None:
        return None,[],None
    card=resolve_caster_loadout(tables,EYJA,pick["skill"],pick["module"],skill_level=pick["skill_level"],
                               module_level=pick["module_level"])
    placements=[]
    withdrawal=None
    for action in request["actions"]:
        if action["operator_id"]!=EYJA:
            continue
        frame=action["frame"]
        if action.get("kind","deploy")=="deploy" and not placements and withdrawal is None:
            placements.append(action)
        elif (action.get("kind")=="withdraw" and placements and withdrawal is None and
                max(owner_frame,placements[0]["frame"])<frame<=request["end_frame"] and
                action.get("phase") in ("before_coroutines","after_tick")):
            withdrawal=action
        else:
            raise UnsupportedChannelScene("此关卡组合需要艾雅法拉的一次部署与随后撤退")
    return (card,FlameBreath.from_card(tables,card)),placements,withdrawal


def validate_ifrit_flame_support(tables,support,deploys,withdrawal,opening,end):
    if not deploys:
        return
    card,_=support
    deploy,=deploys
    start=deploy["frame"]
    if not any(e["kind"]=="deployed" and e["operator_id"]==EYJA and e["frame"]==start for e in opening["events"]):
        raise UnsupportedChannelScene("艾雅法拉的炎息来源未实际部署")
    evidence=caster_prefab_evidence(tables,EYJA,card["skill"],skill_level=card["skill_level"],
                                   module_id=card["module"],module_level=card["module_level"])
    face="Back" if deploy["direction"]=="UP" else "Front"
    first=start+_birth(tables,card,evidence,face)["born_exit_update"]+1
    last=withdrawal["frame"] if withdrawal else end
    if last>=first and any(e["kind"]=="attack_range_enter" and e["operator_id"]==EYJA and e["frame"]<=last
                           for e in opening["events"]):
        raise UnsupportedChannelScene("艾雅法拉也接敌时需要在同一时钟中执行她的攻击")


def ifrit_flame_active(frame,deploys,withdrawal,*,objects=False):
    return bool(deploys and frame>=deploys[0]["frame"]+int(objects) and (
        withdrawal is None or frame<withdrawal["frame"] or frame==withdrawal["frame"] and (
            objects or withdrawal["phase"]=="after_tick")))


def rhine_circuit_definition(card,evidence):
    parts=evidence["character"]["components"]
    talent,=(c["fields"] for c in parts if c["script"]=="Talent" and c["path"].endswith("/Talents/2"))
    passive,=(c["fields"] for c in parts if c["script"]=="PassiveBuffAbility" and c["path"].endswith("/Talents/2"))
    buff,=passive["_buffs"]
    effect,=evidence["templates"][buff["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    if (not talent["_attachInDummy"] or buff["templateKey"]!="periodic_sp" or not buff["waitFirstTriggerInterval"]
            or buff["firstTriggerInterval"]!=-1 or effect["_spString"]!="sp" or effect["_forceFlag"]):
        raise ValueError("莱茵回路的初始挂载、定时或技力修改方式发生变化")
    candidates=[row for row in card["base_talents"] if row["prefabKey"]=="2"]
    rows=talent_rows(card["module_phase"],1,"2",candidates)
    return active_board(rows,card["elite"],card["level"],card["potential"])


def ifrit_line_attack_clock(tables,spec,seconds):
    step=tables.fixed_timestep()
    raw_ability, = [c["fields"] for c in spec["character"]["components"] if
                    c["path"]=="Modes/Default/Body/Attack" and c["script"]=="MeleeAttack"]
    if (raw_ability["_timeMode"], raw_ability["_resetCdStrategy"], raw_ability["_waitForAttackEvent"],
            raw_ability["_selectTargetTiming"], raw_ability["_preDelay"]) != (0,1,1,1,0):
        raise ValueError("普通直线攻击的起手、选取或重置 CD 配置不符")
    anim = read_skin_actions(tables,"chararts/"+IFRIT+".ab","defaultskin",("Attack","Attack_Down"),front_only=["Attack_Down"])
    keys = set()
    for view,data in anim["views"].items():
        for action in data["actions"].values():
            scale = min(raw_ability["_maxAnimScale"],max(single(.1),single(seconds/action["duration"])))
            if (action["loop"] or action["speed"]!=1 or scale*action["duration"]>seconds+1e-5):
                raise ValueError("直线攻击动画延长或速率改变")
            keys.update(r["clip_tick"] for r in sample_clip_events(spec["animations"],view.capitalize(),action["clip"],
                        playback_speed=single(1/scale),timestep=step) if r["event"]=="OnAttack")
    delay, = keys
    ratio = seconds/step
    interval = round(ratio)
    if abs(abs(ratio-interval)-.5)<1e-3 or interval<=delay:
        raise ValueError("攻击 CD 在半帧边界或上次释放之前")
    return delay,interval,anim["source"]
