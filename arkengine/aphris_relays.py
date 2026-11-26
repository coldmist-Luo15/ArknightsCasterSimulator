"""Aphris's QUEUED token card and its S3 capacity changes (CN V079)."""
from .caster_ranges import DIRECTION
from .fixed import ONE, mul
from .module_effects import active_board, talent_rows
from .stage_deployment import DeploymentField
from .volley_damage import _raw_f32


class AphrisRelays:
    def __init__(self, tables, card, evidence):
        phase = card["module_phase"] or {}
        units = tables.read("excel/character_table.json")
        host = units[card["operator_id"]]["talents"][0]["candidates"]
        token, = {row["tokenKey"] for row in host}
        rank = card["elite"], card["level"], card["potential"]
        talent = active_board(talent_rows(phase, 0, "1", host), *rank)
        token_board = active_board(units[token]["talents"][0]["candidates"], *rank)
        for part in phase.get("parts", ()):
            if part["isToken"]:
                token_board.update(active_board(part["addOrOverrideTalentDataBundle"]["candidates"] or [], *rank))
        model, = (c["fields"] for c in evidence["tokens"][token]["components"] if c["script"] == "Token")
        attributes = units[token]["phases"][card["elite"]]["attributesKeyFrames"][-1]["data"]
        mods = {b["key"]: b["value"] for b in (phase.get("tokenAttributeBlackboard") or {}).get(token, [])}
        if model["_cardPolicy"] != 2 or model["_occupiedRemainingCharacterCnt"] or attributes["blockCnt"]:
            raise ValueError("中继器卡片策略、占位或阻挡属性已改变")
        step = _raw_f32(tables.fixed_timestep())
        self.cooldown = (_raw_f32(attributes["respawnTime"] + mods.get("respawn_time", 0)) + step-1) // step
        self.duration = (_raw_f32(token_board["duration"]) + step-1) // step
        self.cost = int(attributes["cost"] + mods.get("cost", 0))
        self.atk_bonus = talent.get("atk", 0)
        self.capacity = self.stock = int(talent["cnt"])
        self.extra = next((int(x["value"]) for x in evidence["skill_blackboard"] if x["key"] == "token_recharge_cnt"), 0)
        self.queued = 0
        self.next_ready = None
        self.active = {}
        self.boosted = False
        self.events = []

    @property
    def ready(self):
        return max(0, self.stock - self.queued)

    def snapshot(self):
        return dict(capacity=self.capacity, ready=self.ready, queued=self.queued,
                    next_ready_frame=self.next_ready, deployed=[dict(r) for r in self.active.values()])

    def attack(self, panel_attack, skill_bonus=0):
        bonus = _raw_f32(self.atk_bonus) if self.active else 0
        return mul(_raw_f32(panel_attack), ONE + _raw_f32(skill_bonus) + bonus)

    def record(self, frame, kind, **fields):
        self.events.append(dict(frame=frame, kind=kind, **fields, capacity=self.capacity, ready=self.ready,
                                queued=self.queued, next_ready_frame=self.next_ready))

    def tick(self, frame):
        # Card.OnTick (0x2722e54) consumes one expired charge in DECK, before
        # object callbacks and player inputs. A pending second charge waits a full period.
        if self.next_ready == frame:
            self.queued -= 1
            self.next_ready = frame + self.cooldown if self.queued else None
            self.record(frame, "relay_ready")
        for key, relay in list(self.active.items()):
            if relay["expires_frame"] == frame:
                self.withdraw(frame, key, "timeout")

    def withdraw(self, frame, key, cause):
        if key not in self.active:
            self.record(frame, "relay_withdraw_rejected", relay=key, reason="not_deployed")
            return
        del self.active[key]
        # The token's ON_BUFF_FINISH credits stock and one QUEUED charge.
        # The raw balance may be negative during S3 shutdown before withdrawals settle.
        self.stock = min(self.capacity, self.stock + 1)
        self.queued = max(0, min(self.stock, self.queued + 1))
        if self.queued and self.next_ready is None:
            self.next_ready = frame + self.cooldown
        self.record(frame, "relay_withdraw", relay=key, cause=cause)

    def switch(self, frame, active):
        if self.boosted == active:
            raise ValueError("中继器上限切换重复")
        self.boosted = active
        delta = self.extra if active else -self.extra
        self.capacity += delta
        self.stock = min(self.capacity, self.stock + delta)
        if not active:
            self.queued = max(0, min(self.stock, self.queued + delta))
        self.record(frame, "relay_capacity", delta=delta)
        for key in list(self.active):
            self.withdraw(frame, key, "skill_start" if active else "skill_finish")
        # ResetTokenRespawnTime(false) sets the current timer to zero. It does
        # not credit stock synchronously (PeriodicTimer.Reset at 0x3e38580).
        self.next_ready = frame + 1 if self.queued else None
        self.record(frame, "relay_reset_redeploy")

    def deploy(self, frame, action, scene, host, dp, occupied_allies=()):
        key = action["relay"]
        row, col, direction = action["row"], action["col"], action["direction"]
        if direction not in DIRECTION or type(row) is not int or type(col) is not int:
            raise ValueError("中继器需要整数坐标和有效朝向")
        occupied = {(r["row"], r["col"]) for r in self.active.values()} | set(occupied_allies) | {(host["row"], host["col"])}
        grid = scene.level["mapData"]["map"]
        reason = None
        if key in self.active:
            reason = "already_deployed"
        elif (row, col) in occupied:
            reason = "tile_occupied"
        elif not (0 <= row < len(grid) and 0 <= col < len(grid[0])):
            reason = "outside_map"
        elif ({"row": row, "col": col} in (scene.level["tilesDisallowToLocate"] or [])
              or scene.tile(row, col)["buildableType"] not in ("MELEE", "RANGED", "ALL")
              or scene.tile(row, col)["playerSideMask"] != "ALL"):
            reason = "illegal_tile"
        elif not self.ready:
            reason = "redeploy_cooldown" if self.queued else "no_stock"
        elif dp < self.cost:
            reason = "insufficient_cost"
        if reason:
            self.record(frame, "relay_deploy_rejected", relay=key, reason=reason, cost_after=dp)
            return dp
        self.stock -= 1
        self.active[key] = dict(relay=key, row=row, col=col, direction=direction,
                                expires_frame=None if self.boosted else frame + self.duration)
        self.record(frame, "relay_deploy", **self.active[key], paid=self.cost, cost_after=dp-self.cost)
        return dp-self.cost


RELAY_INPUTS = {"relay_deploy", "relay_withdraw"}


def split_relay_inputs(request):
    actions, relays = [], {}
    for action in request["actions"]:
        if action.get("kind") not in RELAY_INPUTS:
            actions.append(action)
            continue
        frame = action["frame"]
        if (type(frame) is not int or not 0 <= frame <= request["end_frame"] or
                not isinstance(action.get("relay"), str) or not action["relay"] or
                action.get("operator_id", "char_4229_aphris") != "char_4229_aphris"):
            raise ValueError("中继器操作需要有效的帧、宿主和召唤物编号")
        relays.setdefault(frame, []).append(action)
    return dict(request, actions=actions), relays


class RelayField(DeploymentField):
    """Aphris shares the stage cost clock and occupied cells with her relay card."""
    def __init__(self,tables,card,evidence,opening,scene,host):
        super().__init__(tables,opening,scene)
        self.host=host
        self.card=AphrisRelays(tables,card,evidence)

    def before_objects(self,frame):
        super().before_objects(frame)
        self.card.tick(frame)

    def place_operator(self,placement):
        return super().place_operator(placement,[(r["row"],r["col"]) for r in self.card.active.values()])

    def relay_input(self,frame,action,host_present):
        key=action["relay"]
        if not host_present:
            self.card.record(frame,action["kind"]+"_rejected",relay=key,reason="host_not_deployed")
        elif action["kind"]=="relay_withdraw":
            self.card.withdraw(frame,key,"manual")
        else:
            self.dp=self.card.deploy(frame,action,self.scene,self.host,self.dp,self.occupied)
        self.card.events[-1]["cost_bar"]=self.timer.bar(self.dp,self.cap)
