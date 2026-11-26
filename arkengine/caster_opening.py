"""First cast by an otherwise idle caster with unmodified natural skill recovery."""
from dataclasses import dataclass

from .caster_start import CAST_CONTROLLERS, resolve_cast_start
from .marcil_deploy import deployment_skill_gate
from .skill_recovery import deploy_skill


@dataclass
class CasterOpening:
    initial_sp: int
    sp: int
    through_frame: int
    first_ready_frame: int | None = None
    cast: dict | None = None


def deploy_to_first_cast(tables, card, evidence, *, deploy_frame, direction, facing="Front",
                         end_frame=None, next_manual_input=None, manual_phase="input"):
    """Use the selected skill's resource type, BORN flags, trigger phase and cast controller.

    The caller admits the target and external battle conditions. An optional deterministic
    manual input policy receives the earliest eligible frame and the BORN exit frame.
    The returned cast starts within the requested window and can schedule callbacks beyond it.
    """
    if manual_phase not in ("input","after_tick") or type(deploy_frame) is not int or deploy_frame<0 or (
            end_frame is not None and (type(end_frame) is not int or end_frame<deploy_frame)):
        raise ValueError("部署和观察截止帧无效")
    if (evidence["operator_id"],evidence["skill_id"],evidence["rank"],evidence["module_id"],evidence["module_level"])!=(
            card["operator_id"],card["skill_id"],card["skill_level"],card["module"],card["module_level"]):
        raise ValueError("开技资源与选定的干员、技能或模组不一致")
    active = [c for c in evidence["skill"]["components"] if not c["path"] and c["active"] and c["enabled"]]
    if (not any(c["script"] in CAST_CONTROLLERS for c in active)
            or any(c["script"]=="ChantBeforeSkill" for c in active)):
        raise ValueError("此入口只处理以正常技力释放的 CastSkill 控制器")
    replay,report = deploy_skill(tables,card,deploy_frame)
    if replay is None:
        raise ValueError(f"该技能的自然回复时钟不可用 {report['reason']}")
    if report["skill_type"] not in ("MANUAL","AUTO") or (report["skill_type"]=="AUTO" and next_manual_input is not None):
        raise ValueError("手动提交策略只适用于手动触发技能")
    state = replay.state
    frame = deploy_frame
    opening = CasterOpening(state.points,state.points,frame)
    while state.points<state.cost and (end_frame is None or frame<end_frame):
        frame += 1
        state.advance(replay.step)
    opening.sp,opening.through_frame = state.points,frame
    if state.points<state.cost:
        return opening
    opening.first_ready_frame = frame
    auto = report["skill_type"]=="AUTO"
    gate = deployment_skill_gate(tables,card,deploy_frame=deploy_frame,frame=frame,facing=facing,
                                phase="auto_skill_tick" if auto else manual_phase,evidence=evidence)
    delivery = frame if gate["can_deliver_effective_skill"] else gate["born_exit_frame"]
    if auto:
        # The deployed character's first automatic skill check is on its following OBJECTS update.
        delivery = max(delivery,deploy_frame+1)
    elif next_manual_input is not None:
        scheduled = next_manual_input(delivery,gate["born_exit_frame"])
        if type(scheduled) is not int or scheduled<delivery:
            raise ValueError("选择的首次输入早于实际就绪帧")
        delivery = scheduled
    while frame<delivery and (end_frame is None or frame<end_frame):
        frame += 1
        state.advance(replay.step)
    opening.sp,opening.through_frame = state.points,frame
    if frame==delivery:
        opening.cast = resolve_cast_start(tables,evidence,frame=frame,direction=direction,facing=facing,
            owner_state="born" if frame<gate["born_exit_frame"] else "idle",trigger=report["skill_type"],
            phase="before_coroutines" if auto else manual_phase,sp=state.points)
    return opening
