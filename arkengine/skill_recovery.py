"""V079 natural skill recovery and suspendable-skill timing."""
from dataclasses import dataclass, field
from fractions import Fraction

from .fixed import ONE
from .mystic_timing import fixed_wait_frames
from .skill_assets import lin_attack_modes, lin_begin, lin_hit_talent, suspendable_buff


LIN_DRAW_OPTIONS=("lin_hit_talent_success","lin_hit_talent_seed","lin_hit_native_seed")


def lin_native_stream(profile):
    from .battle_random import BattleRandom
    for key in ("lin_hit_native_seed","lin_hit_talent_seed"):
        if key in profile:
            return BattleRandom(profile[key])
    return None


def lin_hit_draw(profile, frame, phase, index, source, stream=None):
    """Consume one native Dice draw per eligible hit, including during SP lockout."""
    if stream is not None:
        roll,=profile["hit_recovery"]["rolls"]
        return stream.dice(roll["probability"])
    if "lin_hit_talent_success" in profile:
        return profile["lin_hit_talent_success"]
    raise ValueError("受击天赋连续抽取需要场景的战斗随机流")


def _divide(numerator, denominator):
    """FP division for the finite, positive-denominator timer and gauge paths."""
    raw = ((abs(numerator) << 32) + denominator // 2) // denominator
    return -raw if numerator < 0 else raw


# MathUtil.LE(a, b) compares a < b + FP.EN5 in this client.
READY_EPSILON = _divide(ONE, 100_000 * ONE)


@dataclass
class RecoveryTimer:
    period: int
    remaining: int = field(init=False)

    def __post_init__(self):
        if self.period <= 0:
            raise ValueError("技力回复周期必须为正数")
        self.remaining = self.period

    def advance(self, delta):
        if delta < 0:
            raise ValueError("技力计时步长不能为负数")
        self.remaining -= delta
        if self.remaining >= READY_EPSILON:
            return 0
        count = 1 + max(0, _divide(-self.remaining, self.period) // ONE)
        self.remaining += count * self.period
        return count

    def change_period(self, new_period):
        if new_period <= 0:
            raise ValueError("技力回复周期必须为正数")
        elapsed = self.period - self.remaining
        self.period = new_period
        self.remaining = max(0, new_period - elapsed)

    @property
    def progress(self):
        return min(ONE, max(0, ONE - _divide(self.remaining, self.period)))


@dataclass
class NaturalSkillPoints:
    points: int
    cost: int
    maximum: int
    timer: RecoveryTimer | None

    def advance(self, delta, *, stopped=False):
        if stopped or self.timer is None or self.points >= self.maximum:
            return 0
        before = self.points
        self.points = min(self.maximum, before + self.timer.advance(delta))
        return self.points - before

    def snapshot(self):
        progress = self.timer.progress if self.timer is not None else 0
        ready_fraction = min(ONE, _divide(self.points * ONE + progress, self.cost * ONE))
        return dict(sp=self.points, ready=self.points >= self.cost,
                    timer_remaining_q32=self.timer.remaining if self.timer is not None else None, ready_gauge_q32=ready_fraction)


def deploy_skill(tables, card, frame, *, direction="RIGHT"):
    skill_id = card["skill_id"]
    if not skill_id:
        return None, dict(status="unresolved", reason="no_selected_skill")
    if card.get("skill_level") is None:
        return None, dict(status="unresolved", reason="skill_level_unknown", skill_id=skill_id)
    level = card["skill_level"]
    skill = tables.read("excel/skill_table.json")[skill_id]["levels"][level - 1]
    data = skill["spData"]
    marcil = card["operator_id"]=="char_4141_marcil" and data["increment"]==0
    if data["spType"] != "INCREASE_WITH_TIME" or (data["increment"] != 1 and not marcil):
        return None, dict(status="unresolved", reason="outside_unit_natural_recovery", sp_type=data["spType"])
    unit = tables.read("excel/character_table.json")[card["operator_id"]]
    rates = {k["data"]["spRecoveryPerSec"] for k in unit["phases"][card["elite"]]["attributesKeyFrames"]}
    charges = data["maxChargeTime"]
    if rates != {1} or data["spCost"] <= 0 or type(charges) is not int or charges < 1:
        return None, dict(status="unresolved", reason="outside_positive_charge_base_rate_one")
    step = int(tables.fixed_timestep() * ONE)
    cost = data["spCost"]
    maximum = cost * charges
    if marcil:
        if card.get("level") is None or card.get("potential") is None or card.get("module")=="unknown":
            return None, dict(status="unresolved",reason="marcil_card_initial_mana_unknown")
        from .marcil_mana import first_deployment_mana
        bb={r["key"]:r["value"] for r in skill["blackboard"]}
        initial = first_deployment_mana(tables,card,skill,frame)
        maximum = int(bb["mana_max"])
        cost = int(bb["skill_cost_min_sp"])
    else:
        initial = min(data["initSp"],maximum)
    state = NaturalSkillPoints(initial,cost,maximum,None if marcil else RecoveryTimer(ONE))
    ready_frames = [frame] if state.points >= cost else []
    report = dict(status="conditional_natural_recovery", skill_id=skill_id, skill_level=level,
                operator_id=card["operator_id"],
                skill_type=skill["skillType"], sp_type=data["spType"], initial_sp=initial,
                cost=cost, maximum_sp=maximum, max_charges=charges, step_q32=step, period_q32=None if marcil else ONE,
                base_sp_recovery_per_sec=data["increment"], ready_epsilon_q32=READY_EPSILON,
                deployment_frame=frame, first_recovery_tick=frame + 1,
                first_ready_frame=ready_frames[0] if ready_frames else None, ready_frames=ready_frames,
                credits=[], samples=[], buttons=[], modifier_changes=[], conditions=[
                    "ordinary_living_deployed_operator", "base_rate_without_modifiers_or_external_sp",
                    "no_unlisted_skill_use_or_external_recovery_stop"])
    if skill_id == "skchr_lin_3":
        report["hit_recovery"] = lin_hit_talent(tables, card)
        report["attack_modes"] = lin_attack_modes(tables)
        report["conditions"].append("only_explicit_received_damage_events_and_supplied_talent_dice")
    if skill_id=="skchr_marcil_2":
        if card.get("battle_skin","defaultskin")!="defaultskin":
            return None, dict(status="unresolved",reason="marcil_s2_birth_and_chant_skin_unreviewed")
        from .caster_prefabs import caster_prefab_evidence
        from .marcil_deploy import deployment_skill_gate
        facing="Back" if direction=="UP" else "Front"
        evidence=caster_prefab_evidence(tables,card["operator_id"],2,skill_level=level,
                    module_id=next(iter(card["module_ids"]),None),module_level=card["module_level"])
        controller,=(part["fields"] for part in evidence["skill"]["components"]
                     if not part["path"] and part["active"] and part["enabled"] and "_allowSpRecoveryWhenAffecting" in part["fields"])
        if controller["_allowSpRecoveryWhenAffecting"]:
            raise ValueError("玛露西尔二技能的技能生效后阻回条件已变化")
        gate=deployment_skill_gate(tables,card,deploy_frame=frame,frame=frame,facing=facing,evidence=evidence)
        report["auto_chant"]=dict(born_exit_frame=gate["born_exit_frame"])
        return MarcilAutoReplay(state,skill,step,report,card=card,tables=tables,evidence=evidence,facing=facing), report
    return SkillReplay(state, skill, step, report, card=card), report


@dataclass
class SkillReplay:
    stop_at_auto_ready = True
    state: NaturalSkillPoints
    skill: dict
    step: int
    report: dict
    affecting_since: int | None = None
    expires_at: int | None = None
    recovery_stopped: bool = False
    finished: bool = False
    card: dict = field(default_factory=dict)
    cast_at: int | None = None
    spell_at: int | None = None
    cooldown_end: int | None = None
    frozen_mode_at: int | None = None
    unity_expiry: bool = False
    unity_begin: bool = False

    def tick(self, loop):
        if self.finished:
            return
        frame = loop.frame
        if self.affecting_since is not None and self.expires_at is None and self.cooldown_end is None and (frame - self.affecting_since) * self.step >= (
                int(self.skill["duration"] * ONE) - self.step):
            self.report.update(status="unresolved", reason="automatic_skill_expiry_order", stop_frame=frame)
            self.finished = True
            return
        self._advance_natural_recovery(loop)
        if self.cooldown_end == frame:
            self.affecting_since = self.cooldown_end = None
            self.report.setdefault("natural_ends", []).append(frame)
            loop.emit("skill_natural_end", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"])
        if self.recovery_stopped and self.affecting_since is None:
            # Character.OnTick runs Unit/Entity recovery, then BasicSkill.Tick
            # synchronizes the skill's abnormal-flag modifier with isAffecting.
            self.recovery_stopped = False
            self.report["modifier_changes"].append(dict(frame=frame, stopped=False))

    def _advance_natural_recovery(self, loop):
        frame=loop.frame
        if self.state.advance(self.step, stopped=self.recovery_stopped):
            self.report["credits"].append(dict(frame=frame, sp=self.state.points))
            loop.emit("natural_sp", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"], sp=self.state.points)
            if self.state.points == self.state.cost:
                self.report["ready_frames"].append(frame)
                if self.report["first_ready_frame"] is None:
                    self.report["first_ready_frame"] = frame
                loop.emit("skill_sp_ready", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"])

    def after_input(self, loop):
        if self.finished:
            return
        if not self.unity_begin:
            self._advance_begin(loop)
        if not self.unity_expiry and self.expires_at == loop.frame:
            self._expire(loop)

    def _advance_begin(self, loop):
        if self.cast_at == loop.frame:
            self.affecting_since, self.cast_at = loop.frame, None
            self.recovery_stopped = True
            self.state.points -= self.state.cost
            self.report["modifier_changes"].append(dict(frame=loop.frame, stopped=True))
            loop.emit("skill_cast", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"],
                      sp=self.state.points)
        if self.spell_at == loop.frame:
            self.spell_at = None
            self.unity_begin = False
            loop.emit("skill_mode_buff", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"],
                      mode=3)
    def after_fixed(self, loop):
        if self.finished:
            return
        if self.unity_begin:
            self._advance_begin(loop)
        if self.frozen_mode_at == loop.frame:
            self.frozen_mode_at = None
            loop.emit("skill_mode_buff", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"],
                      mode="S3")
        if self.unity_expiry and self.expires_at == loop.frame:
            self.report.setdefault("natural_after_fixed", []).append(loop.frame)
            self._expire(loop)

    def _expire(self, loop):
        self.unity_expiry = False
        self.affecting_since = self.expires_at = None
        self.report.setdefault("natural_ends", []).append(loop.frame)
        loop.emit("skill_natural_end", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"])

    def press(self, tables, loop, *, phase="input", observed_outcome=None, cost_bar=None):
        frame = loop.frame
        row = dict(frame=frame, before=self.state.points)
        if observed_outcome is not None:
            row["observed_outcome"] = observed_outcome
        if phase == "after_tick":
            row["input_phase"] = phase
        if self.finished:
            row.update(outcome="unresolved", reason=self.report.get("reason", "automatic_skill_use"))
        elif self.skill["skillType"] != "MANUAL":
            row.update(outcome="rejected", reason="not_manual")
        elif self.cast_at is not None:
            row.update(outcome="no_change", reason="already_in_skill_begin_state")
        elif self.affecting_since is None and self.state.points < self.state.cost and not observed_outcome:
            row.update(outcome="rejected", reason="insufficient_sp")
        else:
            profile = suspendable_buff(tables, self.skill["prefabId"])
            self.report["button_profile"] = profile
            if not profile["supported"]:
                reason = profile["reason"]
                row.update(outcome="unresolved", reason=reason)
                self.report.update(status="unresolved", reason=reason, stop_frame=frame)
                self.finished = True
            elif self.affecting_since is not None:
                if self.frozen_mode_at is not None:
                    self.frozen_mode_at = None
                    previous = next(b for b in reversed(self.report["buttons"]) if b["outcome"] == "activated")
                    previous["mode_canceled_before_effect"] = row["no_mode_effect"] = True
                self.affecting_since = self.expires_at = None
                self.cooldown_end = self.spell_at = None
                self.unity_expiry = self.unity_begin = False
                row.update(outcome="stopped")
            elif profile.get("timing") == "skill_begin_then_ability_cooldown":
                if self.state.points < self.state.cost:
                    # This is a candidate timing track. Only lin_joint_sp can admit
                    # it to combat after intersecting the reachable SP states with the observation.
                    row.update(before_without_hit_talent=self.state.points,
                               before=self.state.cost,
                               skill_timing_condition="observed_begin_requires_reachable_full_sp")
                    self.state.points = self.state.cost
                    self.report.setdefault("sp_conditioned_buttons", []).append(frame)
                animation = lin_begin(tables, self.card)
                self.report["animation"] = animation
                begin = animation["actions"][profile["begin_action"]]["duration"]
                budget = fixed_wait_frames(Fraction(begin) * ONE / self.step)
                # In the deterministic INPUT path this tick's COROUTINES is the
                # first resume. Unity has already finished the frozen tick.
                self.unity_begin = phase == "after_tick"
                self.cast_at = frame + budget - int(not self.unity_begin)
                # Lin's non-casting ActiveBuffAbility yields a fresh WaitForFixedUpdate.
                self.spell_at = self.cast_at + 1
                duration = Fraction(self.skill["duration"]) * ONE
                self.cooldown_end = self.spell_at + (int(duration) - READY_EPSILON) // self.step + 1
                row.update(outcome="begin_animation", cast_frame=self.cast_at, mode_buff_frame=self.spell_at,
                           scheduled_end=self.cooldown_end)
            else:
                self.affecting_since = frame
                if phase == "after_tick":
                    # OpTrigSkill starts the cast immediately. This ability waits one
                    # Unity fixed update before OnSpellStart attaches its mode/ATK buffs.
                    self.frozen_mode_at = frame + 1
                    row["mode_effect_frame"] = self.frozen_mode_at
                if profile["timed_cast"] and self.skill["duration"] > 0:
                    duration = Fraction(self.skill["duration"]) * ONE
                    self.expires_at = frame + fixed_wait_frames(duration / self.step)
                    row["scheduled_end"] = self.expires_at
                    self.unity_expiry = phase == "after_tick"
                    condition = ("one_unity_fixed_update_per_battle_tick_and_base_duration" if self.unity_expiry
                                 else "uninterrupted_deterministic_coroutine_from_button_frame_with_base_duration")
                    if condition not in self.report["conditions"]:
                        self.report["conditions"].append(condition)
                self.recovery_stopped = True
                self.state.points -= self.state.cost
                self.report["modifier_changes"].append(dict(frame=frame, stopped=True))
                row.update(outcome="activated")
        row["after"] = self.state.points
        if cost_bar is not None:
            row["cost_bar"] = cost_bar
        self.report["buttons"].append(row)
        loop.emit("skill_button", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"],
                  **{k: v for k, v in row.items() if k != "frame"})

    def receive_hit(self, loop, *, ignore_for_sp, dice_succeeded, source=None):
        """Resolve an observed ON_TAKE_DAMAGE callback using the supplied battle die."""
        if type(ignore_for_sp) is not bool or (not ignore_for_sp and type(dice_succeeded) is not bool):
            raise ValueError("受击事件需要明确的忽略标志与本次战斗抽取结果")
        profile = self.report.get("hit_recovery", {})
        if self.finished or profile.get("status") != "conditional_hit_recovery":
            raise ValueError("当前技力轨没有可求解的受击天赋")
        amounts = {x["sp"] for x in profile["rolls"]}
        if len(amounts) != 1:
            raise ValueError("所选配置不能确定受击回技数值")
        amount, = amounts
        before = self.state.points
        if not ignore_for_sp and dice_succeeded and not self.recovery_stopped:
            self.state.points = min(self.state.maximum, before + amount)
        row = dict(frame=loop.frame, ignore_for_sp=ignore_for_sp, rolled=not ignore_for_sp,
                   dice_succeeded=None if ignore_for_sp else dice_succeeded, before=before, after=self.state.points)
        if source is not None:
            row["source"] = source
        self.report.setdefault("hit_credits", []).append(row)
        loop.emit("hit_talent_sp", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"],
                  **{k: v for k, v in row.items() if k != "frame"})
        if before < self.state.cost <= self.state.points:
            self.report["ready_frames"].append(loop.frame)
            if self.report["first_ready_frame"] is None:
                self.report["first_ready_frame"] = loop.frame
            loop.emit("skill_sp_ready", operator_id=self.report["operator_id"], skill_id=self.report["skill_id"])
        return row

    def sample(self, frame, requested):
        if self.finished:
            return
        value = dict(frame=frame, **self.state.snapshot(), affecting=self.affecting_since is not None,
                     recovery_stopped=self.recovery_stopped)
        if self.report["skill_id"] == "skchr_lin_3":
            value["begin_animation"] = self.cast_at is not None
        self.report["final"] = value
        if frame in requested:
            self.report["samples"].append(value)
        if self.stop_at_auto_ready and self.skill["skillType"] != "MANUAL" and value["ready"]:
            self.finished = True


@dataclass
class MarcilAutoReplay(SkillReplay):
    """First S2 auto chant, with the same live resource as deployment and the allied aura."""
    stop_at_auto_ready = False
    tables: object = None
    evidence: dict | None = None
    facing: str = "Front"
    activation: dict | None = None

    def tick(self, loop):
        if self.finished:
            return
        frame=loop.frame
        chant=self.report["auto_chant"]
        payment=self.activation.get("mana_payment") if self.activation else None
        pay_now=payment is not None and frame==payment["frame"]
        if pay_now:
            # Entity.OnTick advances BuffContainer (late-enable/payment) before SpController.
            before=self.state.points
            self.state.points=max(0,before-payment["cost"])
            chant["mana_payment"]=dict(frame=frame,phase=payment["phase"],cost=payment["cost"],
                                       before=before,remaining=self.state.points)
            loop.emit("marcil_mana_payment",operator_id=self.card["operator_id"],callback_phase=payment["phase"],
                      cost=payment["cost"],before=before,remaining=self.state.points)
            self._advance_natural_recovery(loop)
            # Character calls BasicSkill.Tick after Entity. This frame's due credit can still land.
            self.recovery_stopped=True
            self.report["modifier_changes"].append(dict(frame=frame,stopped=True))
            self.report.update(status="through_first_auto_payment",through_frame=frame,
                               next_unresolved="marcil_s2_suspension_recast_and_familiar_actions")
            return
        super().tick(loop)
        if self.activation is None and frame>=chant["born_exit_frame"] and self.state.points>=self.state.cost:
            from .caster_start import resolve_cast_start
            self.activation=resolve_cast_start(self.tables,self.evidence,frame=frame,sp=self.state.points,
                                                phase="before_coroutines",facing=self.facing,owner_state="idle")
            if self.activation["cast_frame"] is None:
                raise ValueError("玛露西尔二技能在已就绪的正常对象更新中未成功启动吟唱")
            chant.update(trigger_frame=frame,mana_at_trigger=self.state.points)
            loop.emit("marcil_auto_chant",operator_id=self.card["operator_id"],sp=self.state.points)

    def after_input(self, loop):
        if self.activation and self.activation["cast_frame"]==loop.frame:
            self.affecting_since=loop.frame
            self.report["auto_chant"].update(cast_frame=loop.frame,mana_at_commit=self.state.points)
            loop.emit("marcil_chant_commit",operator_id=self.card["operator_id"],sp=self.state.points)

    def sample(self, frame, requested):
        super().sample(frame,requested)
        if self.report.get("through_frame")==frame:
            self.finished=True
