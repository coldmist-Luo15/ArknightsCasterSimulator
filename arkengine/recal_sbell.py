"""Shared-board snow tiles, attraction, attacks and blessing for Pramanix the Prerita S3."""
from math import floor, hypot
from types import SimpleNamespace

from .attack_range import circle_in_range
from .baked_mount import single
from .caster_first_spell import attack_key_clock
from .caster_model import _assets
from .caster_runtime import default_attack
from .damage import HP_EPSILON
from .enemy_motion import ROUTE_REACH_DISTANCE, advance_direct_move
from .fixed import ONE
from .mostima_aura import set_period
from .mystic_timing import fixed_wait_frames
from .phalanx_skills import SNOW, _aura_scale
from .sbell_blessing import run_blessing
from .sbell_snow import SnowTileContact
from .sbell_stage import SnowCaster
from .skill_recovery import READY_EPSILON, _divide
from .volley_damage import _raw_f32


def frozen(enemy, frame):
    return frame < getattr(enemy, "sbell_frozen_until", 0)


def attracted(enemy, frame):
    return frame <= getattr(enemy, "sbell_attracted_until", -1)


def resistance_delta(enemy, frame):
    return enemy.sbell_frozen_res if frozen(enemy, frame) else 0


def enemy_attack_speed_multiplier(enemy, frame):
    if frame >= getattr(enemy, "sbell_cold_until", 0):
        return 1
    base = enemy.attr("attackSpeed", 100) or 100
    return max(10,base+enemy.sbell_cold_speed)/base


def override_walk(battle, enemy, cap):
    if not attracted(enemy, battle.frame):
        if not frozen(enemy, battle.frame):
            return False
        enemy.cursor.step(battle.frame, enemy.id, cap, paused=True)
        return True
    cursor = enemy.cursor
    goal = enemy.sbell_destination
    if not frozen(enemy,battle.frame) and hypot(cursor.position[0]-goal[0],cursor.position[1]-goal[1])>ROUTE_REACH_DISTANCE:
        destination = goal
        if cursor.navigation:
            cell = tuple(floor(v+.5) for v in cursor.position)
            destination = cursor.navigation.get_map(goal)[1].get(cell,goal)
        point = advance_direct_move(cursor.position,cursor.velocity,destination,cap,cursor.steering,battle.dt,True)
        if point:
            cursor.position,cursor.velocity = point
    return True


class _Wave:
    def __init__(self, battle, actor):
        self.battle, self.actor = battle, actor
        self.cells = ()

    def snapshot(self, frame):
        return {e.id:dict(row=e.row,col=e.col,radius=e.radius,eligible=True,
                          immune_to_induction=bool(e.attr("attractImmune")),ground=not e.airborne)
                for e in self.battle.live.values() if not e.cursor.hidden}


class _SnowField:
    def __init__(self, battle):
        self.battle = battle
        self.contacts = {}

    def get_navigation(self):
        return self.battle.navigation

    def resist_ratio(self, key, frame):
        return .5 if self.battle.enemies[key].attr("statusResistance",False) else 1.

    def apply_attraction(self, key, frame, selection):
        enemy = self.battle.enemies[key]
        enemy.sbell_destination = tuple(selection["destination"])
        enemy.sbell_attracted_until = selection["expiry_frame"]
        self.battle.log("snow_attraction",target=key,goal=enemy.sbell_destination,until=enemy.sbell_attracted_until)


class SbellCombat(SnowCaster):
    def __init__(self, battle, actor):
        self.arena, self.actor = battle, actor
        if actor.uid != SNOW or actor.card["skill"] != 3:
            raise ValueError("全息群山俯首入口需要圣聆初雪三技能")
        context = SimpleNamespace(tables=battle.tables,card=actor.card,scene=battle.scene,
                                  evidence=_assets(battle.tables,actor.card))
        world = SimpleNamespace(wave=_Wave(battle,actor),events=battle.events)
        ground = set()
        exits = set()
        for r in range(battle.rows):
            for c in range(battle.cols):
                tile = battle.scene.tile(r,c)
                if tile["heightType"]=="LOWLAND" and tile["passableMask"]=="ALL" and tile["tileKey"]!="tile_hole":
                    ground.add((r,c))
                    if tile["tileKey"]=="tile_end":
                        exits.add((r,c))
        super().__init__(context,world,_SnowField(battle),SimpleNamespace(tokens={}),battle.rng,ground,exits,actor.place)
        buffs = self.tables.read("buff_table.json")
        self.frozen_res = next(m["value"] for m in buffs["c2e_freeze"]["attributes"]["attributeModifiers"]
                               if m["attributeType"]=="MAGIC_RESISTANCE")
        self.cold_speed, = [m["value"] for m in buffs["c2e_cold[debuff]"]["attributes"]["attributeModifiers"]
                            if m["attributeType"]=="ATTACK_SPEED"]
        self.attack_key = default_attack(self.tables,SNOW,self.timing["mode_index"])["fields"]["_animKey"]
        self.attack_clocks = {}
        self.cooldown = None
        self.closed = False

    def log(self, frame, kind, **fields):
        fields.setdefault("operator_id",self.actor.uid)
        self.arena.log(kind,**fields)

    def defenses(self):
        trait = {} if self.active or self.switch is not None else self.trait
        stats = self.card["panel_attributes"]
        return stats["def"]*(1+trait.get("def",0)), stats["magicResistance"]+trait.get("magic_resistance",0)

    def _until(self, target, seconds):
        seconds *= self.live.resist_ratio(target.id,self.arena.frame)
        return self.arena.frame+max(1,(_raw_f32(seconds)-READY_EPSILON)//self.step+1)

    def _freeze(self, target, until):
        if target.attr("frozenImmune",False):
            return
        target.sbell_frozen_res = self.frozen_res
        target.sbell_frozen_until = max(until,getattr(target,"sbell_frozen_until",0))
        self.log(self.arena.frame,"snow_frozen",target=target.id,until=target.sbell_frozen_until)

    def after_damage(self, source, hp_after_raw, loss_raw):
        if loss_raw<=0 or self.closed:
            return hp_after_raw
        frame = self.arena.frame
        args = dict(hp_q32=hp_after_raw,max_hp_q32=_raw_f32(self.card["panel_attributes"]["maxHp"]),
                    blessing_available=self.blessing)
        if hp_after_raw<=HP_EPSILON:
            rows = self.arena.targets(self.actor,self.wave.cells)
            result = run_blessing(self.tables,self.card,dict(args,callback="TRY_SET_HP_ZERO",undeadable=False,
                modifier_cancelled=False,selected_enemy_ids=[e.id for e in rows]))
        else:
            result = run_blessing(self.tables,self.card,dict(args,callback="ON_TAKE_DAMAGE",
                damage_source=dict(id=source.id,side="ENEMY",flags=[])))
        self.blessing = result["blessing_available"]
        if result["consumed_hp_zero"]:
            self.log(frame,"blessing_consumed",source=source.id,healed_q32=result["heal_amount_q32"])
        for app in result["buff_applications"]:
            key, seconds = app["target"], app["duration_seconds"]
            if key==SNOW:
                self.frozen_until=frame+(_raw_f32(seconds)+self.step-1)//self.step
                self.impact=self.cooldown=None
                if self.active:
                    self.next_search=self.frozen_until
                else:
                    self.cast=self.switch=None
                continue
            target=self.arena.live.get(key)
            if target is None:
                continue
            until=self._until(target,seconds)
            if app["buff"]=="c2e_cold":
                old=getattr(target,"sbell_cold_until",0)
                if old>frame and not target.attr("frozenImmune",False):
                    target.sbell_cold_until=0
                    self._freeze(target,max(until,old))
                else:
                    target.sbell_cold_until=max(old,until)
                    target.sbell_cold_speed=self.cold_speed
                    self.log(frame,"snow_cold",target=target.id,until=until)
            else:
                self._freeze(target,until)
        return result["hp_after_q32"]

    def move_multiplier(self, enemy):
        contact = self.live.contacts.get(enemy.id)
        return 1+contact.layers*self.talent["move_speed"] if contact and not self.closed else 1.

    def _aura(self):
        if (self.card["module_info"] or {}).get("typeIcon")!="plx-y":
            return ONE
        members = sum(circle_in_range(e.row,e.col,e.radius,self.wave.cells,box_query=True)
                      for e in self.arena.live.values() if not e.cursor.hidden)
        return _aura_scale(self.card,members)

    def _deal(self, targets, scale, kind):
        atk = self.arena.attack(self.actor,self.board["atk"] if self.active else 0)
        pen = _raw_f32(self.board["magic_resist_penetrate_fixed"]) if self.active else 0
        aura = self._aura()
        for e in targets:
            if e.alive:
                self.arena.arts(self.actor,e,atk,pen_raw=pen,scale_raw=_raw_f32(scale),output_raw=aura,kind=kind)

    def _contacts(self, frame, old):
        contacts = self.live.contacts
        for e in tuple(self.arena.live.values()):
            if self.closed:
                break
            if e.airborne:
                continue
            tile = None if e.cursor.hidden else (floor(e.row+.5),floor(e.col+.5))
            contact = contacts.get(e.id)
            if contact is not None:
                layers = (old if e.birth<self.actor.deployed_frame else self.snow).get(contact.tile,0)
                for tick in contact.tick(frame,{},layers,self.active,lambda:0):
                    self.log(frame,tick["kind"],target=e.id,layers=tick["stacks"])
                if contact.tile == tile:
                    continue
                contacts.pop(e.id)
                if tile is not None and hypot(tile[0]-contact.tile[0],tile[1]-contact.tile[1])<2:
                    self.snow.pop(contact.tile,None)
                self.log(frame,"snow_tile_leave",target=e.id,tile=contact.tile)
            if self.snow.get(tile,0):
                contacts[e.id]=SnowTileContact(self.tables,self.card,tile,self.snow[tile])
                self._deal([e],self.talent["talent_magic_scale"],"snow_entry_arts")
                if not e.alive:
                    contacts.pop(e.id,None)

    def _attack_clock(self):
        speed=self.arena.attack_speed(self.actor)
        if speed not in self.attack_clocks:
            stats=self.card["panel_attributes"]
            # The stage's frost ratio applies to total ASPD, including the mode's +30.
            effective=(stats["attackSpeed"]+self.board["attack_speed"])*speed/stats["attackSpeed"]
            interval=single(single(stats["baseAttackTime"]+self.board.get("base_attack_time",0))*100/single(effective))
            delay,_=attack_key_clock(self.tables,self.battle.evidence,self.cast_args["facing"],self.attack_key,interval)
            self.attack_clocks[speed]=delay,fixed_wait_frames(single(interval/self.arena.dt))
        return self.attack_clocks[speed]

    def objects(self):
        if self.closed:
            return
        frame=self.arena.frame
        if self.expiry==frame:
            self.end_mode(frame)
            self.cooldown=None
        old=self.snow.copy()
        timer=self.rapid if self.rapid is not None else self.natural
        if timer.advance(self.step):
            self.grow(frame)
        self._contacts(frame,old)
        blocked=self.active or self.cast is not None or self.switch is not None or frame<self.recovery
        rate=self.arena.sp_rate(self.actor)
        set_period(self.gauge.state,_divide(ONE,_raw_f32(rate)) if rate>0 else None,stopped=blocked)
        if not blocked:
            self.gauge.state.advance(self.gauge.step)
        if not self.active or frame<self.frozen_until:
            return
        if self.cooldown and self.next_search and self.next_search>=frame:
            prior,release=self.cooldown
            _,period=self._attack_clock()
            if prior!=period:
                remaining=self.next_search-(frame-1)
                self.next_search=max(release+1,frame-1+(remaining*period+prior-1)//prior)
                self.cooldown=period,release
        if self.next_search==frame:
            if self.arena.targets(self.actor,self.skill):
                delay,period=self._attack_clock()
                self.impact=frame+delay
                self.next_search=frame+period
                self.cooldown=period,self.impact
            else:
                self.next_search=frame+3
                self.cooldown=None

    def coroutines(self):
        if self.closed or self.arena.frame<self.frozen_until:
            return
        frame=self.arena.frame
        if self.cast and self.cast["cast_frame"]==frame:
            self.commit(frame)
        if self.switch==frame:
            self.switch_mode(frame)
        if self.impact==frame:
            rows=self.arena.targets(self.actor,self.skill)
            self._deal(rows,self.board["attack@atk_scale_s3"],"sbell_area_arts")
            self.impact=None

    def commit(self,frame):
        super().commit(frame)
        self.arena.skill_started(self.actor,frame)

    def press(self):
        if self.closed:
            return False
        before=self.gauge.state.points
        super().press(self.arena.frame,"after_tick")
        return self.cast is not None or self.gauge.state.points<before

    def close(self):
        self.closed=True
        self.active=False
        self.alive=False
        self.snow.clear()
        self.live.contacts.clear()
