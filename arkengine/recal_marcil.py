"""Marcille's S3 chant, mana card, regular splash and high-ground shards on the shared board."""
from collections import defaultdict
from math import ceil, hypot

from .attack_range import range_cells
from .caster_first_spell import attack_key_clock
from .caster_model import _assets
from .caster_start import resolve_cast_start
from .fixed import ONE
from .marcil_blast import blast_geometry
from .marcil_deploy import _birth, companion_deck_buff
from .marcil_mana import entry_mana
from .marcil_toggle import _attack_groups
from .module_effects import active_board, equipped_phase, talent_rows
from .mostima_aura import set_period
from .mystic_timing import fixed_wait_frames
from .skill_recovery import NaturalSkillPoints, RecoveryTimer, _divide
from .volley_damage import _raw_f32


class MarcilleCombat:
    def __init__(self,battle,actor):
        self.b,self.a=battle,actor
        self.card=actor.card
        t=battle.tables
        self.ev=_assets(t,self.card)
        self.face="Back" if actor.direction=="UP" else "Front"
        high=[(r,c) for r in range(battle.rows) for c in range(battle.cols) if battle.scene.tile(r,c)["heightType"]=="HIGHLAND"]
        _,self.blast,_,self.shards=blast_geometry(t,self.card,self.ev,dict(placement=actor.place,rows=battle.rows,cols=battle.cols,high_tiles=high))
        self.cells=range_cells(self.card["base_range"],actor.place,battle.rows,battle.cols)
        self.groups=_attack_groups(self.ev)["Default"]
        self.board={b["key"]:b["value"] for b in self.card["skill_row"]["blackboard"]}
        rows=[r for r in self.card["base_talents"] if r["prefabKey"]=="1"]
        self.talent=active_board(talent_rows(equipped_phase(t,self.card),0,"1",rows),self.card["elite"],self.card["level"],self.card["potential"])
        if not hasattr(battle,"mana_history"):
            battle.mana_history=[]
        self.mana=entry_mana(t,self.card,self.ev,dict(frame=battle.frame,card_events=battle.mana_history),None)[0]
        battle.mana_history.append(dict(kind="deploy",frame=battle.frame))
        self.bonus=companion_deck_buff(t,self.card,list(battle.cards.values()))["affected"].get(actor.uid,{})
        self.recovery=NaturalSkillPoints(self.mana,int(self.board["skill_cost_min_sp"]),int(self.board["mana_max"]),None)
        self.power=RecoveryTimer(_raw_f32(self.talent["interval"]))
        self.power.remaining=0
        self.checked=self.mana
        self.born=battle.frame+_birth(t,self.card,self.ev,self.face)["born_exit_update"]
        self.poll=self.born+1
        self.pending=self.chant=self.resume=None
        self.events=defaultdict(list)
        self.closed=False
        self.timing={}
        battle.log("marcil_entry",operator_id=actor.uid,mana=self.mana)

    def defenses(self):
        p=self.card["panel_attributes"]
        return p["def"]*(1+self.bonus.get("defense_multiplier_delta_q32",0)/ONE),p["magicResistance"]

    def attack(self):
        return self.b.attack(self.a,self.talent["atk"] if self.checked>=self.talent["sp"] else 0)

    def objects(self):
        if self.closed:
            return
        f=self.b.frame
        rate=self.b.sp_rate(self.a)-1
        stopped=self.chant and self.chant["cast_frame"]+2<=f<self.resume
        self.recovery.points=self.mana
        set_period(self.recovery,_divide(ONE,_raw_f32(rate)) if rate>0 else None,stopped=bool(stopped))
        if self.recovery.advance(_raw_f32(self.b.dt),stopped=bool(stopped)):
            self.mana=self.recovery.points
        self.sample=self.power.advance(_raw_f32(self.b.dt))
        if self.sample:
            self.checked=self.mana
        if f==self.resume:
            self.chant=None
            self.poll=f
        if not self.chant and f==self.poll:
            targets=self.b.targets(self.a,self.cells)
            if not targets:
                self.poll+=3
                return
            speed=self.b.attack_speed(self.a)+self.bonus.get("attack_speed_add_q32",0)/ONE
            if speed not in self.timing:
                iv=self.card["panel_attributes"]["baseAttackTime"]*100/speed
                lag,_=attack_key_clock(self.b.tables,self.ev,self.face,"Attack",iv)
                self.timing[speed]=lag,fixed_wait_frames(iv/self.b.dt)
            lag,period=self.timing[speed]
            self.pending=f+lag,targets[0],(targets[0].row,targets[0].col),self.groups["Power" if self.checked>=self.talent["sp"] else "Normal"]
            self.poll=f+period

    def coroutines(self):
        if self.closed:
            return
        f=self.b.frame
        if self.pending and self.pending[0]==f:
            _,t,point,radius=self.pending
            self.pending=None
            row,col=(t.row,t.col) if t.alive else point
            for e in tuple(self.b.live.values()):
                if not e.cursor.hidden and hypot(e.row-row,e.col-col)<=radius+e.radius:
                    self.b.arts(self.a,e,self.attack(),kind="marcil_ordinary_hit")
        for event in self.events.pop(f,()):
            kind=event["kind"]
            if kind=="spend_mana":
                self.mana=max(0,self.mana-event["cost"])
                if self.sample and event["phase"]=="OBJECTS":
                    self.checked=self.mana
            elif kind=="explosion_reach":
                for e in self.b.targets(self.a,self.blast):
                    self.b.arts(self.a,e,self.attack(),scale_raw=_raw_f32(self.board["atk_scale"]),kind="marcil_blast")
            elif kind=="high_tile_shards":
                for e in self.b.targets(self.a,self.shards):
                    if not e.attr("stunImmune"):
                        ticks=ceil(self.board["stun"]*(.5 if e.attr("statusResistance") else 1)/self.b.dt)
                        e.stun_until=max(e.stun_until,f+ticks)
                        self.b.log("shard_stun",target=e.id,until=e.stun_until)

    def press(self):
        if self.chant or self.closed:
            return
        f=self.b.frame
        next_press=next((n for n in sorted(self.b.actions) if n>f and any(a["operator_id"]==self.a.uid and
            a.get("kind") in ("skill","chant_stop") for a in self.b.actions[n])),None)
        options={"repress_frame":next_press} if next_press is not None else {}
        attempt=resolve_cast_start(self.b.tables,self.ev,frame=f,phase="after_tick",sp=self.mana,facing=self.face,
            direction=self.a.direction,owner_state="born" if f<self.born else "idle",chant_options=options)
        if attempt["cast_frame"] is None:
            self.b.log("skill_rejected",operator_id=self.a.uid,reason=attempt["status"])
            return
        self.chant=attempt
        self.b.skill_started(self.a,attempt["cast_frame"])
        self.resume=attempt["blast_clock"]["first_unblocked_recovery_tick"]
        self.pending=None
        for e in attempt["events"]:
            self.events[e["frame"]].append(e)
        self.b.log("chant_start",operator_id=self.a.uid,mana=self.mana,cast_frame=attempt["cast_frame"])

    def close(self):
        self.closed=True
        self.b.mana_history.append(dict(kind="finish",frame=self.b.frame,mana=self.mana))
