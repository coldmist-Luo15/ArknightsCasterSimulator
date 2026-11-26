"""One ordinary enemy attack from ability start through its release or paralysis interruption."""
from collections import Counter

from .attack_range import circle_in_range
from .damage import HP_EPSILON
from .elemental_casters import MANTRA, _board
from .elemental_projectiles import ranged_shot_release,stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .mantra_stage import PalsyAura,palsy_delay
from .mantra_battle import burst_output_scale
from .mystic_timing import fixed_wait_frames
from .skill_recovery import RecoveryTimer
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def run_mantra_interrupt(tables, card, event, stream):
    if card["operator_id"] != MANTRA or card["skill"] not in (1, 2):
        raise ValueError("此段为真言一、二技能下普通敌方攻击的麻痹回调")
    graph = tables.read("battle/buff_template_data.json")
    buffs = tables.read("buff_table.json")
    stack = graph["palsy[stack]"]["eventToActions"]
    immune, family, ignore, create = stack["ON_ABILITY_START"]
    finish, forced = stack["ON_ABILITY_FINISH"]
    delay = buffs[create["_buffKey"]]
    trigger, = graph[delay["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    lock, _, palsying, consume, _, signal, unlock = stack["ON_BUFF_TRIGGER"]
    dice, debit = consume["_succeedNodes"]
    damage = graph["mantra_t_1"]["eventToActions"]["ON_TRIGGER_PALSY"][0]
    if (immune["_abnormalFlag"] != "PALSY" or family["_familyGroupMask"] != "ATTACK_OR_COMBAT"
            or not ignore["_unset"] or ignore["_skipUnitCheck"] or finish["_finishReason"] != "PALSY"
            or not forced["_force"] or trigger["_buffKeys"] != ["palsy[stack]"] or not trigger["_forceTrigger"]
            or not delay["waitFirstTriggerInterval"] or delay["firstTriggerInterval"] != -1 or delay["triggerCnt"] != 1
            or lock["_var"] != "lock" or unlock["_value"] != 0 or not debit["_decCntIfStack"]
            or not dice["_isReverseProb"] or signal["_target"] != "BUFF_OWNER"
            or damage["_damageType"] != "ELEMENT" or damage["_attackType"] != "BUFF"):
        raise ValueError("麻痹中断或噤声限域回调图变化")
    start, spell = event["frame"], event["spell_frame"]
    stacks = event["stacks"]
    if (type(spell) is not int or spell < start or type(stacks) is not int
            or not 0 <= stacks <= buffs["palsy[stack]"]["maxStackCnt"]):
        raise ValueError("普通攻击需要有效的出手帧及基础麻痹层数")
    immune_status, ignores, aura = (event[k] for k in ("palsy_immune", "ability_ignores_palsy", "talent_aura_active"))
    if not all(type(v) is bool for v in (immune_status, ignores, aura)):
        raise ValueError("需要确认本次免疫、能力忽略麻痹及光环状态")
    receiver = EnemyElements.from_scene_enemy(tables, event["enemy"])
    log = [dict(kind="enemy_attack_start", frame=start)]
    def result(frame, stopped):
        return dict(frame=frame, interrupted=stopped, stages=log, remaining_stacks=stacks, hp_after_q32=receiver.hp)
    if not stacks or immune_status or ignores:
        log.append(dict(kind="enemy_spell_allowed", frame=spell))
        return result(spell, False)

    # A long windup may be interrupted by the delayed buff before SpellOn.
    first = event["first_trigger_update_frame"]
    if type(first) is not int or first not in (start, start + 1):
        raise ValueError("需要实际挂载后首次更新延迟触发器的帧")
    timer = RecoveryTimer(_raw_f32(delay["triggerInterval"]))
    step = _raw_f32(tables.fixed_timestep())
    frame = spell
    cause = "spell_gate"
    for tick in range(first, spell + 1):
        if timer.advance(step):
            if tick == spell:
                cause = "spell_gate_or_delayed_trigger_same_frame"
            else:
                frame, cause = tick, "delayed_trigger"
            break
    status = buffs[palsying["_buffKey"]]
    duration = next(b["value"] for b in status["blackboard"] if b["key"] == status["durationKey"])
    log.append(dict(kind="enemy_attack_interrupted", frame=frame, cause=cause, finish_reason="PALSY",
                    status=status["buffKey"], status_seconds=duration))
    talent = _board(card, 0, "1")
    # PriorityBBKeys=["prob"] and the shared override group enable only its
    # highest-priority live buff. The damage listener is a separate buff.
    other = event.get("other_retention_probabilities", [])
    if not isinstance(other, list) or any(type(p) not in (int, float) or not 0 <= p <= 1 for p in other):
        raise ValueError("同组保层来源需要给出当前生效范围内各来源的有效概率")
    probabilities = [*other, talent[dice["_probKey"]]] if aura else other
    retention = max(probabilities) if probabilities else None
    if retention is not None:
        if stream is None:
            raise ValueError("保层抽样需要已定位的战斗随机流")
        # DiceByBuffKeys._ReverseProb supplies 1 - prob to the consume roll.
        # Inverting a roll of prob changes outcomes for a fixed battle seed.
        spent = stream.dice(1 - retention)
    else:
        spent = True
    stacks -= int(spent)
    log.append(dict(kind="palsy_stack_settlement", frame=frame, spent=spent, remaining=stacks,
                    retention_probability=retention))
    if aura:
        power = mul(_raw_f32(card["panel_attributes"]["atk"]), _raw_f32(talent[damage["_atkScaleVar"]]))
        scale = burst_output_scale(card) if event.get("elemental_burst_active") else ONE
        log.append(receiver.apply_elemental_hp(frame, power, source="mantra_t_1", output_scale_q32=scale))
    return result(frame, True)


class MantraCrossbowWave:
    """Hostile release, paralysis and friendly tracking on the same moving wave."""
    def __init__(self,tables,card,evidence,world,deploy,event,states,cells,radii,logs,seed):
        self.world,self.states,self.cells,self.radii=world,states,cells,radii
        self.tables,self.deploy,self.event,self.evidence,self.logs=tables,deploy,event,evidence,logs
        self.dt=tables.fixed_timestep()
        self.attack=world.incoming
        if self.attack:
            self.attack.live_projectiles=True
        self.rng=tables.combat_random(seed)
        self.births=Counter(b["frame"] for b in world.births.values())
        if self.rng:
            for _ in range(2*sum(n for f,n in self.births.items() if f<=deploy["frame"])):
                self.rng.value()
        self.aura=PalsyAura(evidence,world)
        self.delay=palsy_delay(tables)
        self.talent=_board(card,0,"1")
        self.source=_raw_f32(card["panel_attributes"]["atk"])
        self.module=burst_output_scale(card)
        graph=tables.read("battle/buff_template_data.json")["palsy[stack]"]["eventToActions"]
        buffs=tables.read("buff_table.json")
        effect=buffs[graph["ON_BUFF_TRIGGER"][2]["_buffKey"]]
        seconds,=(v["value"] for v in effect["blackboard"] if v["key"]==effect["durationKey"])
        self.control=fixed_wait_frames(seconds/self.dt)+1
        self.flying=[]
        self.face_sign=-1 if deploy["direction"]=="LEFT" else 1

    def advance(self,frame):
        self.world.receive(frame,self.states,self.logs,before_fire=self._interrupt,hit_label="incoming_hit",
                           interrupt_delay=lambda key:self.delay if self.states[key].palsy_stacks else None)
        if self.rng:
            for _ in range(2*self.births[frame]):
                self.rng.value()
        return None

    def _interrupt(self,frame,key,position):
        state=self.states[key]
        if not state.palsy_stacks or state.palsy_immune:
            return True
        aura=key in self.aura.members and self.world.present
        state.palsy_stacks-=int(not aura or self.rng.dice(1-self.talent["prob"]))
        self.attack.stun(key,frame+self.control)
        if aura:
            state.apply_elemental_hp(frame,mul(self.source,_raw_f32(self.talent["atk_scale"])),source="mantra_t_1",
                                     output_scale_q32=self.module if state.burst else ONE)
        self.logs.append(dict(kind="enemy_attack_interrupted",frame=frame,target=key,stacks=state.palsy_stacks,
                              hp_after_q32=state.hp))
        return False

    def schedule(self,frame,attack,interval,target):
        shot=ranged_shot_release(self.tables,self.evidence,attack,attack_frame=frame,attack_interval=interval,
                                 facing=self.event["view"])
        delta=target["col"]-self.deploy["col"]
        if delta:
            self.face_sign=1 if delta>0 else -1
        return shot,self.face_sign

    def release(self,shot,sign,target,frame,attack,enhanced):
        row=self.world.receiver_at(target,frame)
        if row is None or self.states[target].hp<=HP_EPSILON:
            return
        origin,mount=stationary_cast_mounts(self.tables,MANTRA,shot["clock"],self.event["placement"],row,
                                           self.event["view"],caster_face_sign=sign)
        parts=self.evidence["projectiles"][attack["_projectileKey"]]["logic"]["components"]
        movement,=(p["fields"] for p in parts if p["script"]=="AdvancedMovement")
        if movement["_moveType"]!=1 or movement["_delayToStart"]:
            raise ValueError("真言普通移动弹道发生变化")
        self.flying.append(dict(key=target,born=frame,position=origin,speed=movement["_speed"],enhanced=enhanced,
                                offset=(mount[0]-row["col"],mount[1]-row["row"],mount[2])))

    def impacts(self,frame):
        for shot in tuple(self.flying):
            key,born=shot["key"],shot["born"]
            if frame<=born:
                continue
            row=self.world.by_id(key,frame,projectile_born=born)
            if row is None or self.states[key].hp<=HP_EPSILON:
                self.flying.remove(shot)
                self.logs.append(dict(kind="projectile_target_left",frame=frame,target=key))
                continue
            x,y,z=shot["offset"]
            hit=ordinary_trace_step(shot["position"],(row["col"]+x,row["row"]+y,z+row.get("height_offset",0)),
                                    effective_dt=self.dt,round_position=self.event["round_position"],speed=shot["speed"])
            shot["position"]=hit.position
            if hit.reached:
                self.flying.remove(shot)
                yield key,born,shot["enhanced"]
