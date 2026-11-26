"""Nymph's source-bound receiver buffs from the selected V079 templates."""
from dataclasses import dataclass, field

from .damage import HP_EPSILON
from .caster_equipment_traits import _trait_values
from .caster_prefabs import caster_prefab_evidence
from .elemental_casters import NYMPH, _board
from .fixed import ONE, mul
from .nymph_impact import apply_s3_hit, s3_damage_scales
from .mostima_aura import set_period
from .skill_recovery import RecoveryTimer, SkillReplay, _divide, deploy_skill
from .volley_damage import _raw_f32


def _action(node):
    return node["$type"].split("+")[-1].split(",")[0]


def y_recovery_period(tables,card,evidence):
    if (card["module_info"] or {}).get("typeIcon")!="pri-y":
        return None
    aura, = [c["fields"] for part in evidence["equipment"].values() for c in part["components"]
             if c["script"]=="AuraAbility" and any(b["buffKey"]=="nymph_e_003_tr[check]" for b in c["fields"]["_buffs"])]
    check,=aura["_buffs"]
    actions=tables.read("battle/buff_template_data.json")[check["templateKey"]]["eventToActions"]
    start,=actions["ON_BUFF_START"]
    burst,=actions["ON_EP_BREAK_START"]
    finish,=actions["ON_EP_BREAK_FINISH"]
    condition=start["_conditionNode"]
    effect,=start["_succeedNodes"]
    mod,=effect["_buff"]["attributes"]["attributeModifiers"]
    if (not aura["_removeBuffWhenTargetLeave"] or not aura["_removeBuffWhenAbilityDetached"]
            or start!=burst or _action(condition)!="IsTargetInEPBreakRecovery" or condition["_checkRecoveryType"]
            or condition["_target"]!="BUFF_OWNER" or _action(effect)!="CreateBuff" or effect["_buffOwner"]!="BUFF_SOURCE"
            or not effect["_isDerivedBuff"] or not effect["_finishDerivedBuffIfParentFinish"]
            or effect["_buff"]["overrideType"]!="DEFAULT" or effect["_buff"]["disableOverride"]
            or (mod["attributeType"],mod["formulaItem"],mod["loadFromBlackboard"])!=("SP_RECOVERY_PER_SEC","ADDITION",True)
            or _action(finish)!="FinishDerivedBuff" or not finish["_updateOverrideMap"]):
        raise ValueError("妮芙 Y 的爆发检测、派生回复或清除条件变化")
    base_rate=card["skill_row"]["spData"]["increment"]
    return _divide(ONE,_raw_f32(base_rate)+_raw_f32(_trait_values(card)["sp_recovery_per_sec"]))


@dataclass
class NymphRecovery:
    gauge: SkillReplay
    faster_period: int
    attached: bool = False
    stopped: bool = False
    events: list = field(default_factory=list)
    last_tick: int | None = None

    def sync(self, frame, has_burst):
        gauge=self.gauge.state
        period=self.faster_period if has_burst else ONE
        if gauge.timer.period!=period:
            set_period(gauge,period,stopped=self.stopped)
            self.events.append(dict(kind="period_changed",frame=frame,period_q32=period,sp=gauge.points))

    def tick(self, frame):
        if self.last_tick is not None and frame!=self.last_tick+1:
            raise ValueError("妮芙技力控制器需要连续且各不重复的对象更新")
        self.last_tick=frame
        if self.gauge.state.advance(self.gauge.step,stopped=self.stopped):
            self.events.append(dict(kind="natural_sp",frame=frame,sp=self.gauge.state.points))


@dataclass(frozen=True)
class SoulEffect:
    scale: int
    interval: int
    parent_key: str


@dataclass(frozen=True)
class SoulRules:
    root: str
    delay: int
    ordinary: SoulEffect
    enhanced: SoulEffect | None

    @classmethod
    def from_card(cls, tables, card, evidence):
        templates = tables.read("battle/buff_template_data.json")
        base, = [c["fields"]["_buffs"][0] for c in evidence["character"]["components"]
                 if c["path"] == "Others/Direction/Talents/1" and c["script"] == "PassiveBuffAbility"]
        root = base["buffKey"]
        for equipment in evidence["equipment"].values():
            parts = equipment["components"]
            if any(c["script"] == "Talent" and c["fields"]["_overwriteTalentKey"] == "1_override" for c in parts):
                override, = [c["fields"]["_buffs"][0] for c in parts if c["script"] == "PassiveBuffAbility"]
                root = override["buffKey"]
        filt, present, invert, listener = templates[root]["eventToActions"]["ON_BEFORE_TARGET_APPLY_MODIFIER"]
        watch = listener["_buff"]
        branch, = templates[watch["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
        contains, = branch["_succeedNodes"]
        create, finish = contains["_failNodes"]
        if ([_action(n) for n in (filt,present,invert,listener)] != ["FilterDamageModifer","CheckContainsBuff","IfNot","CreateBuff"]
                or filt["_damageMask"] != "ANY_ATTACK" or not present["_checkBuffSource"]
                or present["_buffSourceType"] != "BUFF_OWNER" or listener["_buffOwner"] != "MODIFIER_TARGET"
                or not listener["_finishDerivedBuffIfParentFinish"] or not watch["waitFirstTriggerInterval"]
                or watch["triggerCnt"] != 1 or branch["_conditionNode"]["_recoveryType"] != "DARK"
                or not contains["_conditionNode"]["_checkBuffSource"] or _action(finish) != "FinishBuff"):
            raise ValueError("失魂的施加条件、延迟或同源过滤变化")
        boosted, = templates["nymph_s_2"]["eventToActions"]["ON_BUFF_START"]
        if boosted["_conditionNode"]["_recoveryType"] != "DARK":
            raise ValueError("妮芙二技能失魂的爆发类型变化")
        def effect(buff, board):
            actions = templates[buff["templateKey"]]["eventToActions"]
            attach, invert, finish = actions["ON_BUFF_START"]
            damage, = actions["ON_BUFF_TRIGGER"]
            clear, = actions["ON_BEFORE_EP_BREAK_FINISH"]
            key = buff["buffKey"]
            if (buff["waitFirstTriggerInterval"] or not buff["independentCharacterSource"]
                    or buff["disableOverride"] or buff["overrideType"] != "DEFAULT"
                    or buff["priorityBBKeys"] != ["element_atk_scale"] or buff["triggerLifeType"] != "INFINITY"
                    or (damage["_damageType"],damage["_attackType"]) != ("ELEMENT","BUFF")
                    or (damage["_sourceType"],damage["_targetType"]) != ("SOURCE","TARGET")
                    or damage["_baseOnHostAtk"] or damage["_noSource"]
                    or damage["_forceUseProjectileCachedAtk"] or damage["_getCachedAtkFromBlackboard"]
                    or clear["_buffKey"] != key or not clear["_checkBuffSource"]
                    or clear["_sourceType"] != "BUFF_SOURCE" or clear["_finishHostBuff"]
                    or not clear["_alsoClearNullSource"]
                    or [_action(n) for n in (attach,invert,finish)] != ["AttachAsDerivedBuffById","IfNot","FinishBuff"]
                    or not attach["_finishDerivedBuffIfParentFinish"] or attach["_finishDerivedBuffIfNoParent"]):
                raise ValueError("失魂的伤害、同源优先级或派生状态变化")
            prefix = key+"." if buff["stripBlackboardParamsWithBuffKey"] else ""
            return SoulEffect(_raw_f32(board[prefix+damage["_atkScaleVar"]]),
                              _raw_f32(board.get(prefix+"interval",buff["triggerInterval"])), attach["_buffKey"])
        skill_board = {r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
        return cls(root, _raw_f32(watch["triggerInterval"]), effect(create["_buff"],_board(card,0,"1")),
                   effect(boosted["_succeedNodes"][0]["_buff"],skill_board) if card["skill"] == 2 else None)


@dataclass
class SoulOnTarget:
    """One operator card and one receiver. Call tick during the receiver's update."""
    rules: SoulRules
    listeners: list = field(default_factory=list)
    active: SoulEffect | None = None
    timer: RecoveryTimer | None = None
    source_present: bool = True
    orphan: SoulEffect | None = None

    def _has_enabled_source(self):
        return (self.source_present and self.active is not None
                and (self.orphan is None or self.orphan.scale < self.active.scale))

    def before_attack_damage(self):
        if self.source_present and self.active is None:
            self.listeners.append(RecoveryTimer(self.rules.delay))

    def _attach(self, effect):
        # The stronger S2 instance wins; equal-priority new instances never reset the first one's timer.
        if self.active is None or self.active.scale < effect.scale:
            self.active = effect
            self.timer = RecoveryTimer(effect.interval)
            self.timer.remaining = 0

    def after_s2_buildup(self, state, *, reborn=False):
        if self.rules.enhanced and self.source_present and not reborn and state.burst == "DARK" and state.hp > HP_EPSILON:
            self._attach(self.rules.enhanced)

    def withdraw(self):
        self.source_present = False
        self.listeners.clear()
        if self.active and self.active.parent_key == self.rules.root:
            self.active = self.timer = None

    def redeploy(self):
        if self.source_present:
            raise ValueError("妮芙仍在场，不能重新部署")
        # The source UID is gone, but Reset gives the same card the same override key.
        # All orphan effects are indefinite and only the strongest can block a new buff.
        if self.active is not None and (self.orphan is None or self.orphan.scale < self.active.scale):
            self.orphan = self.active
        self.active = self.timer = None
        self.source_present = True

    def tick(self, frame, state, attack, output_scale):
        if state.hp <= HP_EPSILON:
            self.orphan = self.active = self.timer = None
            self.listeners.clear()
            return
        enabled = self._has_enabled_source()
        if enabled and state.burst != "DARK":
            self.orphan = self.active = self.timer = None
        # A disabled buff does not advance its trigger timer. An enabled orphan
        # has neither a cached ATK nor a live source for the break-finish action.
        if self.timer is not None and enabled and self.timer.advance(state.step):
            state.apply_elemental_hp(frame,mul(attack,self.active.scale),source="nymph_soul",
                                     output_scale_q32=output_scale)
        prior_count = len(self.listeners)
        self.listeners = [timer for timer in self.listeners if not timer.advance(state.step)]
        if len(self.listeners) < prior_count:
            if state.burst == "DARK" and self.active is None and state.hp > HP_EPSILON:
                # A new DOT created inside this container iteration first ticks on the following update.
                self._attach(self.rules.ordinary)


class NymphReceiverSource:
    """One deployed Nymph observing a receiver shared with the actual damage sources."""

    def __init__(self, tables, card, setup, frame):
        if card["operator_id"] != NYMPH or card["skill"] not in (2,3):
            raise ValueError("混合受击者需要已部署的妮芙二或三技能")
        self.slot = card["skill"]
        self.watching = setup["talent_attached"]
        self.s3_active = setup.get("s3_active", False)
        if type(self.watching) is not bool or type(self.s3_active) is not bool or self.s3_active and self.slot != 3:
            raise ValueError("需给出开局受击者实际持有的天赋范围标志及妮芙当前模式")
        ev = caster_prefab_evidence(tables, NYMPH, self.slot, skill_level=card["skill_level"],
                                   module_id=card["module"], module_level=card["module_level"])
        self.soul = SoulOnTarget(SoulRules.from_card(tables,card,ev))
        self.recovery = None
        if "y_recovery" in setup:
            config=setup["y_recovery"]
            period=y_recovery_period(tables,card,ev)
            if period is None or type(config["target_attached"]) is not bool or type(config.get("stopped",False)) is not bool:
                raise ValueError("技力回放需要 Y 模组、受击者的光环标志和技力阻回状态")
            gauge,_=deploy_skill(tables,card,frame)
            self.recovery=NymphRecovery(gauge,period,config["target_attached"],config.get("stopped",False))
            self.recovery_loadout=tables,card
        self.skill = {r["key"]:_raw_f32(r["value"]) for r in card["skill_row"]["blackboard"]}
        talent = _board(card,1,"2")
        self.global_talent = card["module"] == "uniequip_002_nymph" and card["module_level"] >= 2
        self.stacks = 0
        self.limit = int(talent["max_stack_cnt"])
        self.stack_bonus = _raw_f32(talent["atk"])
        self.base = _raw_f32(card["panel_attributes"]["atk"])
        self.burst_factor = _raw_f32(_trait_values(card).get("damage_scale",1))
        if self.slot == 3:
            attack, = [p["fields"] for p in ev["character"]["components"]
                       if p["path"] == "Modes/S3/Body/Attack" and p["script"] == "RangedAttackWithExtraActions"]
            self.s3_scales = s3_damage_scales(attack,{r["key"]:r["value"] for r in card["skill_row"]["blackboard"]})

    @property
    def attack(self):
        skill_bonus = self.skill["atk"] if self.s3_active else 0
        return mul(self.base, ONE + self.stacks*self.stack_bonus + skill_bonus)

    def on_dark_start(self):
        if self.soul.source_present and (self.watching or self.global_talent):
            self.stacks = min(self.limit,self.stacks+1)

    def sync_recovery(self,state,frame):
        if self.recovery is not None and self.soul.source_present:
            inside=self.recovery.attached and state.burst is not None and state.hp>HP_EPSILON
            self.recovery.sync(frame,inside)

    def handle(self, state, frame, hit, base_resistance):
        kind = hit["kind"]
        if kind == "nymph_withdraw":
            self.soul.withdraw()
            return
        if kind == "nymph_redeploy":
            attached = hit["talent_attached"]
            if type(attached) is not bool:
                raise ValueError("重新部署时需给出受击者实际持有的天赋范围标志")
            self.soul.redeploy()
            self.watching = attached
            self.s3_active = False
            self.stacks = 0
            if self.recovery is not None:
                self.recovery.gauge,_=deploy_skill(*self.recovery_loadout,frame)
                self.recovery.attached=self.recovery.stopped=False
                self.recovery.last_tick=None
            return
        if not self.soul.source_present:
            raise ValueError("该妮芙已经离场")
        if kind in ("nymph_y_contact","nymph_sp_block","nymph_sp_tick"):
            if self.recovery is None:
                raise ValueError("需要初始化妮芙 Y 的技力回复状态")
            if kind=="nymph_sp_tick":
                self.recovery.tick(frame)
            else:
                active=hit["active"]
                if type(active) is not bool:
                    raise ValueError("光环接触或技力阻回状态需使用布尔值")
                if kind=="nymph_y_contact":
                    self.recovery.attached=active
                else:
                    self.recovery.stopped=active
            return
        if kind in ("nymph_aura_contact","nymph_s3_mode"):
            active = hit["active"]
            if type(active) is not bool or kind == "nymph_s3_mode" and self.slot != 3:
                raise ValueError("妮芙模式或范围天赋回调与装载不符")
            if kind == "nymph_aura_contact":
                self.watching = active
            else:
                self.s3_active = active
            return
        if kind not in ("nymph_s2_hit","nymph_s3_hit","nymph_basic_hit"):
            raise ValueError("未知妮芙命中回调")
        # The projectile owns its damage action. Its living source supplies the
        # current ATK even when the mode changed between release and impact.
        if kind == "nymph_s3_hit" and self.slot != 3 or kind == "nymph_s2_hit" and self.slot != 2:
            raise ValueError("妮芙弹体与装备的技能不符")
        if state.hp <= HP_EPSILON:
            return
        resistance = hit.get("effective_arts_resistance_q32")
        if resistance is None:
            if base_resistance is None:
                raise ValueError("妮芙法伤需要目标基础法抗或本次实际法抗")
            resistance = base_resistance+state.res_add
        self.soul.before_attack_damage()
        factor = self.burst_factor if state.burst else ONE
        if kind == "nymph_s3_hit":
            apply_s3_hit(state,frame,self.attack,resistance,factor,self.s3_scales)
            return
        previous = state.hp
        scale = self.skill["atk_scale"] if kind == "nymph_s2_hit" else ONE
        state.receive_ordinary(frame,source=NYMPH,damage_type="arts",attack_q32=mul(self.attack,scale),
                               mitigation_q32=resistance,output_scale_q32=factor)
        if kind == "nymph_s2_hit":
            state.apply_buildup(frame,"DARK",mul(previous-state.hp,self.skill["ep_damage_ratio"]))
            self.soul.after_s2_buildup(state,reborn=hit.get("reborn",False))
