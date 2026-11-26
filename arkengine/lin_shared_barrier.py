"""Lin S2's source-owned aura and the independent interceptor on each recipient."""
from math import isfinite

from .attack_range import circle_in_range, range_cells
from .damage import HP_EPSILON, ordinary_arts_hit
from .fixed import ONE, mul
from .lin_equipment import lin_output_stacks
from .lin_interactions import LIN, barrier_hit, lin_with_block_buffs
from .module_effects import active_board, talent_rows
from .skill_recovery import READY_EPSILON
from .volley_damage import _raw_f32


class SharedLinBarriers:
    def __init__(self, tables, card, evidence):
        self.tables = tables
        self.card = card
        parts = evidence["character"]["components"]
        aura, = (part["fields"] for part in parts if part["path"] == "Modes/S2/Body/Talents/1"
                 and part["script"] == "AuraAbility")
        validator, = (p["fields"]["_targetOptions"] for p in parts if p["id"] == aura["_targetValidator"]["m_PathID"])
        take = evidence["templates"]["lin_t_1"]["eventToActions"]["ON_TAKE_DAMAGE"]
        select, disable = take[4:6]
        damage = evidence["templates"]["lin_t_1[damage]"]["eventToActions"]
        if (aura["_selfOption"] != 2 or aura["_removeBuffWhenTargetLeave"] or not aura["_removeBuffWhenAbilityDetached"]
                or (validator["targetSide"], validator["targetMotion"]) != (1,1)
                or [b["templateKey"] for b in aura["_buffs"] + aura["_passiveBuffs"]] != ["lin_t_1","lin_t_1"]
                or (select["_abilityName"], select["_targetType"], select["_sourceType"]) != ("Attack","BUFF_OWNER","SOURCE")
                or select["_useAbilityFromTarget"] or select["_buff"]["buffKey"] != "lin_t_1[damage]"
                or disable["_buffOwner"] != "BUFF_OWNER" or disable["_buff"]["buffKey"] != "lin_t_1[disable]"
                or not take[0]["_checkBuffSource"] or take[3]["_condType"] != "LT"
                or damage["ON_BUFF_START"][0]["_buff"]["buffKey"] != "stun"
                or damage["ON_BUFF_LATE_ENABLE"][0]["_atkScaleVar"] != "atk_scale"):
            raise ValueError("荫庇的友军资格、离场清理或破壁来源和时点已变化")
        unit = tables.read("excel/character_table.json")[LIN]
        phase = card["module_phase"] or {}
        rows = talent_rows(phase, 0, "1", unit["talents"][0]["candidates"])
        self.talent = active_board(rows, card["elite"], card["level"], card["potential"])
        self.step = _raw_f32(tables.fixed_timestep())
        lifetime = _raw_f32(select["_buff"]["lifeTime"])
        self.damage_buff_ticks = (lifetime+self.step-1)//self.step
        self.cooldown = (_raw_f32(self.talent["interval"]) - READY_EPSILON + self.step) // self.step
        self.stacks = lin_output_stacks(tables, phase, card)
        self.holders = {}

    def attach(self, holder):
        # Repeated aura contact never restarts a broken recipient's own disable buff.
        self.holders.setdefault(holder, None)

    def advance(self, frame):
        restored = [key for key, due in self.holders.items() if due is not None and frame >= due]
        for key in restored:
            self.holders[key] = None
        return restored

    def detach(self):
        removed = [key for key in self.holders if key != LIN]
        for key in removed:
            del self.holders[key]
        return removed  # Lin's ordinary talent keeps her own current disable buff.

    def receive(self, frame, holder, damage, ignore_block=False, *, threshold_q32=None, packet=None, defer_break=False):
        active = holder in self.holders and self.holders[holder] is None
        threshold=_raw_f32(self.talent["value"]) if threshold_q32 is None else threshold_q32
        packet=packet or {}
        ignore_block=packet.get("ignore_block",ignore_block)
        if "attached_block_buffs" in packet:
            hit=lin_with_block_buffs(self.tables,damage,threshold,active=active,ignore_block=ignore_block,
                attached=packet["attached_block_buffs"],damage_type=packet.get("damage_type"))
        else:
            hit=barrier_hit(damage, threshold, active=active, ignore_block=ignore_block)
        if hit["breaks"] and not defer_break:
            self.holders[holder] = frame + self.cooldown
        return hit

    def victims(self, placement, rows, cols, enemies):
        cells = range_cells(self.card["base_range"], placement, rows, cols)
        return [key for key, enemy in enemies.items() if enemy["hp_q32"] > HP_EPSILON and enemy.get("eligible", True)
                and circle_in_range(enemy["row"], enemy["col"], enemy["radius"], cells, box_query=True)]

    def late_damage(self, state, *, source_attack, aura_count):
        scale = ONE
        if self.stacks:
            if type(aura_count) is not int or aura_count < 0:
                raise ValueError("PLX-Y 破壁法伤需提供伤害回调时林自己的光环人数")
            scale += _raw_f32(self.stacks["per_enemy"]) * min(aura_count, self.stacks["maximum"])
        amount = mul(source_attack, _raw_f32(self.talent["atk_scale"]))
        packet = ordinary_arts_hit(attack_raw=amount, resistance_raw=state["resistance_q32"],
                                    hp_raw=state["hp_q32"], output_scale_raw=scale)
        state["hp_q32"] = packet.hp_after_raw
        return dict(source=LIN, damage_q32=packet.damage_raw, hp_after_q32=packet.hp_after_raw)


def replay_shared_barriers(tables, card, evidence, event):
    """Accepted HP modifiers after recipient OBJECTS. Late-enable callbacks are supplied in actual order."""
    group = SharedLinBarriers(tables, card, evidence)
    start, end = event["frame"], event["end_frame"]
    rows, cols = event["rows"], event["cols"]
    if any(type(x) is not int or x < 1 for x in (rows, cols)) or type(end) is not int or end < start:
        raise ValueError("荫庇回放需要有效地图和帧区间")

    def placement(pos):
        if not (type(pos["row"]) is int and 0 <= pos["row"] < rows and
                type(pos["col"]) is int and 0 <= pos["col"] < cols):
            raise ValueError("持盾者格子必须位于地图内")
        return dict(row=pos["row"], col=pos["col"], direction="RIGHT")

    locations = {k:placement(v) for k,v in event["allies"].items()}
    hp = {k:_raw_f32(v["hp"]) for k,v in event["allies"].items()}
    if LIN not in locations or any(v <= 0 for v in hp.values()):
        raise ValueError("荫庇回放需要存活的林和有效的友方生命")
    aura = set(range_cells(card["base_range"], locations[LIN], rows, cols))
    enemies = {k:dict(v, hp_q32=_raw_f32(v["hp"]), resistance_q32=_raw_f32(v["resistance"]))
               for k,v in event["enemies"].items()}
    if any(not all(isfinite(v[x]) for x in ("row","col","radius","hp","resistance")) or
            v["hp"] <= 0 or v["radius"] < 0 for v in enemies.values()):
        raise ValueError("破壁受击者的位置、生命、抗性和碰撞半径无效")
    for key,pos in locations.items():
        if (pos["row"],pos["col"]) in aura:
            group.attach(key)
    active = True
    events = [dict(frame=start,kind="barrier_holders_snapshot",holders=list(group.holders))]
    pending = {}
    stuns = {}
    cursor = start
    for obs in [*event["observations"], dict(frame=end,kind="end")]:
        frame,kind = obs["frame"],obs["kind"]
        if type(frame) is not int or not cursor <= frame <= end:
            raise ValueError("受击及晚启用回调必须按实际顺序位于回放区间")
        cursor = frame
        for key,due in tuple(group.holders.items()):
            if due is not None and due <= frame:
                events.append(dict(kind="barrier_restored",frame=due,holder=key))
        group.advance(frame)
        if kind == "end":
            continue
        if kind == "mode_exit":
            if not active:
                raise ValueError("荫庇已经移除")
            active = False
            events.append(dict(kind=kind,frame=frame,removed=group.detach()))
        elif kind == "move_ally":
            key = obs["holder"]
            if key == LIN or not hp[key]:
                raise ValueError("此回放要求林固定部署、移动对象存活")
            locations[key] = placement(obs)
            if active and (obs["row"],obs["col"]) in aura:
                group.attach(key)
        elif kind == "incoming_hit":
            key = obs["holder"]
            if not hp[key]:
                raise ValueError("受击者已经离场")
            hit = group.receive(frame,key,_raw_f32(obs["damage_after_defense"]),obs.get("ignore_block",False))
            if hit["breaks"]:
                token = obs["hit_id"]
                if token in pending:
                    raise ValueError("破盾回调标识重复")
                victims = group.victims(locations[key],rows,cols,enemies)
                pending[token] = dict(frame=frame,targets=victims)
                for victim in victims:
                    if not enemies[victim].get("stun_immune",False):
                        life = mul(_raw_f32(group.talent["stun"]), _raw_f32(enemies[victim].get("status_duration_scale",1)))
                        stuns[victim] = max(stuns.get(victim,0),frame+(life+group.step-1)//group.step)
                events.append(dict(kind="barrier_shatter",frame=frame,holder=key,source=LIN,targets=victims,
                                   stun_until={k:stuns[k] for k in victims if k in stuns}))
            hp[key] = max(0,hp[key]-hit["hp_loss_q32"])
            if hp[key] <= HP_EPSILON:
                hp[key] = 0
                group.holders.pop(key,None)
                if key == LIN:
                    active = False
                    group.detach()
            events.append(dict(kind=kind,frame=frame,holder=key,hp_after_q32=hp[key],**hit))
        elif kind == "late_enable":
            shatter = pending.pop(obs["hit_id"])
            if frame-shatter["frame"] > group.damage_buff_ticks:
                raise ValueError("破裂伤害 Buff 已超出晚启用回调的存续窗口")
            victims = shatter["targets"]
            attack = _raw_f32(obs.get("source_attack",card["panel_attributes"]["atk"]))
            hits = {key:group.late_damage(enemies[key],source_attack=attack,aura_count=obs.get("aura_count"))
                    for key in victims if enemies[key]["hp_q32"] > HP_EPSILON}
            events.append(dict(kind="shatter_damage",frame=frame,hits=hits))
        else:
            raise ValueError("未知的荫庇回调 "+kind)
    return dict(status="shared_barrier_callbacks",through_frame=end,events=sorted(events,key=lambda row:row["frame"]),
                holder_recovery_frame=group.holders,ally_hp_q32=hp,enemy_hp_q32={k:v["hp_q32"] for k,v in enemies.items()},
                pending_late_enable=pending)
