"""Ifrit S2's first automatic charged line hit, followed by all native burn ticks."""
from collections import deque
from math import isfinite

from .attack_range import circle_in_range,range_cells
from .caster_cast import run_committed_cast
from .caster_special_start import resolve_special_start
from .enemy_elements import EnemyElements
from .fixed import ONE
from .ifrit_delta import ifrit_delta_hit,ifrit_delta_rules
from .marcil_deploy import _birth
from .skill_recovery import RecoveryTimer,deploy_skill
from .volley_damage import _raw_f32


def scorch_definition(evidence):
    f=next(c["fields"] for c in evidence["skill"]["components"] if c["script"]=="MeleeAttack")
    buff,=f["_activeBuffs"]
    stat,=buff["attributes"]["attributeModifiers"]
    if (f["_selectTargetTiming"]!=1 or f["_timeMode"]!=1 or f["_waitForAttackEvent"] or
            (stat["attributeType"],stat["formulaItem"],stat["loadFromBlackboard"])!=(2,0,1) or
            buff["templateKey"]!="burn" or (buff["lifeTimeType"],buff["triggerLifeType"])!=(1,2) or
            not buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"]!=-1 or
            (buff["buffKey"],buff["overrideKey"],buff["disableOverride"],buff["overrideType"],
             buff["independentCharacterSource"],buff["priority"],buff["priorityBBKeys"])
             !=("ifrit_s_2","",0,0,1,-1,[])):
        raise ValueError("灼烧前摇、减防或周期配置改变")
    board={r["key"]:r["value"] for r in evidence["skill_blackboard"]}
    return buff,board


def scorch_defense_rule(buff,board):
    return _raw_f32(board["def"]),_raw_f32(board[buff["durationKey"] or "duration"])


class ScorchBurnGroup:
    """One target, one Ifrit, same skill level. Equal-priority instances keep creation order."""
    def __init__(self, duration_frames, period):
        self.duration_frames = duration_frames
        self.period = period
        self.expirations = deque()
        self.timer = RecoveryTimer(period)

    def attach(self, frame):
        self.expirations.append(frame + self.duration_frames)

    def tick(self, frame, step):
        # The recipient visits older buffs first. Finish immediately enables the next
        # unfinished instance, whose own update still runs on this recipient frame.
        triggers = 0
        while self.expirations:
            triggers += self.timer.advance(step)
            if frame < self.expirations[0]:
                break
            self.expirations.popleft()
            self.timer = RecoveryTimer(self.period)
        return triggers


def run_ifrit_scorch(tables,card,evidence,event):
    if card["skill"]!=2 or card["module_info"]["typeIcon"]!="bla-d":
        raise ValueError("该灼伤回放需要伊芙利特二技能和 BLA-Δ")
    start,end=event["frame"],event["end_frame"]
    target,place=event["target"],event["placement"]
    enter,leave=target["enter_frame"],target["leave_frame"]
    rows,cols=event["rows"],event["cols"]
    if (any(type(f) is not int for f in (start,end,enter,leave,rows,cols,place["row"],place["col"])) or
            not 0<=start<=enter<leave<=end or end-start>18000 or not 0<=place["row"]<rows or not 0<=place["col"]<cols or
            any(type(target[k]) not in (int,float) or not isfinite(target[k]) for k in ("row","col","radius","hp","defense","resistance")) or
            not 0<=target["row"]<rows or not 0<=target["col"]<cols or target["radius"]<0 or
            target["resistance"]!=0 or target["hp"]<=0 or target["defense"]<0):
        raise ValueError("靶场需要有效的部署与停留时窗，以及正生命、零法抗的静止受击者")
    cells=range_cells(card["base_range"],place,rows,cols)
    if not circle_in_range(target["row"],target["col"],target["radius"],cells,box_query=True):
        raise ValueError("靶标在停留时窗内须与火线相交")
    buff,board=scorch_definition(evidence)
    panel=_raw_f32(card["panel_attributes"]["atk"])
    rules=ifrit_delta_rules(card,evidence)
    receiver=EnemyElements(tables,enemy_level=target["enemy_level"],hp_q32=_raw_f32(target["hp"]))
    sp,_=deploy_skill(tables,card,start)
    gauge=sp.state
    facing=event.get("facing","Front")
    first_idle=start+_birth(tables,card,evidence,facing)["born_exit_update"]+1
    # The request supplies range occupancy sampled before the caster's idle search.
    first_search=first_idle+3*max(0,(enter-first_idle+2)//3)
    relative=run_committed_cast(tables,evidence,horizon=200,facing=facing,direction=place["direction"],commit_phase="before_coroutines")
    hit_offset,=(e["frame"] for e in relative["events"] if e["kind"]=="melee_spell")
    impact=first_search+hit_offset
    cast_end=first_search+relative["end_frame"]
    recovery=first_search+relative["first_unblocked_recovery_tick"]
    if not impact<leave<=cast_end+1 or end<impact:
        raise ValueError("靶标须维持到本次命中，且在技能结束后首次重新索敌前离开火线")
    talent=next(r for r in card["base_talents"] if r["prefabKey"]=="2")
    gain={r["key"]:r["value"] for r in talent["blackboard"]}
    pulse=RecoveryTimer(_raw_f32(gain["interval"]))
    burn=RecoveryTimer(_raw_f32(buff["triggerInterval"]))
    life=_raw_f32(board[buff["durationKey"] or "duration"])
    expires=impact+(life+sp.step-1)//sp.step
    log=[dict(kind="deploy",frame=start,sp=gauge.points)]
    debuffed=False
    for now in range(start+1,end+1):
        receiver.tick(now)
        blocked=first_search<now<recovery
        credited=gauge.advance(sp.step,stopped=blocked)
        if pulse.advance(sp.step) and not blocked:
            credited+=min(gauge.maximum-gauge.points,int(gain["sp"]))
            gauge.points=min(gauge.maximum,gauge.points+int(gain["sp"]))
        if credited:
            log.append(dict(kind="sp_recovered",frame=now,sp=gauge.points))
        if now==first_search:
            delivery=resolve_special_start(tables,evidence,frame=now,phase="before_coroutines",source="before_attack",sp=gauge.points)
            if delivery["status"]!="replacement_registered":
                raise ValueError("首次有目标时技能尚不能替换普攻")
            gauge.points=delivery["sp_after"]
            log.extend(dict(e) for e in delivery["events"] if e["kind"] in ("reduce_sp","register_attack_replacement"))
        if now==recovery:
            log.append(dict(kind="first_unblocked_recovery_update",frame=now))
        if now==leave:
            log.append(dict(kind="left_attack_range",frame=now))
        source=None
        if now==impact:
            debuffed=True
            log.append(dict(kind="defense_delta",frame=now,delta=board["def"]))
            source="attack_hit"
        elif debuffed and burn.advance(sp.step):
            source="burn_tick"
        if source and receiver.hp:
            hit=ifrit_delta_hit(receiver,card,rules,now,source,panel,0)
            log.append(dict(kind=source,frame=now,hp_q32=receiver.hp,damage_q32=hit.damage_raw if hit else 0))
        if debuffed and (now==expires or not receiver.hp):
            debuffed=False
            log.append(dict(kind="debuff_finished",frame=now,reason="expired" if receiver.hp else "target_dead"))
    return dict(status="ifrit_s2_first_replacement_and_burn",events=log,element_events=receiver.events,
                hp_q32=receiver.hp,sp=gauge.points,active_defense_delta_q32=_raw_f32(board["def"]) if debuffed else 0,
                source_defense_q32=_raw_f32(target["defense"]),through_frame=end,attack_cells=cells,cast_end_frame=cast_end,
                conditions=["solo_default_skin_no_external_effects","fixed_target_range_enter_before_idle_search",
                            "owner_remains_deployed_after_hit","zero_base_resistance","first_deployment_talent_timer"])
