"""SILENCED on the recipient's object clock, including multiple DEFAULT buff instances."""
from .volley_damage import _raw_f32


class ReceiverSilence:
    def __init__(self):
        self.expires=(0,0)

    def clear(self):
        self.expires=(0,0)

    def attach(self,tables,frame,seconds,*,receiver_born=None,projectile_born=None,receiver_first_on_tie=None):
        template=tables.read("buff_table.json")["silence"]
        if (template["overrideType"]!="DEFAULT" or template["statusResistable"]!="AUTOMATIC" or
                template["attributes"]["abnormalFlags"]!=["SILENCED"]):
            raise ValueError("沉默标志、抵抗或覆盖规则变化")
        step=_raw_f32(tables.fixed_timestep())
        ticks=(_raw_f32(seconds)+step-1)//step
        low=high=frame
        if receiver_born is not None:
            low-=int(receiver_born>=projectile_born)
            high-=int(receiver_born>projectile_born)
            if receiver_born==projectile_born and receiver_first_on_tie is not None:
                low=high=frame-int(not receiver_first_on_tie)
        self.expires=(max(self.expires[0],low+ticks),max(self.expires[1],high+ticks))
        return self.expires

    def gates(self,receiver_tick,*,ignore_silence=False,silenceable_buff=True):
        """Other availability gates are evaluated by the caller, before or after this one."""
        low,high=self.expires
        held=True if receiver_tick<low else False if receiver_tick>=high else None
        return dict(silenced=held,skill_gate_passes=True if ignore_silence else None if held is None else not held,
                    buff_gate_passes=True if not silenceable_buff else None if held is None else not held)
