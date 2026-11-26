"""Carnelian's charged and ordinary modes on the shared stage timeline."""
from .attack_range import circle_in_range
from .caster_cast import run_committed_cast
from .caster_ranges import skill_range_cells
from .caster_start import resolve_cast_start
from .carnelian_defense import s1_defense
from .fixed import ONE, mul
from .lin_output_aura import LinStageAura
from .module_effects import trait_board
from .phalanx_deployment import CarnelianCharge, carnelian_talent_first_tick, carnelian_taunt
from .phalanx_mode_scene import _active_mode, _timing
from .phalanx_skills import CARN, _aura_scale, _carnelian_ramp, execute_phalanx_event
from .ranged_operator_battle import RangedOperatorBattle
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


class CarnelianBattle(RangedOperatorBattle):
    uid=CARN

    def __init__(self,tables,request,releases):
        super().__init__(tables,request,releases)
        if self.world.incoming:
            self.world.incoming.live_projectiles=True
        self.unit=tables.read("excel/character_table.json")[CARN]
        self.trait=trait_board(self.unit,self.card["module_phase"] or {},self.card)
        self.base_taunt=self.unit["phases"][self.card["elite"]]["attributesKeyFrames"][-1]["data"]["tauntLevel"]
        self.sluggish=tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"][0]["value"]
        self.controls={}
        self.control_samples={}
        self.marks={}
        self.mode=self.expiry=self.hit_frame=None
        self.last_mode=self.last_charged=None
        self.taunts={}
        self.taunt_updates={}
        self.aura=None
        mods=[b["triggerInterval"] for v in self.ev["equipment"].values() for c in v["components"]
              if c["script"]=="PassiveBuffAbility" for b in c["fields"]["_buffs"] if b["templateKey"]=="billro_e_002[half_attribute]"]
        self.poll_seconds=mods[0] if mods else None

    def set_hp(self,hp,maximum):
        self.world.hp=hp
        unit=self.units[self.uid]
        unit.update(hp=hp,maximum=maximum)
        if self.world.incoming:
            self.world.incoming.units[self.uid]["hp"]=hp

    def deploy(self,frame,place):
        super().deploy(frame,place)
        self.charge=CarnelianCharge(self.tables,self.card,self.ev,self.sp,frame,carnelian_talent_first_tick(self.ev,frame),self.events)
        self.clock=run_committed_cast(self.tables,self.ev,horizon=0,facing=self.view,direction=place["direction"])["natural_end"]
        self.layout=dict(placement=self.placement,rows=self.scene["rows"],cols=self.scene["cols"])
        self.skill_cells=skill_range_cells(self.tables,self.card,self.layout)["cells"]
        self.resume=frame+1
        self.half=False
        self.poll=RecoveryTimer(_raw_f32(self.poll_seconds)) if self.poll_seconds else None
        self.taunts[self.uid]=self.base_taunt
        self.aura=LinStageAura(self.tables,self.world.wave,place,self.card["base_range"]) if (self.card["module_info"] or {}).get("typeIcon")=="plx-y" else None
        base=_raw_f32(self.panel["maxHp"])
        self.set_hp(self.request.get("hp_before_q32",base) if frame==self.first["frame"] else base,base)
        self.defenses(False)
        if self.slot==1:
            self.log("owner_defenses",frame,profiles=s1_defense(self.tables,self.card,active=False,charged=False))

    def clear_owner(self):
        super().clear_owner()
        self.marks.clear()
        self.mode=self.expiry=self.hit_frame=None
        self.taunt_updates.clear()
        self.taunts.pop(self.uid,None)
        self.aura=None

    def defenses(self,active):
        idle=not active or self.slot==1 and self.charged
        bonus=self.trait["def"] if idle else 0
        res=self.trait["magic_resistance"] if idle else 0
        if self.slot==1 and active:
            bonus+=self.bb["def"]
        if self.half:
            bonus+=self.trait["billro_e_002[buff].def"]
            res+=self.trait["billro_e_002[buff].magic_resistance"]
        w=self.world
        defense=mul(_raw_f32(self.panel["def"]),ONE+_raw_f32(bonus))
        w.receiver.defenses[self.uid]=defense
        w.receiver.resistances[self.uid]=_raw_f32(self.panel["magicResistance"])+_raw_f32(res)
        if w.incoming and self.uid in w.incoming.units:
            w.incoming.units[self.uid]["defense"]=defense

    def before_incoming(self,frame):
        if not self.world.present or frame==self.generation:
            return
        active=self.mode is not None and self.mode<frame<=self.expiry
        if self.poll and self.poll.advance(self.step):
            self.half=active and not (self.slot==1 and self.charged)
        self.defenses(active)
        if frame in self.taunt_updates:
            value=self.taunts[self.uid]=self.taunt_updates.pop(frame)
            self.log("taunt_changed",frame,taunt_level=value)

    def movement(self,key,frame):
        root,slow=self.controls.get(key,(-1,-1))
        return frame<=root,frame<=slow,self.sluggish

    def button(self,frame,action):
        points=self.sp.points
        if self.mode is not None or points<self.sp.cost:
            self.log("skill_button",frame,sp=points,outcome="skill_active" if self.mode is not None else "insufficient_sp")
            return
        cast=resolve_cast_start(self.tables,self.ev,sp=points,frame=frame,phase=action.get("phase","after_tick"),facing=self.view,
                                direction=self.placement["direction"])
        self.log("skill_button",frame,sp=points,outcome=cast["status"])
        if cast["spell_frame"] is None:
            return
        options={k:self.request[k] for k in ("heal_free","outgoing_heal_scales_q32","incoming_heal_scales_q32") if k in self.request}
        result=execute_phalanx_event(self.tables,self.card,dict(kind="phalanx_activate",frame=frame,sp=points,
                                                               hp_before_q32=self.world.hp,**options))
        self.charged=self.last_charged=result["charged"]
        self.sp.points=result["sp_after"]
        self.set_hp(result["owner_hp"]["hp_after_q32"],result["skill_max_hp_q32"])
        self.mode=self.last_mode=cast["spell_frame"]
        self.expiry=cast["cast_frame"]+self.clock["end"]
        self.resume=cast["cast_frame"]+self.clock["first_recovery_tick"]
        self.timing=_timing(self.tables,self.card,_active_mode(self.tables,self.card,self.charged))
        self.events.extend(cast["events"])
        entry=dict(kind="skill_cast",frame=frame,charged=self.charged,sp=self.sp.points,owner_hp=result["owner_hp"],
                   heal_request_q32=result["heal_request_q32"],heal_after_modifiers_q32=result["heal_after_modifiers_q32"])
        self.events.append(entry)
        report=dict(events=[entry],mode_frame=self.mode,through_frame=self.expiry,stop="skill_ended")
        carnelian_taunt(self.tables,self.card,dict(end_frame=self.expiry),report)
        self.taunt_updates={e["frame"]:e["taunt_level"] for e in report["events"] if e["kind"]=="taunt_changed"}

    def tick(self,frame):
        if not self.world.present or frame==self.generation:
            return
        self.charge.advance_to(frame,blocked=frame<self.resume)
        if frame==self.expiry:
            out=execute_phalanx_event(self.tables,self.card,dict(kind="phalanx_deactivate",frame=frame,
                                       charged=self.charged,hp_before_q32=self.world.hp))
            self.set_hp(out["hp_after_q32"],_raw_f32(self.panel["maxHp"]))
            self.log("mode_end",frame,charged=self.charged,owner_hp=out,cleared_marks=dict(self.marks))
            self.marks.clear()
            self.mode=self.expiry=self.hit_frame=None
            if self.aura:
                self.aura.set_range(self.card["base_range"])
            if self.slot==1:
                self.log("owner_defenses",frame,profiles=s1_defense(self.tables,self.card,active=False,charged=False))
        if self.mode is None or frame<self.mode:
            return
        if frame==self.mode:
            self.log("mode_switch",frame,charged=self.charged)
            if self.slot==1:
                self.log("owner_defenses",frame,profiles=s1_defense(self.tables,self.card,active=True,charged=self.charged))
            self.next_attack=frame+1
            if self.aura:
                self.aura.set_range(self.card["skill_range"] or self.card["base_range"])
        if frame>=self.next_attack:
            rows=self.pool(frame,self.skill_cells)
            if rows:
                self.hit_frame=frame+self.timing["ticks_to_key"]
                self.next_attack=frame+self.timing["interval_ticks"]
                self.log("attack_start",frame,targets=[r["id"] for r in rows])
            else:
                self.next_attack=frame+3

    def after_frame(self,frame):
        for key,(origin,end,root) in tuple(self.control_samples.items()):
            if frame in (origin+1,end,end+1) and self.alive(key) and key in self.world.positions[frame]:
                flags={"movement_suppressed" if root else "sluggish_active":frame<=end}
                self.log("root_motion" if root else "sluggish_motion",frame,enemy=key,position=self.world.positions[frame][key],**flags)
            if frame>=end+1 or not self.alive(key):
                self.control_samples.pop(key)
        if not self.world.present:
            return
        if self.aura:
            for k,state in self.states.items():
                self.world.wave.attrs[k]["hp_q32"]=state.hp
            self.aura.advance(frame)
        if self.hit_frame!=frame:
            return
        self.hit_frame=None
        members=self.aura.members()[0] if self.aura else []
        aura_scale=_aura_scale(self.card,len(members))
        bonus=_raw_f32(self.bb.get("atk",0)) if self.slot!=2 or self.charged else 0
        ramp=None
        if self.slot==3:
            ramp=_carnelian_ramp(self.tables,self.card,frame-self.mode,self.bb["atk"])
            bonus=ramp["bonus_q32"]
        power=mul(_raw_f32(self.panel["atk"]),ONE+bonus)
        hits={}
        for row in self.world.snapshot(frame,projectile_born=frame):
            key=row["id"]
            if not self.alive(key) or not circle_in_range(row["row"],row["col"],self.radii[key],self.skill_cells,box_query=True):
                continue
            state=self.states[key]
            scale=ONE
            if self.slot==3 and self.charged:
                stacks=self.marks[key]=min(5,self.marks.get(key,0)+1)
                scale=ONE+stacks*_raw_f32(self.bb["attack@damage_scale"])
            hit=state.receive_ordinary(frame,source=self.uid,damage_type="arts",attack_q32=power,
                mitigation_q32=_raw_f32(row["res"])+state.res_add,output_scale_q32=aura_scale,received_scale_q32=scale)
            packet=hits[key]=dict(damage_q32=hit["damage_q32"],hp_after_q32=state.hp,status_requests={})
            if self.slot==3 and self.charged:
                packet.update(marks_after=stacks,marks_by_source={self.uid:stacks})
            if self.slot==2 and self.alive(key):
                root=self.charged
                seconds=self.bb["attack@root" if root else "attack@sluggish"]
                ticks=(_raw_f32(seconds*self.resistance(key,frame))+self.step-1)//self.step
                oldroot,oldslow=self.controls.get(key,(-1,-1))
                if not root or not row.get("root_immune",False):
                    self.controls[key]=(max(oldroot,frame+ticks),oldslow) if root else (oldroot,max(oldslow,frame+ticks))
                    self.control(key,frame,self.sluggish)
                    self.control_samples[key]=(frame,frame+ticks,root)
                    packet["root_start_position" if root else "sluggish_start_position"]=self.world.positions[frame][key]
                    packet["status_requests"]["root_seconds" if root else "sluggish_seconds"]=seconds
                    packet["movement_resumes_frame" if root else "ordinary_speed_resumes_frame"]=frame+ticks+1
            self.log("area_target_hit",frame,target=key,charged=self.charged,**packet)
            if not self.alive(key):
                self.marks.pop(key,None)
        details={}
        if self.slot==1:
            details["owner_defenses"]=s1_defense(self.tables,self.card,active=True,charged=self.charged,mode_age=frame-self.mode)
        self.log("area_arts_damage",frame,hits=hits,ramp=ramp,aura_members=members,**details)

    def run(self):
        result=super().run()
        result.update(charged=self.last_charged,mode_frame=self.last_mode,owner_hp_q32=self.world.hp,defeated=list(self.world.receiver.deaths),
                      taunt_level=self.taunts.get(self.uid,self.base_taunt))
        return result
