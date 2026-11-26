"""One enemy_esabr_add_energy ON_BUFF_START, not range selection or a game clock.

Conditional bridge: 2.7.91 template, CN 78.0 non-hotfix action bodies. Stable
resolved target through both ContainsBuff checks. For the numeric branch only:
existing numeric ability entry, valid incoming blackboard, ordinary in-bounds
list replacement, no callbacks/overrides. Values supplied are the two GetFloat
results, NOT the enemy table defaults. Never infers attachment or overload.
"""
from dataclasses import dataclass
from enum import Enum
import math
import struct


MARK_KEY = 'blacksteel_energy[mark]'
OVERLOAD_KEY = 'act28side_over_energy_buff'


@dataclass(frozen=True)
class BuffEntry:
    """A resolved buff at the observation point, not an AddBuff return or UID."""
    key: str
    enabled: bool

    def __post_init__(self):
        if not isinstance(self.key, str) or not self.key.isascii() or not self.key or type(self.enabled) is not bool:
            raise ValueError('Expected a resolved ASCII buff key and enabled bit')


def receive_from_container(entries, *, complete: bool, target_present: bool | None, **blackboard_inputs):
    """Stable receiver snapshot through both checks; None *entries* within it are resolved-null.

    A missing key is negative only for a complete enumeration. A known enabled
    match in a partial enumeration is sufficient. No key is inferred from the prefab.
    """
    if type(complete) is not bool:
        raise ValueError('Specify whether enumeration was complete')
    seen = set()
    for entry in entries:
        if entry is None:
            continue
        if not isinstance(entry, BuffEntry):
            raise ValueError('Expected a resolved entry or resolved-null reference')
        if entry.enabled:
            seen.add(entry.key)
    def contains(key):
        return True if key in seen else (False if complete else None)
    return receive_energy(target_present=target_present, marked=contains(MARK_KEY),
                          overloaded=contains(OVERLOAD_KEY), **blackboard_inputs)


class TriggerLookup(Enum):
    MISSING = 'missing'
    NOT_ENERGY = 'found_but_energy_cast_unusable'
    ENERGY = 'usable_energy_ability'


@dataclass(frozen=True)
class ReceiveTrace:
    executed: tuple[str, ...]
    reason: str
    stored_before_update: float | None
    update_called: bool | None
    # Result of ActionUtil.RunActions, not of Buff.OnStart or of applying overload.
    actions_result: bool | None


def _f32(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 2**30:
        raise ValueError('Expected finite resolved blackboard value in the supported range')
    return struct.unpack('<f', struct.pack('<f', value))[0]


def receive_energy(*, target_present=None, marked=None, overloaded=None,
                   add_ability_present=None, ability_board_present=None,
                   existing_energy=None, incoming_add=None, trigger_lookup=None):
    """Return the proven prefix. None is unknown, never a failed guard or a zero."""
    for flag in (target_present, marked, overloaded, add_ability_present, ability_board_present):
        if flag is not None and type(flag) is not bool:
            raise ValueError('Expected bool or unknown')
    if trigger_lookup is not None and not isinstance(trigger_lookup, TriggerLookup):
        raise ValueError('Expected a resolved trigger lookup or unknown')
    steps = ['check_mark']
    def done(reason, updated, success, stored=None):
        return ReceiveTrace(tuple(steps), reason, stored, updated, success)

    if target_present is False:
        return done('null_target', False, False)
    if target_present is None or marked is None:
        return done('target_or_mark_unknown', None, None)
    if not marked:
        return done('missing_mark', False, False)
    steps.append('check_overload')
    if overloaded is None:
        return done('overload_unknown', None, None)
    if overloaded:
        return done('already_overloaded', False, False)
    steps.extend(('if_not', 'add_blackboard'))
    if add_ability_present is False:
        return done('missing_add_ability', False, False)
    if add_ability_present is None:
        return done('add_ability_unknown', None, None)
    if ability_board_present is False:
        return done('missing_ability_board', False, False)
    if ability_board_present is None or existing_energy is None or incoming_add is None:
        return done('write_inputs_unresolved', None, None)

    stored = _f32(_f32(existing_energy) + _f32(incoming_add))
    steps.append('trigger_specified_ability')
    if trigger_lookup is None:
        return done('trigger_lookup_unknown', None, None, stored)
    if trigger_lookup is TriggerLookup.MISSING:
        return done('missing_trigger_ability', False, False, stored)
    if trigger_lookup is TriggerLookup.NOT_ENERGY:
        return done('unusable_energy_cast', False, True, stored)
    # This result assumes UpdateEnergy returned normally; it can return early.
    return done('update_called', True, True, stored)
