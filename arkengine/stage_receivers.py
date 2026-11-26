"""Live receivers and ordinary stage settlement on the common wave clock."""
from .battle_outcome import ordinary_battle_outcome
from .damage import HP_EPSILON
from .health_bounds import ordinary_life_points
from .lin_incoming import HighlandCrossbows, incoming_by_frame, ordinary_ranged
from .phalanx_wave import PhalanxWave
from .volley_damage import _raw_f32


class StageReceivers:
    def __init__(self, battle, request, placements, cards, events, *, native_first_walk=False):
        self.battle = battle
        self.tables = battle.tables
        self.end = request["end_frame"]
        self.wave = wave = PhalanxWave(battle, self.end, allow_flying=True, allow_thrower=True,
            allow_later_waves=True, first_sample=0, absolute_route_times=True, allow_partial_movement=True,native_first_walk=native_first_walk)
        self.schedule = battle.opening["wave_schedule"]
        self.roster = {e["instance_id"] for e in self.schedule["events"] if e["kind"] == "spawn"}
        self.deaths, self.exits = {}, {}
        self.escapes, self.events = [], events
        self.life = self.initial_life = battle.scene.level["options"]["maxLifePoint"]
        self.victory = None
        self.external = incoming_by_frame(request)
        ranged = any(ordinary_ranged(battle.scene, b["enemy_key"]) for b in wave.births.values())
        self.incoming = None
        self.hp = {uid:_raw_f32(c["panel_attributes"]["maxHp"]) for uid,c in cards.items()}
        self.defenses = {uid:_raw_f32(c["panel_attributes"]["def"]) for uid,c in cards.items()}
        self.resistances = {uid:_raw_f32(c["panel_attributes"]["magicResistance"]) for uid,c in cards.items()}
        if ranged or self.external:
            self.incoming = HighlandCrossbows(self.tables, wave, placements, cards, allow_thrower=True, track_stuns=True)
            self.incoming.events = events
            self.incoming.moving_holders = {a["operator_id"] for a in request["actions"] if a.get("kind") == "move_ally"}

    def valid(self, key, frame):
        return (key not in self.deaths and key not in self.exits and self.wave.targetable(key,frame)
                and self.wave.attrs[key]["hp_q32"] > HP_EPSILON)

    def settle_exits(self,frame):
        wave=self.wave
        for key,exit_frame in wave.escapes.items():
            if exit_frame==frame and key not in self.deaths:
                loss=self.battle.scene.enemies[wave.births[key]["enemy_key"]]["lifePointReduce"]["m_value"]
                self.life=max(0,self.life-loss)
                self.exits[key]=frame
                self.escapes.append(dict(id=key,instance=key,frame=frame,remaining_life=self.life,
                    scheduler_release_window=[frame,frame],outcomes=["reached_exit"],life_loss_min=loss,life_loss_max=loss))
                self.events.append(dict(kind="enemy_escaped",frame=frame,target=key,remaining_life=self.life))
                if self.life==0:
                    break

    def damage(self,key,frame,after):
        self.wave.attrs[key]["hp_q32"]=after
        if after<=HP_EPSILON:
            self.deaths[key]=frame

    def outcome(self,frame):
        case=dict(through_frame=frame,exit_coverage=True,exits=self.escapes,unresolved=[],finishes=[
            dict(instance=k,first_possible_frame=f,finished_by_frame=f,scheduler_killed_by_frame=f) for k,f in self.deaths.items()])
        life=ordinary_life_points(self.initial_life,[case],frame)
        return ordinary_battle_outcome(self.battle.scene,self.schedule,[case],life,self.tables.fixed_timestep(),use_stage_objective=True)

    def stage_ended(self,frame):
        if self.life==0:
            return "stage_defeat"
        if self.victory is None and not self.schedule["pending"] and len(self.roster)==len(self.deaths)+len(self.exits):
            window=self.outcome(self.end).get("finish_game_projection")
            if window:
                self.victory=window["frames"][-1]
        if self.victory is not None and frame>=self.victory:
            return "stage_victory_projected"
