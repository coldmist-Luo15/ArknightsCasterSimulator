"""V079 ChargeTimeListener and FilterBuffToggleChecker for Veen's talent and MSC-Y."""
from dataclasses import dataclass


@dataclass
class StorageToggle:
    threshold: int
    marker: bool = False
    active: bool = False
    remaining: int = 0

    def observe_charge(self, units):
        """ChargeTimeListener.OnTick sets/removes its marker on a threshold crossing."""
        self.marker = units >= self.threshold

    def attach_checker(self):
        # CheckInitialToggled reads ContainsBuff; OnAttached resets ticker to zero.
        self.active = self.marker
        self.remaining = 0

    def tick_checker(self):
        self.remaining = max(0, self.remaining - 1)
        if self.remaining == 0:
            self.remaining = 10
            self.active = self.marker


def storage_modifiers(has_stored_marker, has_merged_marker, talent, module_speed):
    return {
        "attack_ratio": talent["atk"] if has_merged_marker else 0,
        "attack_speed": (module_speed if has_stored_marker else 0)
                        + (0 if has_merged_marker else talent["attack_speed"]),
    }


def storage_callbacks(merge, cap, talent, module_speed, callbacks):
    """Run ordered native callbacks; the caller supplies their order within an owner tick."""
    merged = StorageToggle(merge)
    ordinary = StorageToggle(1)
    events = []
    previous = -1
    for callback in callbacks:
        frame = callback["frame"]
        if type(frame) is not int or frame < previous or frame < 0:
            raise ValueError("储能回调帧须非负且按发生顺序提供")
        previous = frame
        kind = callback["kind"]
        if kind == "charge_listener":
            units = callback["energy_units"]
            if type(units) is not int or not 0 <= units <= cap:
                raise ValueError("储存的能量超出该干员上限")
            merged.observe_charge(units)
            ordinary.observe_charge(units)
        elif kind == "attach":
            ordinary.attach_checker()
            merged.attach_checker()
        elif kind == "talent_tick":
            merged.tick_checker()
        elif kind == "module_tick":
            ordinary.tick_checker()
        else:
            raise ValueError(f"未知储能回调 {kind}")
        events.append(dict(frame=frame, kind=kind, has_stored=ordinary.active, has_merged=merged.active,
                           **storage_modifiers(ordinary.active, merged.active, talent, module_speed)))
    return dict(events=events)
