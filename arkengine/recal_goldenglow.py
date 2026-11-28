"""Goldenglow S3. Resolve attached funnels and delayed explosions on the shared H5-1 clock."""
from .attack_range import range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_drone_chain_mystic import GG, _drone
from .caster_model import _assets
from .caster_start import resolve_cast_start
from .elemental_casters import _board
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .eyja_ignition import ignition_circle_overlap
from .fixed import ONE, mul
from .goldenglow_stage import remote_funnel_profiles
from .marcil_deploy import _birth
from .module_effects import trait_board
from .mostima_aura import set_period
from .mystic_timing import fixed_wait_frames
from .skill_recovery import _divide, deploy_skill
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


class GoldenglowCombat:
    def __init__(self, battle, actor):
        self.battle, self.actor = battle, actor
        self.card = actor.card
        self.stats = self.card["panel_attributes"]
        self.ev = _assets(battle.tables, self.card)
        self.board = {r["key"]:r["value"] for r in self.ev["skill_blackboard"]}
        self.talents = [_board(self.card, i, str(i+1)) for i in (0, 1)]
        character = battle.tables.read("excel/character_table.json")[GG]
        self.trait = trait_board(character, self.card["module_phase"], self.card)
        self.pen = _raw_f32(self.talents[1]["magic_resist_penetrate_fixed"])
        rule, = battle.tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"]
        self.slow = -rule["value"]
        self.slow_frames = fixed_wait_frames(single(self.board["attack@sluggish"]/battle.dt))
        self.step = _raw_f32(battle.dt)
        parts = {p["id"]:p for p in self.ev["character"]["components"] if p["active"] and p["enabled"]}
        self.normal, = (p["fields"] for p in parts.values() if p["script"]=="RangedAttack"
                        and p["path"]=="Modes/Default/Body/Attack")
        mode, = (p["fields"] for p in parts.values() if p["script"]=="MultiFunnelRemoteAttack"
                 and p["path"].startswith("Modes/S3/"))
        if mode["_alwaysUseFunnelSelector"] or not mode["_alwaysIncludeTarget"]:
            raise ValueError("澄闪浮游单元的派发选择器已改变")
        self.drone_keys, self.explosions = remote_funnel_profiles(self.ev, parts, mode, self.board, battle.dt)
        mover, = (p["fields"] for p in self.ev["projectiles"][self.normal["_projectileKey"]]["logic"]["components"]
                  if p["script"]=="AdvancedMovement")
        if mover["_moveType"] != 1 or mover["_delayToStart"]:
            raise ValueError("澄闪普通弹道已改变")
        self.projectile_speed = mover["_speed"]
        self.cells = range_cells(self.card["base_range"], actor.place, battle.rows, battle.cols)
        self.view = "Back" if actor.direction=="UP" else "Front"
        self.sign = -1 if actor.direction=="LEFT" else 1
        self.clock = run_committed_cast(battle.tables, self.ev, horizon=0, facing=self.view,
                                        direction=actor.direction)["natural_end"]
        self.gauge, _ = deploy_skill(battle.tables, self.card, battle.frame)
        self.born_end = battle.frame + _birth(battle.tables, self.card, self.ev, self.view)["born_exit_update"]
        self.poll = self.born_end + 1
        self.unblock = battle.frame+1
        self.mode = self.restore = self.commit = self.pending = self.cooldown = None
        self.explosion_stacks = dict.fromkeys(self.drone_keys, 1)
        self.units, self.shots, self.blasts = [], [], []
        self.previous = None
        self.hits = 0
        self.closed = self.active = False
        self.mount_notes = set()

    def log(self, kind, **fields):
        self.battle.log(kind, operator_id=self.actor.uid, **fields)

    def enemy_row(self, enemy):
        def note(rule):
            if (enemy.id, rule) not in self.mount_notes:
                self.mount_notes.add((enemy.id,rule))
                self.log("rule_applied",target=enemy.id,rule=rule)
        return dict(id=enemy.id, row=enemy.row, col=enemy.col, prefab=enemy.prefab, airborne=enemy.airborne,
                    tile_z=0, hp=enemy.hp/ONE, res=enemy.res, receiver_mount_rule=note)

    def interval(self):
        return _divide(_raw_f32(self.stats["baseAttackTime"]),
                       _divide(_raw_f32(self.battle.attack_speed(self.actor)),100*ONE))

    def search(self):
        b = self.battle
        if self.active:
            candidates = [e for e in b.live.values() if not e.cursor.hidden]
            target = min(candidates, key=lambda e:e.priority(), default=None)
        else:
            target = next(iter(b.targets(self.actor,self.cells)),None)
        if target is None:
            self.poll = b.frame+3
            self.cooldown = None
            return
        interval = self.interval()/ONE
        if target.col != self.actor.col:
            self.sign = 1 if target.col>self.actor.col else -1
        trace = (dict(release_frame=b.frame) if self.active else
                 ranged_shot_release(b.tables,self.ev,self.normal,attack_frame=b.frame,attack_interval=interval,
                                     facing=self.view,mode_path="Modes/Default"))
        self.pending = target, self.active, trace
        period = fixed_wait_frames(single(interval/b.dt))
        self.cooldown = period, trace["release_frame"]
        self.poll = b.frame+period

    def launch(self):
        target, boosted, trace = self.pending
        self.pending = None
        if not target.alive or target.cursor.hidden:
            return
        b = self.battle
        if boosted:
            for unit in self.units:
                if unit["target"] is None and b.frame>=unit["rearm"]:
                    unit.update(target=target.id,first_tick=b.frame+1,born=b.frame,remaining=0,
                                previous_target=None,prior_same_target_hits=0)
                    self.log("gg_drone_release",unit=unit["projectile"],target=target.id)
            return
        self.hits = self.hits+1 if self.previous==target.id else 0
        self.previous = target.id
        scale = min(_raw_f32(self.trait["max_atk_scale"]),
                    _raw_f32(self.trait["init_atk_scale"])+self.hits*_raw_f32(self.trait["delta_atk_scale"]))
        row = self.enemy_row(target)
        for kind, scale_raw, mount, field in (("gg_ordinary_hit",ONE,2,"muzzlaTransform"),
                                              ("gg_ordinary_funnel_hit",scale,6,"specialPoint1")):
            origin, hit = stationary_cast_mounts(b.tables,GG,trace["clock"],self.actor.place,row,self.view,
                                                  mount=mount,field=field,caster_face_sign=self.sign)
            self.shots.append((target,origin,(hit[0]-target.col,hit[1]-target.row,hit[2]),scale_raw,kind))

    def hit_remote(self, unit, target):
        b = self.battle
        key = unit["projectile"]
        period = self.interval()-self.step
        unit["remaining"] = period
        state = dict(unit, explosion_stack=self.explosion_stacks[key],target=self.enemy_row(target),explosion_targets=[])
        packet = _drone(GG,3,"drone_hit",state,self.board,self.stats,self.trait,self.talents,stream=b.rng)
        self.explosion_stacks[key] = packet["explosion_stack"]
        attack = b.attack(self.actor,self.board["atk"])
        if packet["exploded"]:
            delay, radius = self.explosions[key]
            self.blasts.append((b.frame+delay,dict(row=target.row,col=target.col),radius,
                                mul(attack,_raw_f32(self.talents[0]["attack@atk_scale_2"]))))
            unit.update(target=None,rearm=b.frame+fixed_wait_frames(single((period-self.step)/ONE/b.dt)))
            self.log("gg_explosion_emitted",unit=key,target=target.id,impact_frame=b.frame+delay)
            return
        unit.update(previous_target=packet["previous_target"],prior_same_target_hits=packet["prior_same_target_hits"])
        b.arts(self.actor,target,attack,self.pen,_raw_f32(packet["damage_scale"]),kind="gg_drone_hit")
        if target.alive:
            b.slow(target,self.slow,b.frame+self.slow_frames+1)

    def objects(self):
        if self.closed:
            return
        f = self.battle.frame
        if self.commit is not None and self.commit[0]==f:
            self.gauge.state.points = self.commit[1]
            self.commit = None
        if f==self.mode:
            self.active = True
            self.units = [dict(projectile=key,target=None,rearm=f) for key in self.drone_keys]
            self.poll = f+1
            self.log("gg_global_mode",funnels=len(self.units))
        if f==self.restore:
            self.active = False
            self.units.clear()
            self.mode = self.restore = self.pending = self.cooldown = None
            self.poll = f+1
        rate = self.battle.sp_rate(self.actor)
        set_period(self.gauge.state,_divide(ONE,_raw_f32(rate)) if rate>0 else None,stopped=f<self.unblock)
        if f>=self.unblock:
            self.gauge.state.advance(self.step)
        if self.cooldown and self.poll>=f:
            old, release = self.cooldown
            period = fixed_wait_frames(single(self.interval()/ONE/self.battle.dt))
            if old!=period:
                self.poll = max(release+1,f-1+((self.poll-f+1)*period+old-1)//old)
                self.cooldown = period,release
        if f==self.poll:
            self.search()

    def coroutines(self):
        b = self.battle
        remaining = []
        for target, position, offset, scale, kind in self.shots:
            if not target.alive or target.cursor.hidden:
                continue
            goal = target.col+offset[0],target.row+offset[1],offset[2]
            step = ordinary_trace_step(position,goal,effective_dt=b.dt,round_position=True,speed=self.projectile_speed)
            if step.reached:
                b.arts(self.actor,target,b.attack(self.actor,self.board["atk"] if self.active else 0),
                       self.pen,scale,kind=kind)
            else:
                remaining.append((target,step.position,offset,scale,kind))
        self.shots = remaining
        for unit in self.units[:]:
            if self.closed:
                break
            if unit["target"] is None or b.frame<unit["first_tick"]:
                continue
            target = b.live.get(unit["target"])
            if target is None or target.cursor.hidden:
                unit.update(target=None,rearm=b.frame+1)
                continue
            unit["remaining"] -= self.step
            if unit["remaining"]<=0:
                self.hit_remote(unit,target)
        while self.blasts and self.blasts[0][0]==b.frame:
            _, center, radius, attack = self.blasts.pop(0)
            for target in tuple(b.live.values()):
                if not target.cursor.hidden and ignition_circle_overlap(center,dict(row=target.row,col=target.col),radius,target.radius):
                    b.arts(self.actor,target,attack,self.pen,kind="gg_explosion_hit")
                    if target.alive:
                        b.slow(target,self.slow,b.frame+self.slow_frames+1)
        if not self.closed and self.pending and self.pending[2]["release_frame"]==b.frame:
            self.launch()

    def press(self):
        b = self.battle
        if self.closed or self.mode is not None or b.frame<self.unblock:
            self.log("skill_button_rejected",reason="inactive_or_busy")
            return False
        cast = resolve_cast_start(b.tables,self.ev,frame=b.frame,phase="after_tick",facing=self.view,
                                  direction=self.actor.direction,trigger="MANUAL",sp=self.gauge.state.points)
        if cast["spell_frame"] is None:
            self.log("skill_button_rejected",reason=cast["status"],sp=self.gauge.state.points)
            return False
        f = cast["cast_frame"]
        if f==b.frame:
            self.gauge.state.points=cast["sp_after"]
        else:
            self.commit=f,cast["sp_after"]
        self.mode=cast["spell_frame"]
        self.restore=f+self.clock["end"]+int(self.clock["end_phase"]=="after_fixed")
        self.unblock=f+self.clock["first_recovery_tick"]
        self.pending=self.cooldown=None
        self.poll=self.mode+1
        b.skill_started(self.actor,f)
        self.log("skill_pressed",skill=3,mode_frame=self.mode,end_frame=self.restore)
        return True

    def close(self):
        self.closed=True
        self.active=False
        self.units.clear()
        self.pending=self.commit=None
