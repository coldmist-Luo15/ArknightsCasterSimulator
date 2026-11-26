"""CN 78.0 base enemy spell-start checks, before dispatch to the target(s).

PASS means only that these checks passed. It does not promise an active cast,
valid target, projectile, hit, or damage. No video or animation clock here.
"""
from dataclasses import dataclass, replace
from enum import Enum


# CN 78.0 enemy_1003_ncbow Default._attack on the base init path. Not a
# fallback for other abilities, pooled references or later option overrides.
CROSSBOW_DEFAULT_ATTACK_FAMILY = 0


class SpellGate(Enum):
    PASS = 'pass_to_target_processing'
    OWNER_DEAD = 'OWNER_DEAD'
    PALSY = 'PALSY'


@dataclass(frozen=True)
class EnemySpellSample:
    owner_ref_valid: bool
    owner_alive: bool  # Entity.alive result, not merely whether a pooled reference exists
    ignore_owner_dead: bool
    ability_present: bool  # non-null comparison in Enemy.CanDoAbilitySpellOn
    ignore_palsy_interrupt: bool
    family_group: int  # runtime Ability options, not a serialized damage type
    effective_palsy: bool  # resolved status, not an attempt to apply control

    def check(self) -> SpellGate:
        if not self.owner_ref_valid or not (self.owner_alive or self.ignore_owner_dead):
            return SpellGate.OWNER_DEAD
        if (self.ability_present and not self.ignore_palsy_interrupt
                and (1 << (self.family_group & 31)) & 3 and self.effective_palsy):
            return SpellGate.PALSY
        return SpellGate.PASS


class UnresolvedSpellOrder(ValueError):
    """The change and the affected check share a logic frame and order matters."""


def single_change_window(before: EnemySpellSample, *, change_frame: int, check_frame: int,
                         owner_alive: bool | None = None, effective_palsy: bool | None = None,
                         change_first: bool | None = None) -> SpellGate:
    """One resolved status change vs its particular predicate read, not vs shot creation.

    All other sampled predicates and the ability instance must stay fixed. The caller
    must establish that the cast actually reached this check. No default tie-breaker.
    """
    if any(type(n) is not int or n < 0 for n in (change_frame, check_frame)):
        raise ValueError('Expected non-negative integer game frames')
    updates = {k: v for k, v in [('owner_alive', owner_alive), ('effective_palsy', effective_palsy)]
               if v is not None}
    if len(updates) != 1 or any(type(v) is not bool for v in updates.values()):
        raise ValueError('Expected exactly one boolean predicate update')
    if change_first is not None and (type(change_first) is not bool or change_frame != check_frame):
        raise ValueError('An explicit same-frame order only applies at a tie')
    after = replace(before, **updates)
    if change_frame < check_frame or change_first is True:
        return after.check()
    if change_frame > check_frame or change_first is False:
        return before.check()
    if before.check() != after.check():
        raise UnresolvedSpellOrder(f'Frame {check_frame}: order of {next(iter(updates))} and its read is unknown')
    return before.check()
