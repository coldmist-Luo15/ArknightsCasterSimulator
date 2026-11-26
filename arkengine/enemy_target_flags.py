"""Target flags supplied by V079 enemy abilities and time-bounded combat effects."""
from collections import defaultdict

from .caster_prefabs import _prefab
from .mystic_timing import fixed_wait_frames


FLAG_NAMES={2:"TARGET_FREE",5:"INVINCIBLE",9:"INVISIBLE",32:"TOWER_TARGET_FREE"}


def highland_passives(tables,enemy):
    prefab=_prefab(tables,"enemies",enemy)
    parts={p["id"]:p for p in (prefab or {}).get("components",()) if p["enabled"] and p["active"]}
    flags={}
    for part in parts.values():
        if part["script"]!="ToggleablePassiveBuffAbility":
            continue
        ability=part["fields"]
        checker=parts.get(ability["_checker"]["m_PathID"],{})
        opts=checker.get("fields",{})
        if (checker.get("script")=="AdvancedCompoundToggleChecker" and opts["_isInitToggled"]
                and opts["_disableWhenBlocked"] and not ability["_isInverseToggle"]
                and not any(v for k,v in opts.items() if k.startswith("_") and k not in
                            ("_isInitToggled","_disableWhenBlocked","_restoreDelay"))):
            for buff in ability["_buffs"]:
                if buff["templateKey"]=="empty":
                    delay=fixed_wait_frames(opts["_restoreDelay"]/tables.fixed_timestep()) if opts["_restoreDelay"]>0 else 0
                    flags.update((FLAG_NAMES[f],delay) for f in buff["attributes"]["abnormalFlags"] if f in FLAG_NAMES)
    return flags


class EnemyTargetFlags:
    def __init__(self,tables,scene,births,spans):
        by_type={}
        self.passives={}
        self.held=set()
        self.released={}
        self.spans=defaultdict(list)
        for key,birth in births.items():
            enemy=birth["enemy_key"]
            prefab=scene.enemies[enemy]["prefabKey"]["m_value"] or enemy
            if prefab not in by_type:
                by_type[prefab]=highland_passives(tables,prefab)
            self.passives[key]=by_type[prefab]
        for span in spans:
            self.spans[span["target"]].append(span)

    def at(self,key,frame):
        flags={s["flag"] for s in self.spans.get(key,()) if s["start_frame"]<=frame<=s["end_frame"]}
        if key not in self.held:
            flags.update(flag for flag,delay in self.passives[key].items()
                         if key not in self.released or frame>=self.released[key]+delay)
        return flags

    def update_blocked(self,keys,frame):
        for key in self.held-keys:
            self.released[key]=frame
        self.held=keys
