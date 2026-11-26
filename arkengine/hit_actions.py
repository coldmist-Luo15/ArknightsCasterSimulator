"""CN 78.0 ordinary ApplyDamage routing, not the damage formula or an HP setter."""
from dataclasses import dataclass
from enum import Enum

from .fixed import ONE, SIGN64, add


class DamageRoute(Enum):
    NO_TARGET = 'no_target'
    NO_ATTACK = 'source_null_and_negative_cache'
    SOURCE = 'calculate_from_source'
    CACHE = 'calculate_without_source'


def _raw(value):
    if type(value) is not int or not -SIGN64 <= value < SIGN64:
        raise ValueError('Expected a signed Q32.32 raw integer')
    return value


@dataclass(frozen=True)
class DamageInput:
    route: DamageRoute
    attack_raw: int | None = None  # not final damage


class OrdinaryDamageNode:
    """Own one per action node, NOT per projectile; register the node by reference."""
    def __init__(self):
        self.cached_atk_raw = -ONE

    def preprocess(self, owner_atk_raw: int | None):
        # Outer Ability.PreprocessActionsForProjectile skips a Unity-null owner.
        if owner_atk_raw is not None:
            self.cached_atk_raw = _raw(owner_atk_raw)

    def select_input(self, *, target_present: bool, source_atk_raw: int | None) -> DamageInput:
        """Caller has resolved explicit/trace target and Unity-null source, not alive."""
        if type(target_present) is not bool:
            raise ValueError('Final target reference must already be resolved')
        if not target_present:
            return DamageInput(DamageRoute.NO_TARGET)
        if source_atk_raw is not None:
            return DamageInput(DamageRoute.SOURCE, _raw(source_atk_raw))
        if self.cached_atk_raw < 0:
            return DamageInput(DamageRoute.NO_ATTACK)
        return DamageInput(DamageRoute.CACHE, self.cached_atk_raw)


def ordinary_nonlethal_hp_store(*, hp_raw, max_hp_raw, final_delta_raw, alive_at_apply,
                                cancelled, alive_at_store):
    """Return the value written, or None for an explicit refusal. Not an ATK formula.

    Ordinary Character, mode permits, HP/MINUS, no ES, immortality, modifiers
    changing limits, redirection, or further callbacks. Only the nonlethal path.
    """
    if not all(type(x) is bool for x in (alive_at_apply, cancelled, alive_at_store)):
        raise ValueError('Receiver checks must be resolved')
    if not alive_at_apply or cancelled or not alive_at_store:
        return None
    hp, maximum, delta = map(_raw, (hp_raw, max_hp_raw, final_delta_raw))
    if not (0 < hp <= maximum and -hp < delta <= 0):
        raise ValueError('Outside ordinary nonlethal HP/MINUS path')
    return min(maximum, max(0, add(hp, delta)))
