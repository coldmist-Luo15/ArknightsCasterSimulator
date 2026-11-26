"""Necrass's owned V079 tokens on the stage's placement, block and damage receivers."""
from collections import defaultdict
from math import floor

from .animation_assets import read_spine_events
from .attack_range import range_cells
from .attributes import base_integer_at_level
from .baked_mount import baked_position, mount_parents, transform_point
from .blocking import StationaryBlockSearch
from .damage import HP_EPSILON
from .elemental_casters import _board
from .fixed import ONE, mul
from .skill_recovery import READY_EPSILON
from .mystic_cycle import baked_key_sample
from .opening_scene import bundle_environment
from .volley_damage import _raw_f32


def tile_of(position):
    return tuple(floor(x + .5) for x in position)


class Servants:
    def __init__(self, battle):
        self.b = battle
        tables, card = battle.tables, battle.card
        self.token_id, = battle.ev["tokens"]
        prefab = battle.ev["tokens"][self.token_id]
        unit = tables.read("excel/character_table.json")[self.token_id]
        phase = unit["phases"][card["elite"]]
        frames = phase["attributesKeyFrames"]
        self.base = dict(frames[-1]["data"])
        for field in ("maxHp", "atk", "def"):
            self.base[field] = base_integer_at_level(frames, card["level"], field)
        self.upgrade = _board(card, 0, "1")
        self.dragon = _board(card, 3, "4")
        ranges = tables.read("excel/range_table.json")
        self.area = ranges[phase["rangeId"]]
        binding = unit["skills"][2]["skillId"]
        self.skill = tables.read("excel/skill_table.json")[binding]["levels"][-1]
        self.skill_area = ranges[self.skill["rangeId"]]
        self.skill_scale = next(v["value"] for v in self.skill["blackboard"] if v["key"] == "attack@atk_scale")
        self.radius = next(c["fields"]["m_Radius"] for c in prefab["components"] if c["script"] == "CircleCollider2D" and not c["path"])
        self.bake = tables.read(f"bakemuzzledata/character/{self.token_id}_default.json")["bakedAnimDatas"]
        self.clips, self.parents = self.skin(tables, prefab)
        self.live = {}
        self.serial = 0
        self.selectors = {c["path"].split("/")[1]: c["fields"] for c in battle.ev["character"]["components"]
                          if c["script"] == "NecromancerQueueTileSelector" and "/TrySpawnToken/" in c["path"]}
        self.aura_extends = any(c["fields"]["_extendable"] for equip in battle.ev["equipment"].values()
                                for c in equip["components"] if c["script"] == "AutoLoadBoxRange")
        self.block_volumes = {k: battle.world.scene.enemies[r["enemy_key"]]["attributes"]["blockVolume"]["m_value"]
                              for k, r in battle.world.stats.items()}
        self.disallowed = {(p["row"], p["col"]) for p in battle.world.scene.level["tilesDisallowToLocate"] or ()}
        self.tile_changes = defaultdict(list)
        self.tile_state = {}
        for change in battle.request.get("tile_build_events", ()):
            self.tile_changes[change["frame"]].append(change)
        self.candidates = [dict(r, row=0, col=0, instance_uid=battle.world.births[k]["order"],
                                motion_mode="FLY" if r.get("airborne") else "WALK",
                                special_condition_passed=not r.get("block_free", False))
                           for k, r in battle.world.stats.items()]

    def skin(self, tables, prefab):
        cache = ("servant_skin", self.token_id)
        if cache not in tables._prefab_data:
            source = prefab["source"]
            raw, _ = tables.verified_bundle(source["member"])
            root = bundle_environment(raw).container[source["container"]].deref()
            objects = root.assets_file.objects
            def read(ref):
                return objects[ref["m_PathID"]].read_typetree()
            animator = next(c["fields"] for c in prefab["components"] if c["script"] == "CharacterAnimator")
            clips, parents = {}, {}
            for view in ("Front", "Back"):
                skin = animator["_" + view.lower()]
                skeleton = read(read(skin["skeleton"])["skeletonDataAsset"])
                decoded = read_spine_events(read(skeleton["skeletonJSON"])["m_Script"])
                clips[view] = {a["name"]: a["duration"] for a in decoded["animations"]}
                parents[view] = mount_parents(objects, read(skin["hitTransform"]), root.path_id)
            tables._prefab_data[cache] = clips, parents
        return tables._prefab_data[cache]

    def cells(self, token, channel=False, *, extend=True):
        data = self.area
        if channel:
            extra = token["level"] // 3 if extend else 0
            data = dict(self.skill_area, grids=[*self.skill_area["grids"],
                        *(dict(row=0, col=2+i) for i in range(extra))])
        return range_cells(data, token["placement"], self.b.scene["rows"], self.b.scene["cols"])

    def special(self):
        return next((t for t in self.live.values() if t["dragon"]), None)

    def update_tiles(self, frame):
        for event in self.tile_changes.get(frame, ()):
            pos = event["row"], event["col"]
            state = self.tile_state.setdefault(pos, dict(self.b.world.scene.tile(*pos)))
            state.update(event["changes"])

    def attempt(self, frame, preferred=None):
        b = self.b
        selector = self.selectors["S3" if b.cast and b.cast.get("s3_mode") else "Default"]
        opts = selector["_options"]
        candidates = []
        if len(self.live) < self.upgrade["max_token_cnt"]:
            # PhysicsRange.FindTiles scans Map.get_Item(row, col) with both indices increasing.
            for pos in sorted(b.owner_cells()):
                tile = self.tile_state.get(pos) or b.world.scene.tile(*pos)
                if opts["checkHeightType"] and ("LOWLAND", "HIGHLAND")[opts["heightType"]] != tile["heightType"]:
                    continue
                buildable = ("NONE", "MELEE", "RANGED", "ALL").index(tile["buildableType"])
                if not buildable & opts["buildableType"] or pos in b.orders.field.occupied:
                    continue
                mask = tile.get("advancedBuildableMask", 1)
                if not opts["allowAllAdvancedBuildableMask"] and not (mask & opts["advancedBuildableMask"]):
                    continue
                if opts["checkExtraBuildableCheckers"] and (pos in self.disallowed or not tile.get("extraBuildable", True)):
                    continue
                candidates.append(pos)
        if candidates:
            # NecromancerQueueTileSelector._DoFilter shuffles, moves tiles with
            # eligible enemies forward, then moves the valid queued tile to front.
            pool = candidates+[preferred] if preferred in candidates else candidates
            shuffled = b.rng.select_targets(pool, len(pool))
            occupied = {tile_of(p) for k, p in b.world.positions[frame].items() if b.alive(k)
                        and k in b.world.active[frame] and b.world.selectable(k, frame)
                        and k not in b.sleeping and selector["_targetMotion"] & (2 if b.world.stats[k].get("airborne") else 1)}
            pos = preferred if preferred in candidates else next((p for p in shuffled if p in occupied), shuffled[0])
            reason = "defeated_tile" if preferred == pos else "enemy_tile" if pos in occupied else "free_tile"
            b.log("servant_tile_selected", frame, preferred=preferred, available=candidates, position=pos, rule=reason,
                  enemy_tiles=sorted(occupied.intersection(candidates)), living=list(self.live))
            t = self.spawn(frame, pos)
        else:
            ordinary = [t for t in self.live.values() if not t["dragon"]]
            reason = "capacity" if len(self.live) >= self.upgrade["max_token_cnt"] else "no_free_tile"
            if not ordinary:
                b.log("servant_summon_unavailable", frame, reason=reason)
                return
            t = min(ordinary, key=lambda u: (u["level"], u["hp"], -u["order"]))
            changed = not t["level"]
            t["level"] = 1
            self.attributes(t)
            self.health(t, t["maximum"])
            b.log("servant_upgrade" if changed else "servant_heal", frame, servant=t["id"], hp_q32=t["hp"], reason=reason)
            if not changed:
                return
        if b.slot == 1:
            b.blast(frame, t)

    def spawn(self, frame, pos):
        b = self.b
        self.serial += 1
        uid = f"{self.token_id}#{self.serial}"
        place = dict(row=pos[0], col=pos[1], direction=b.placement["direction"], frame=frame)
        special = b.slot == 3 and self.special() is None
        t = dict(id=uid, summoner=b.uid, placement=place, level=0, dragon=special, order=self.serial,
                 healable=False, radius=self.radius, pending=None, next_attack=frame+(ONE+b.step-1)//b.step+1,
                 sp=0, channel=None)
        self.live[uid] = b.units[uid] = t
        b.orders.field.occupied.add(pos)
        b.blockers[uid] = StationaryBlockSearch(b.tables, dict(position=pos, capacity=1, mode="WALK",
                                                first_search_frame=frame+1), self.candidates)
        for key, volume in self.block_volumes.items():
            if volume >= 0:
                b.blockers[uid].candidates[key]["base_volume"] = volume
        self.attributes(t)
        t["hp"] = t["maximum"]
        if b.world.incoming:
            view = b.view
            clip = "Idle" if special else "Idle_2"
            track = self.bake[view]["bakedMPDatas"][clip]["1"]
            offset = transform_point(baked_position(track, 0, b.dt), self.parents[view])
            b.world.incoming.units[uid] = dict(placement=place.copy(), order=len(b.world.cards)+self.serial,
                                                radius=self.radius, hp=t["hp"], defense=t["defense"], offset=offset)
            b.world.incoming.move_holder(uid, *pos)
        b.entity_order.append(uid)
        b.log("servant_spawn", frame, servant=uid, position=pos, dragon=special, level=0, capacity=b.blockers[uid].manager.capacity)
        return t

    def attributes(self, t):
        b, base, lv = self.b, self.base, t["level"]
        def bonus(key):
            if t["dragon"]:
                return self.dragon.get(key, 0) + lv*b.bb.get("attack@"+key, 0)
            return lv*self.upgrade.get(key, 0)
        uid = t["id"]
        t["maximum"] = _raw_f32(base["maxHp"]*(1+bonus("max_hp")))
        t["atk"] = _raw_f32(base["atk"]*(1+bonus("atk")))
        t["defense"] = _raw_f32(base["def"]*(1+bonus("def")))
        b.world.receiver.defenses[uid] = t["defense"]
        b.world.receiver.resistances[uid] = _raw_f32(base["magicResistance"]+bonus("magic_resistance"))
        t["interval"] = base["baseAttackTime"] + (self.dragon["base_attack_time"] if t["dragon"] else 0)
        capacity = self.dragon["block_cnt"] + lv//3 if t["dragon"] else lv*self.upgrade["block_cnt"]
        b.blockers[uid].manager.capacity = int(base["blockCnt"] + capacity)

    def health(self, t, value):
        t["hp"] = min(t["maximum"], value)
        incoming = self.b.world.incoming
        if incoming and t["id"] in incoming.units:
            incoming.units[t["id"]].update(hp=t["hp"], defense=t["defense"])

    def upgrade_dragon(self, frame):
        b, t = self.b, self.special()
        if t is None:
            return
        # MAX_HP changes retain the health ratio. HealViaMaxHpRatio bypasses HEAL_FREE.
        hp, maximum = t["hp"], t["maximum"]
        t["level"] = min(t["level"]+1, int(b.bb["attack@max_stack_cnt"]))
        self.attributes(t)
        self.health(t, hp*t["maximum"]//maximum + mul(t["maximum"], _raw_f32(b.bb["attack@hp_ratio"])))
        b.log("dragon_upgrade", frame, servant=t["id"], level=t["level"], hp_q32=t["hp"],
              capacity=b.blockers[t["id"]].manager.capacity)

    def remove(self, frame, uid, reason):
        b = self.b
        t = self.live.pop(uid)
        blocker = b.blockers.pop(uid)
        for key in blocker.manager.blocked:
            b.world.stats[key]["blocked"] = False
            b.melee_clocks.pop((uid, key), None)
            b.log("release_block", frame, holder=uid, target=key)
        b.orders.field.occupied.discard(blocker.manager.position)
        b.units.pop(uid)
        if b.world.incoming:
            b.world.incoming.blocked_sources.difference_update(blocker.manager.blocked)
            b.world.incoming.withdraw(uid)
        b.sync_block_flags(frame)
        b.recycle_at[frame+b.recycle_delay].append(uid)
        b.world.receiver.defenses.pop(uid)
        b.world.receiver.resistances.pop(uid)
        b.log("servant_leave", frame, servant=uid, reason=reason, position=blocker.manager.position,
              level=t["level"], dragon=t["dragon"])

    def targets(self, t, frame, channel):
        b = self.b
        held = b.blockers[t["id"]].manager.blocked
        cells = self.cells(t, channel)
        rows = [r for r in b.targets(frame, cells, held) if not r.get("airborne")]
        if channel:
            return [r["id"] for r in rows]
        priority = [r for r in rows if r["id"] in held] or rows
        return [b.world.select(priority, frame)["id"]] if priority else []

    def advance(self, frame):
        b = self.b
        for t in list(self.live.values()):
            if t["hp"] <= HP_EPSILON:
                self.remove(frame, t["id"], "defeated")
                continue
            if t["dragon"] and frame > t["placement"]["frame"]:
                if self.channel(t, frame):
                    continue
            if frame >= t["next_attack"]:
                keys = self.targets(t, frame, False)
                if keys:
                    clip = "Attack" if t["dragon"] else "Attack_2"
                    key_time, = self.bake[b.view]["bakedEvents"][clip]["eventTime"]
                    speed = 1/min(1, t["interval"]/self.clips[b.view][clip])
                    t["pending"] = frame+baked_key_sample(key_time, speed, b.dt)[0], keys[0]
                    t["next_attack"] = frame+b.ticks(t["interval"])
                else:
                    t["next_attack"] = frame+3
            if t["pending"] and frame == t["pending"][0]:
                key = t["pending"][1]
                t["pending"] = None
                if b.world.receiver_at(key, frame):
                    b.hit(frame, key, t["id"], t["atk"], 1, "servant_hit")

    def channel(self, t, frame):
        b = self.b
        if t["channel"] is None:
            cost = self.skill["spData"]["spCost"]*ONE
            t["sp"] = min(cost, t["sp"]+b.step)
            if t["sp"]+READY_EPSILON < cost or not self.targets(t, frame, True):
                return False
            t["sp"] = 0
            t["pending"] = None
            prefix = "Skill_Down" if t["placement"]["direction"] == "DOWN" else "Skill"
            t["channel"] = frame
            first_key, = self.bake[b.view]["bakedEvents"][prefix+"_Loop"]["eventTime"]
            t["pulse"] = (frame + b.ticks(self.clips[b.view][prefix+"_Begin"])
                          + baked_key_sample(first_key, 1, b.dt)[0])
            t["end"] = frame + b.ticks(self.skill["duration"])
            b.log("servant_skill_start", frame, servant=t["id"])
        if frame >= t["end"]:
            t["channel"] = None
            t["next_attack"] = frame+1
            b.log("servant_skill_end", frame, servant=t["id"])
        elif frame == t["pulse"]:
            for key in self.targets(t, frame, True):
                b.hit(frame, key, t["id"], t["atk"], self.skill_scale, "servant_skill_hit")
            t["pulse"] += b.ticks(1)
        return True
