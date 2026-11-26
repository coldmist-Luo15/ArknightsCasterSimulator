"""Necrass's death listeners, owned tokens and three cast paths on the stage clock."""
from collections import defaultdict, deque

from .attack_range import circle_in_range
from .caster_cast import _after_spell_clock, run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_start import _begin_clip, resolve_cast_start
from .damage import HP_EPSILON
from .elemental_casters import NECRAS, _board
from .elemental_projectiles import ranged_shot_release, stable_enemy_mount
from .fixed import ONE, mul
from .necras_servants import Servants, tile_of
from .ordinary_receiver_buffs import passive_buffs
from .ranged_operator_battle import RangedOperatorBattle
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


class NecrasBattle(RangedOperatorBattle):
    uid = NECRAS

    def __init__(self, tables, request, releases):
        request = dict(request, actions=[dict(a, operator_id=a.get("operator_id", self.uid))
                        if a.get("kind") == "servant_withdraw" else a for a in request["actions"]])
        super().__init__(tables, request, releases)
        self.servants = Servants(self)
        self.module = _trait_values(self.card)
        self.low_hp = _board(self.card, 1, "2")
        self.base_atk = _raw_f32(self.panel["atk"])
        self.interval = self.panel["baseAttackTime"]*100/self.panel["attackSpeed"]
        self.attacks = {c["path"].split("/")[1]: c["fields"] for c in self.ev["character"]["components"]
                        if c["script"] == "RangedAttack"}
        self.dead = set()
        self.spawn_queue = deque()
        self.tile_queue = deque()
        self.links = {}
        self.sleeping = set()
        self.world.sleeping = self.sleeping
        self.entity_order = [("predefined", i) for i in range(len(self.world.scene.predefines))]
        self.recycle_delay = self.ticks(1)  # V079 Character and Enemy constructor, m_delayToRecycle.
        self.recycle_at = defaultdict(list)
        self.birth_order = defaultdict(list)
        for key, birth in sorted(self.world.births.items(), key=lambda item: item[1]["order"]):
            self.birth_order[birth["frame"]].append(key)
        self.escapes_seen = 0
        self.block_free = set()
        self.sleep_immune = set()
        for key, row in self.world.stats.items():
            for buff in passive_buffs(tables, "enemies", row["prefab"]):
                attrs = buff.get("attributes") or {}
                if 3 in (attrs.get("abnormalFlags") or ()):
                    self.block_free.add(key)
                if 0 in (attrs.get("abnormalComboImmunes") or ()):
                    self.sleep_immune.add(key)
        self.buff_end = -1
        self.cast = None
        self.dragon_delays = []
        self.attached_shots = []
        self.controls = request.get("operator_status_effects", ())
        if self.slot == 2:
            self.lasso = next(c["fields"] for c in self.ev["skill"]["components"] if c["script"] == "RangedAttack")
            parts = self.ev["projectiles"][self.lasso["_projectileKey"]]["logic"]["components"]
            self.lasso_speed = next(c["fields"]["_speed"] for c in parts if c["script"] == "HarpoonMovement")
        self.spawn_delay = self.ticks(next(v["value"] for v in
            self.ev["templates"]["necras_t_1"]["eventToActions"]["ON_OWNER_KILLED"][1]["_succeedNodes"][1]
            ["_buff"]["blackboard"] if v["key"] == "interval"))

    def deploy(self, frame, place):
        super().deploy(frame, place)
        self.resume = frame+1
        self.buff_end = -1

    def sync_cards(self, frame):
        self.frame = frame
        self.servants.update_tiles(frame)
        for key in self.recycle_at.pop(frame, ()):
            # UnitManager.Unregister uses swap-back. FetchTokenOrHost visits this list in slot order.
            at = self.entity_order.index(key)
            self.entity_order[at] = self.entity_order[-1]
            self.entity_order.pop()
        old = {k: u["placement"]["frame"] for k, u in self.units.items() if not u.get("summoner")}
        super().sync_cards(frame)
        for uid, born in old.items():
            if uid not in self.units:
                self.recycle_at[frame+self.recycle_delay].append((uid, born))
        self.entity_order.extend((uid, frame) for uid in self.orders.placed if uid not in old)
        self.entity_order.extend(self.birth_order.get(frame, ()))
        for escaped in self.world.escaped[self.escapes_seen:]:
            self.recycle_at[escaped["frame"]+self.recycle_delay].append(escaped["instance"])
        self.escapes_seen = len(self.world.escaped)

    def clear_owner(self):
        super().clear_owner()
        self.cast = None
        for key in list(self.links):
            self.unlink(self.frame, key)
        self.spawn_queue.clear()
        self.tile_queue.clear()
        self.dragon_delays.clear()
        self.buff_end = -1

    def cleanup(self, frame):
        for uid, t in list(self.servants.live.items()):
            if not self.world.present or t["hp"] <= HP_EPSILON:
                self.servants.remove(frame, uid, "host_left" if not self.world.present else "defeated")

    def withdraw(self, frame):
        super().withdraw(frame)
        self.cleanup(frame)

    def player_action(self, frame, action):
        if action.get("kind") != "servant_withdraw":
            return super().player_action(frame, action)
        uid = action.get("servant")
        if uid is None:
            cell = action["row"], action["col"]
            uid = next((k for k in self.servants.live if self.blockers[k].manager.position == cell), None)
        if uid in self.servants.live:
            self.servants.remove(frame, uid, "manual")
        else:
            self.log("servant_withdraw_missed", frame, servant=uid, row=action.get("row"), col=action.get("col"))

    def update_walks(self, frame):
        self.cleanup(frame)
        self.collect_deaths(frame)
        super().update_walks(frame)
        self.cleanup(frame)
        self.sync_block_flags(frame)

    def sync_block_flags(self, frame):
        if self.world.wave.flags:
            self.world.wave.flags.update_blocked({k for b in self.blockers.values() for k in b.manager.blocked}, frame)

    def movement(self, key, frame):
        return key in self.sleeping, False, 0

    def unblockable(self, key, frame):
        return key in self.sleeping or key in self.block_free

    def owner_cells(self):
        return self.skill_cells if self.cast and self.cast.get("s3_mode") else self.cells

    def in_cells(self, key, pos, cells):
        return circle_in_range(*pos, self.radii[key], cells, box_query=True)

    def targets(self, frame, cells, extra=()):
        result = []
        for key in self.states:
            if not self.alive(key) or key in self.sleeping:
                continue
            if not self.world.selectable(key, frame):
                continue
            row = self.world.by_id(key, frame)
            if row and (key in extra or self.in_cells(key, (row["row"], row["col"]), cells)):
                result.append(row)
        return result

    def servant_blocks(self):
        return {k for uid in self.servants.live for k in self.blockers[uid].manager.blocked}

    def power(self, frame):
        return mul(self.base_atk, ONE+_raw_f32(self.module.get("atk", 0))) if frame < self.buff_end else self.base_atk

    def hit(self, frame, key, source, power, scale, kind):
        if not self.alive(key) or self.invincible(key, frame):
            return
        if key in self.sleeping and source != self.uid:
            return
        state = self.states[key]
        hp_scale = self.low_hp["damage_scale"] if state.hp < mul(
            _raw_f32(self.world.stats[key]["hp"]), _raw_f32(self.low_hp["hp_ratio"])) else 1
        power = mul(power, _raw_f32(scale))
        if source == self.uid and key in self.servant_blocks():
            power = mul(power, _raw_f32(self.module.get("atk_scale", 1)))
        hit = state.receive_ordinary(frame, source=source, damage_type="arts", attack_q32=power,
                                     mitigation_q32=_raw_f32(self.world.stats[key]["res"])+state.res_add,
                                     output_scale_q32=_raw_f32(hp_scale))
        self.log(kind, frame, source=source, target=key, attack_q32=power, output_scale=hp_scale,
                 damage_q32=hit["damage_q32"], hp_after_q32=state.hp)
        if not self.alive(key):
            self.on_death(frame, key)

    def blast(self, frame, t):
        r, c = self.blockers[t["id"]].manager.position
        radius = self.bb["range_radius"]
        targets = [key for key, pos in self.world.positions[frame].items() if self.alive(key)
                   and key in self.world.active[frame] and self.world.selectable(key, frame)
                   and (pos[0]-r)**2+(pos[1]-c)**2 <= radius**2]
        targets = self.world.wave.attack_order(targets, frame, after_objects=True)
        self.log("servant_explosion", frame, servant=t["id"], position=(r, c), level=t["level"], targets=targets)
        for key in targets:
            self.hit(frame, key, self.uid, self.power(frame), self.bb["atk_scale"], "servant_blast")

    def enqueue(self, frame, count=1, preferred=None, source="owner"):
        if not self.spawn_queue:
            self.spawn_at = frame+self.spawn_delay
        if preferred is not None:
            self.tile_queue.append(preferred)
        self.spawn_queue.extend([source]*count)

    def collect_deaths(self, frame):
        for key, state in self.states.items():
            if state.hp <= HP_EPSILON and key not in self.dead:
                self.on_death(frame, key)

    def on_death(self, frame, key):
        if key in self.dead:
            return
        self.dead.add(key)
        self.recycle_at[frame+self.recycle_delay].append(key)
        if not self.world.present:
            return
        pos = self.world.positions[frame].get(key) or self.world.positions.get(frame-1, {}).get(key)
        in_owner = pos is not None and self.in_cells(key, pos, self.owner_cells())
        token_aura = (pos is not None and self.card["module_level"] == 3 and
                      any(self.in_cells(key, pos, self.servants.cells(t, t["channel"] is not None, extend=self.servants.aura_extends))
                          for t in self.servants.live.values()))
        if in_owner or token_aura:
            if self.module.get("atk_duration"):
                self.buff_end = frame+self.ticks(self.module["atk_duration"])
            self.log("rekindle", frame, target=key, aura="owner" if in_owner else "servant", buff_end=self.buff_end)
            if in_owner and len(self.servants.live) >= self.servants.upgrade["max_token_cnt"]:
                self.servants.attempt(frame)
            else:
                self.enqueue(frame, preferred=tile_of(pos) if in_owner else None, source="owner" if in_owner else "module")
        if key in self.links and self.links[key].get("attached"):
            self.enqueue(frame, int(self.bb["additional_token_cnt"]), source="tether")
            self.log("tether_death_summons", frame, target=key, count=int(self.bb["additional_token_cnt"]))

    def impact(self, frame, row, shot):
        power = self.power(frame) if self.generation == shot["generation"] else shot["cached_power"]
        self.hit(frame, row["id"], self.uid, power, 1, "necras_hit")

    def tick(self, frame):
        if not self.world.present:
            return
        self.collect_deaths(frame)
        if self.spawn_queue and frame >= self.spawn_at:
            source = self.spawn_queue.popleft()
            count = len(self.servants.live)
            preferred = self.tile_queue.popleft() if self.tile_queue and count < self.servants.upgrade["max_token_cnt"] else None
            self.log("servant_summon_attempt", frame, source=source, count=count, queued=len(self.spawn_queue))
            self.servants.attempt(frame, preferred)
            self.spawn_at = frame+self.spawn_delay
        for due in self.dragon_delays[:]:
            if due == frame:
                self.servants.upgrade_dragon(frame)
                self.dragon_delays.remove(due)
        self.servants.advance(frame)
        if self.cast:
            self.advance_cast(frame)
        if self.cast is None and frame >= self.resume:
            self.sp.advance(self.step)
        if self.cast is None and frame >= self.next_attack:
            rows = self.targets(frame, self.cells, self.servant_blocks())
            if rows:
                row = self.world.select(rows, frame)
                self.release_attack(frame, row, self.attacks["Default"], self.interval, "Modes/Default", cached_power=self.power(frame))
                self.log("attack_start", frame, target=row["id"])
            else:
                self.next_attack = frame+3

    def after_frame(self, frame):
        for born, targets, owner, power in self.attached_shots[:]:
            if frame > born:
                for key in targets:
                    if self.world.receiver_at(key, frame):
                        atk = self.power(frame) if owner == self.generation else power
                        self.hit(frame, key, self.uid, atk, self.bb["attack@atk_scale"], "necras_s3_hit")
                self.attached_shots.remove((born, targets, owner, power))
        for shot in self.pending:
            if shot["release_frame"] == frame:
                shot["cached_power"] = self.power(frame)
        self.bolts(frame)
        self.cleanup(frame)

    def button(self, frame, action):
        if self.cast or self.sp.points < self.sp.cost:
            self.log("skill_rejected", frame, sp=self.sp.points)
            return
        targets = []
        if self.slot == 2:
            rows = self.targets(frame, self.skill_cells)
            targets = self.world.wave.attack_order([r["id"] for r in rows], frame, observer_born=self.generation)
            if not targets:
                self.log("skill_rejected", frame, reason="no_target")
                return
        phase = action.get("phase", "after_tick")
        cast = resolve_cast_start(self.tables, self.ev, frame=frame, facing=self.view,
                                  direction=self.placement["direction"], phase=phase, sp=self.sp.points,
                                  owner_state="attack" if self.pending else "idle")
        self.pending.clear()
        self.cast = cast
        self.events.extend(e for e in cast["events"] if e["frame"] == frame)
        if cast["cast_frame"] == frame:
            self.sp.points = cast["sp_after"]
        if self.slot == 1:
            clock = run_committed_cast(self.tables, self.ev, horizon=0, facing=self.view, commit_phase=phase)["natural_end"]
            cast["end"] = frame+clock["end"]
            cast["tokens"] = tuple(self.servants.live)
        if self.slot == 2:
            cast["targets"] = targets[:int(self.bb["max_target"])]
            cast["release"] = frame+self.ticks(self.lasso["_preDelay"])
        if self.slot == 3:
            cast["count"] = 0
        self.log("necras_skill_start", frame, skill=self.slot)

    def finish(self, frame):
        self.log("necras_skill_end", frame, skill=self.slot)
        self.cast = None
        self.next_attack = frame+1
        self.resume = frame+2

    def advance_cast(self, frame):
        cast = self.cast
        self.events.extend(e for e in cast["events"] if e["frame"] == frame)
        if frame == cast["cast_frame"]:
            self.sp.points = cast["sp_after"]
        if self.slot == 1:
            if frame == cast["spell_frame"]:
                count = 0
                for uid in cast["tokens"]:
                    t = self.servants.live.get(uid)
                    if t:
                        count += 1+t["level"]
                        self.servants.remove(frame, uid, "resummon")
                if count:
                    self.enqueue(frame, count, source="s1")
                cast["fallback"] = not count
            if frame == cast["end"]:
                if cast.get("fallback") and not self.servants.live:
                    self.enqueue(frame, source="s1")
                self.finish(frame)
        elif self.slot == 2:
            self.advance_lasso(frame, cast)
        else:
            self.advance_s3(frame, cast)

    def advance_lasso(self, frame, cast):
        interrupted = any(e.get("operator_id", self.uid) == self.uid and e["flag"] in ("STUN", "FROZEN", "SILENCE")
                          and e["start_frame"] <= frame <= e["end_frame"] for e in self.controls)
        if frame == cast["release"] and not interrupted and "end" not in cast:
            for key in cast["targets"]:
                row = self.world.receiver_at(key, frame)
                if row and self.alive(key):
                    origin = self.placement["col"], self.placement["row"], self.placement["tile_z"]
                    self.links[key] = dict(position=origin, born=frame)
                    self.log("tether_release", frame, target=key)
        for key, link in list(self.links.items()):
            flags = self.world.wave.flags.at(key, frame) if self.world.wave.flags else ()
            row = self.world.receiver_at(key, frame)
            if (interrupted or not self.alive(key) or row is None or
                    {"LEVITATE", "OUT_OF_GROUND", "DISAPPEARED"}.intersection(flags) or frame == link.get("expires")):
                self.unlink(frame, key)
                continue
            if not link.get("attached"):
                if frame == link["born"]:
                    continue
                move = ordinary_trace_step(link["position"], stable_enemy_mount(self.tables, row), speed=self.lasso_speed,
                                           effective_dt=self.dt, round_position=True)
                link["position"] = move.position
                if not move.reached:
                    continue
                link.update(attached=True, pulse=frame, expires=link["born"]+self.ticks(self.bb["hit_duration"]))
                attrs = self.world.scene.enemies[self.world.stats[key]["enemy_key"]]["attributes"]
                immune = attrs["sleepImmune"]["m_value"] or key in self.sleep_immune or "SLEEP_IMMUNE" in flags
                if not immune:
                    self.sleeping.add(key)
                    self.control(key, frame)
                    for uid, blocker in self.blockers.items():
                        if key in blocker.manager.blocked:
                            blocker.manager.release(key)
                            blocker.stable.pop(key, None)
                            blocker.tweens.pop(key, None)
                            self.world.stats[key]["blocked"] = False
                            self.melee_clocks.pop((uid, key), None)
                            self.log("release_block", frame, holder=uid, target=key)
                    inc = self.world.incoming
                    if inc and key in inc.enemies:
                        inc.enemies[key]["shot"] = None
                        inc.enemies[key]["sleep_until"] = link["expires"]-1
                self.log("tether_attach", frame, target=key, sleeping=not immune)
            if frame == link["pulse"]:
                self.hit(frame, key, self.uid, self.power(frame), self.bb["atk_scale"], "tether_hit")
                link["pulse"] += self.ticks(self.bb["interval"])
                if not self.alive(key):
                    self.unlink(frame, key)
        if (interrupted or frame >= cast["release"]) and not self.links and "end" not in cast:
            begin, _ = _begin_clip(self.tables, self.ev, "Skill_2_Begin", self.view)
            end, _ = _begin_clip(self.tables, self.ev, "Skill_2_End", self.view)
            cast["end"] = max(cast["cast_frame"]+self.ticks(begin["duration"]), frame,
                              cast["release"]+self.ticks(self.lasso["_minPostDelayWhenProjectileInvalid"]))+1+self.ticks(end["duration"])
            self.log("all_tethers_lost", frame)
        if frame == cast.get("end"):
            self.finish(frame)

    def unlink(self, frame, key):
        del self.links[key]
        self.sleeping.discard(key)
        inc = self.world.incoming
        if inc and key in inc.enemies:
            inc.enemies[key].pop("sleep_until", None)
        self.log("tether_end", frame, target=key)

    def advance_s3(self, frame, cast):
        if frame == cast["spell_frame"]:
            cast.update(s3_mode=True, attack=frame+1)
        if frame == cast.get("attack"):
            shot = ranged_shot_release(self.tables, self.ev, self.attacks["S3"], attack_frame=frame,
                                       attack_interval=self.interval, facing=self.view, mode_path="Modes/S3")
            cast["release"] = shot["release_frame"]
            tail = _after_spell_clock(cast["release"]-frame, shot["clock"]["cached_duration_seconds"],
                                      self.step, same_frame_resume=True)
            cast["after_attack"] = frame+tail["end"]
            cast["attack"] = frame+self.ticks(self.interval)
            cast["count"] += 1
        if frame == cast.get("release"):
            keys = [r["id"] for r in self.targets(frame, self.skill_cells)]
            self.attached_shots.append((frame, keys, self.generation, self.power(frame)))
            self.log("necras_s3_volley", frame, number=cast["count"], targets=keys)
        # BuffToOwner uses ON_CAST_END (3). Summons from projectile kills can
        # enter the UnitManager before this attack pays its sacrifice.
        if frame != cast.get("after_attack"):
            return
        ordinary = next((self.servants.live[k] for k in self.entity_order
                         if k in self.servants.live and not self.servants.live[k]["dragon"]), None)
        if ordinary:
            self.servants.upgrade_dragon(frame)
            if ordinary["level"]:
                self.dragon_delays.append(frame+self.spawn_delay)
            self.servants.remove(frame, ordinary["id"], "sacrifice")
        if cast["count"] == self.bb["attack@necras_s_3[attack_cnt].max_stack_cnt"]:
            self.finish(frame)
