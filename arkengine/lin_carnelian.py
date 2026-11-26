"""Carnelian S2's callbacks when she shares Lin's ordinary stage and enemy roster."""
from .caster_cast import run_committed_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON
from .fixed import ONE,mul
from .lin_output_aura import LinStageAura
from .module_effects import trait_board
from .phalanx_deployment import CarnelianCharge,carnelian_talent_first_tick,carnelian_taunt
from .phalanx_mode_scene import _active_mode,_timing
from .phalanx_skills import CARN,execute_phalanx_event
from .skill_recovery import RecoveryTimer,deploy_skill
from .stage_channel import UnsupportedChannelScene
from .volley_damage import _raw_f32


class CarnelianS2:
    def __init__(self,tables,card,placement,wave,end,events):
        self.tables,self.card,self.placement,self.wave=tables,card,placement,wave
        self.start,self.end,self.events=placement["frame"],end,events
        self.direction=placement["direction"]
        self.facing="Back" if self.direction=="UP" else "Front"
        self.evidence=caster_prefab_evidence(tables,CARN,2,skill_level=card["skill_level"],
                                            module_id=card["module"],module_level=card["module_level"])
        self.end_clock=run_committed_cast(tables,self.evidence,horizon=0,direction=self.direction,
                                         facing=self.facing)["natural_end"]
        if self.end_clock["end_phase"]!="objects":
            raise ValueError("沙缚镣锁的自然退态时相已变化")
        grid=wave.scene.level["mapData"]["map"]
        self.layout=dict(placement=placement,rows=len(grid),cols=len(grid[0]))
        self.cells=skill_range_cells(tables,card,self.layout)["cells"]
        self.gauge,_=deploy_skill(tables,card,self.start)
        self.charge_events=[]
        self.charge=CarnelianCharge(tables,card,self.evidence,self.gauge.state,self.start,
                                    carnelian_talent_first_tick(self.evidence,self.start),self.charge_events)
        unit=tables.read("excel/character_table.json")[CARN]
        self.trait=trait_board(unit,card["module_phase"] or {},card)
        panel=card["panel_attributes"]
        self.base_def,self.base_res,self.hp=(_raw_f32(panel[key]) for key in ("def","magicResistance","maxHp"))
        self.poll=None
        self.half=False
        self.base_taunt=unit["phases"][card["elite"]]["attributesKeyFrames"][-1]["data"]["tauntLevel"]
        self.taunt=self.base_taunt
        self.taunt_updates={}
        equipped=[part for bundle in self.evidence["equipment"].values() for part in bundle["components"]]
        polls=[buff for part in equipped if part["script"]=="PassiveBuffAbility" for buff in part["fields"]["_buffs"]
               if buff["templateKey"]=="billro_e_002[half_attribute]"]
        if polls:
            poll,=polls
            if not poll["waitFirstTriggerInterval"] or poll["firstTriggerInterval"]!=-1:
                raise ValueError("卡涅利安 X 模组的双抗检查起点已变化")
            self.poll=RecoveryTimer(_raw_f32(poll["triggerInterval"]))
        self.aura=None
        if (card["module_info"] or {}).get("typeIcon")=="plx-y":
            ability,=(p["fields"] for p in equipped if p["script"]=="AuraAbility")
            if ([b["templateKey"] for b in ability["_buffs"]]!=["billro_e_003_tr"] or
                    not ability["_removeBuffWhenTargetLeave"] or not ability["_removeBuffWhenAbilityDetached"]):
                raise ValueError("卡涅利安 Y 模组的范围接触或清理已变化")
            self.aura=LinStageAura(tables,wave,placement,card["base_range"])
        self.cast=self.mode=self.expires=self.ready=self.attack_start=self.hit=None
        self.active=False
        self.present=True

    def log(self,kind,frame,**fields):
        self.events.append(dict(kind=kind,frame=frame,operator_id=CARN,**fields))

    def defenses(self):
        defense,resistance=0,0
        if not self.active:
            defense,resistance=self.trait["def"],self.trait["magic_resistance"]
        if self.half:
            defense+=self.trait["billro_e_002[buff].def"]
            resistance+=self.trait["billro_e_002[buff].magic_resistance"]
        return dict(defense_q32=mul(self.base_def,ONE+_raw_f32(defense)),resistance_q32=self.base_res+_raw_f32(resistance))

    def leave(self):
        self.present=False
        self.cast=self.mode=self.expires=self.ready=self.attack_start=self.hit=None
        self.active=False
        self.taunt=self.base_taunt
        if self.aura:
            self.aura.detach()

    def objects(self,frame):
        if not self.present or frame<=self.start:
            return
        if self.poll and self.poll.advance(self.gauge.step) and self.half!=self.active:
            self.half=self.active
            self.log("ally_defense_update",frame,reason="module_poll",**self.defenses())
        self.charge.advance_to(frame,blocked=self.cast is not None)
        self.events.extend(dict(e,operator_id=CARN) for e in self.charge_events)
        self.charge_events.clear()
        if frame==self.expires:
            reset=execute_phalanx_event(self.tables,self.card,dict(kind="phalanx_deactivate",frame=frame,
                                                                 charged=self.cast["charged"],hp_before_q32=self.hp))
            self.hp=reset["hp_after_q32"]
            self.cast=self.mode=self.expires=self.ready=self.attack_start=self.hit=None
            self.active=False
            self.log("ally_mode_end",frame,hp_after_q32=self.hp,**self.defenses())
        if frame in self.taunt_updates:
            self.taunt=self.taunt_updates.pop(frame)
            self.log("ally_taunt_changed",frame,taunt_level=self.taunt)
        if frame==self.ready:
            targets=self.wave.search(frame,cells=self.cells,observer_born=self.start)
            if targets:
                self.attack_start=frame
                self.hit=frame+self.timing["ticks_to_key"]
                self.ready=frame+self.timing["interval_ticks"]
                self.log("ally_attack_start",frame,targets=targets)
            else:
                self.ready=frame+3

    def press(self,frame):
        sp=self.gauge.state.points
        if not self.present:
            self.log("ally_skill_button",frame,sp=sp,outcome="operator_absent")
            return
        if self.cast:
            self.log("ally_skill_button",frame,sp=sp,outcome="skill_busy")
            return
        cast=resolve_cast_start(self.tables,self.evidence,sp=sp,frame=frame,phase="after_tick",
                               facing=self.facing,direction=self.direction)
        self.log("ally_skill_button",frame,sp=sp,outcome=cast["status"])
        if cast.get("spell_frame") is None:
            return
        if cast["cast_frame"]!=frame or cast["spell_frame"]!=frame+self.end_clock["spell_frame"]:
            raise ValueError("沙缚镣锁的技能提交与动作时钟不一致")
        self.cast=execute_phalanx_event(self.tables,self.card,dict(kind="phalanx_activate",frame=frame,sp=sp,hp_before_q32=self.hp))
        self.hp=self.cast["owner_hp"]["hp_after_q32"]
        self.gauge.state.points=self.cast["sp_after"]
        self.mode,self.expires=cast["spell_frame"],frame+self.end_clock["end"]
        charged=self.cast["charged"]
        self.timing=_timing(self.tables,self.card,_active_mode(self.tables,self.card,charged))
        self.log("ally_skill_cast",frame,charged=charged,sp=self.gauge.state.points,hp_after_q32=self.hp,
                 heal_request_q32=self.cast["heal_request_q32"])
        taunt=dict(events=[dict(kind="skill_cast",frame=frame,charged=charged)],mode_frame=self.mode,
                   through_frame=self.expires,stop="skill_ended")
        carnelian_taunt(self.tables,self.card,dict(end_frame=self.end),taunt)
        self.taunt_updates={e["frame"]:e["taunt_level"] for e in taunt["events"]
                            if e["kind"]=="taunt_changed" and e["taunt_level"]!=self.base_taunt}
        if self.taunt_updates:
            self.taunt_updates[self.expires]=self.base_taunt

    def resume(self,frame):
        if self.present and self.aura and frame>=self.start:
            self.aura.advance(frame)
        if frame==self.mode:
            self.active=True
            self.ready=frame+1
            self.log("ally_mode_switch",frame,charged=self.cast["charged"],**self.defenses())

    def packets(self,frame):
        self.hit=None
        members,uncertain=self.aura.members() if self.aura else ([],[])
        event=dict(**self.layout,kind="phalanx_impact",frame=frame,targets=self.wave.snapshot(frame),
                   charged=self.cast["charged"],aura_count=len(members))
        packets=execute_phalanx_event(self.tables,self.card,event)["hits"]
        if self.aura and len(packets)>1 and any(k in members and v["hp_after_q32"]<=HP_EPSILON for k,v in packets.items()):
            ordered=self.wave.attack_order(packets,frame,after_objects=True)
            for key in ordered:
                single=dict(event,targets={key:event["targets"][key]},aura_count=len(members))
                packets[key]=execute_phalanx_event(self.tables,self.card,single)["hits"][key]
                if packets[key]["hp_after_q32"]<=HP_EPSILON and key in members:
                    members.remove(key)
            packets={key:packets[key] for key in ordered}
        self.log("ally_area_arts_damage",frame,hits=packets)
        return packets
