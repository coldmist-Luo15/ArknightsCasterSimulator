"""绝处重燃的屏障、重燃形态与复起动画。"""
from .animation_assets import read_skin_actions
from .damage import HP_EPSILON
from .elemental_casters import BLAZE, _board
from .fixed import ONE, mul
from .mystic_timing import fixed_wait_frames
from .skill_recovery import RecoveryTimer, _divide
from .volley_damage import _raw_f32


class BlazeRekindle:
    def __init__(self, tables, card, evidence, world, events):
        self.world, self.events = world, events
        self.max_hp = _raw_f32(card["panel_attributes"]["maxHp"])
        talent = _board(card, 1, "2")
        self.step = tables.fixed_timestep()
        rate = mul(self.max_hp, _raw_f32(talent["hp_recovery_per_sec_by_max_hp_ratio"]))
        # Entity.OnAttributeDirty sets the period to 1 / rate. _UpdateHpRecovery awards 1 HP per tick.
        self.hp_period = _divide(ONE,rate)
        self.step_q32 = _raw_f32(self.step)
        self.barrier_size = _raw_f32(talent["dynamic"])
        self.stun_seconds = talent["stun"]
        components = evidence["character"]["components"]
        passive = next(p["fields"] for p in components if p["script"]=="PassiveBuffAbility" and p["path"].endswith("/Talents/2"))
        buff = next(b for b in passive["_buffs"] if b["templateKey"]=="blaze2_t_2")
        board = {r["key"]:r["value"] for r in buff["blackboard"]}
        self.full_hp_period = _raw_f32(board["interval"])
        self.inv_frames = (_raw_f32(board["inv_duration"])+self.step_q32-1)//self.step_q32
        self.radius = next(p["fields"]["m_Radius"] for p in components
                           if p["script"]=="CircleCollider2D" and p["path"].endswith("/CauseStun/Selector"))
        skin = read_skin_actions(tables, "chararts/"+BLAZE+".ab", "defaultskin", ["Revive_Begin","Revive_End"])
        self.animations = skin["views"]
        self.counter = _board(card,-1,"11_root") if card["module"]=="uniequip_003_blaze2" else {}
        self.reset()

    def reset(self):
        self.mode = "normal"
        self.shield = self.inv_until = self.animation_end = 0

    def damage(self, frame, amount, damage_type, facing):
        """Intercept TrySetHpZero once per normal form. A second lethal hit while down can remove the unit."""
        w = self.world
        if frame < self.inv_until or self.mode == "rising":
            return
        if self.mode != "normal" and damage_type not in ("ELEMENT","ELEMENTAL","HP_LOSS"):
            absorbed = min(self.shield, amount)
            self.shield -= absorbed
            amount -= absorbed
            if absorbed:
                self.events.append(dict(kind="reborn_barrier",frame=frame,absorbed_q32=absorbed,remaining_q32=self.shield))
        w.hp = max(0,w.hp-amount)
        if w.hp <= HP_EPSILON and self.mode=="normal":
            w.hp = ONE
            self.mode = "falling"
            self.shield = self.barrier_size
            self.regeneration = RecoveryTimer(self.hp_period)
            self.full_hp_check = RecoveryTimer(self.full_hp_period)
            self.full_hp_check.remaining = 0
            self.inv_until = frame + self.inv_frames
            length = self.animations[facing.lower()]["actions"]["Revive_Begin"]["duration"]
            self.animation_end = frame + fixed_wait_frames(length / self.step)
            self.events.append(dict(kind="reborn_start",frame=frame,barrier_q32=self.shield,inv_until=self.inv_until))

    def tick(self, frame, facing):
        if self.mode == "falling" and frame >= self.animation_end:
            self.mode = "down"
        if self.mode in ("falling","down"):
            restored = self.regeneration.advance(self.step_q32)
            self.world.hp = min(self.max_hp,self.world.hp+restored*ONE)
            if self.full_hp_check.advance(self.step_q32) and self.finish_if_full(frame,facing):
                return "full"
        elif self.mode == "rising" and frame >= self.animation_end:
            self.mode = "normal"
            self.events.append(dict(kind="reborn_end",frame=frame))
            return "ready"

    def finish_if_full(self, frame, facing):
        if self.mode!="down" or self.world.hp < self.max_hp:
            return False
        self.mode = "rising"
        self.shield = 0
        length = self.animations[facing.lower()]["actions"]["Revive_End"]["duration"]
        self.animation_end = frame + fixed_wait_frames(length / self.step)
        self.events.append(dict(kind="reborn_full_hp",frame=frame,animation_end=self.animation_end))
        return True
