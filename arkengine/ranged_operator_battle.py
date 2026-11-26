"""Ordinary wave, cards, moving receivers and projectiles on the 30 Hz caster clock."""
from collections import defaultdict

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .blocking import StationaryBlockSearch
from .caster_flight import _movement
from .caster_prefabs import caster_prefab_evidence
from .continuous_ranged import prepare_continuous_ranged
from .damage import HP_EPSILON, ordinary_arts_hit, ordinary_physical_hit
from .elemental_projectiles import ranged_shot_release, stable_enemy_mount, stationary_cast_mounts
from .enemy_elements import EnemyElements
from .enemy_speed import move_attribute, movement_cap
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .ordinary_receiver_buffs import ReceiverBuffs, passive_buffs
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import deploy_skill
from .sbell_live import blocked_enemy_attack
from .stage_deployment import RangedReentries
from .trace_motion import ordinary_trace_step, paracurve_horizontal_step
from .volley_damage import _raw_f32


class RangedOperatorBattle:
    def __init__(self,tables,request,releases):
        self.tables,self.request=tables,request
        deck_only=not any(a["operator_id"]==self.uid and a.get("kind","deploy")=="deploy" for a in request["actions"])
        self.card,self.stage,self.first,self.scene,self.world=prepare_continuous_ranged(
            tables,request,self.uid,releases,native_walk=getattr(self,"native_walk",True),deck_only=deck_only)
        self.orders=RangedReentries(tables,self.stage,self.world,request,self.first["frame"])
        self.dt=tables.fixed_timestep()
        self.step=_raw_f32(self.dt)
        self.slot=self.card["skill"]
        self.panel=self.card["panel_attributes"]
        self.ev=caster_prefab_evidence(tables,self.uid,self.slot,skill_level=self.card["skill_level"],
            module_id=self.card["module"],module_level=self.card["module_level"])
        self.bb={b["key"]:b["value"] for b in self.card["skill_row"]["blackboard"]}
        self.rng=tables.combat_random(request.get("battle_random_seed",0))
        self.states={k:EnemyElements.from_scene_enemy(tables,dict(v,hp_q32=_raw_f32(v["hp"]))) for k,v in self.world.stats.items()}
        self.radii={k:tables.enemy_circle(v["prefab"]) for k,v in self.world.stats.items()}
        self.events=[]
        self.units={}
        self.walks={}
        self.blockers={}
        self.melee_clocks={}
        self.melee_profiles={}
        self.pending=[]
        self.flights=[]
        self.placement=None
        self.generation=None
        self.world.present=False
        self.world.hp=0
        if self.world.incoming:
            self.world.incoming.units.clear()
        self.sp=None
        self.corrections=defaultdict(list)
        for hit in request.get("buildup_before_attacks",()):
            self.corrections[hit["frame"],hit.get("phase","before_attack_search")].append(hit)
        self.receiver_buffs={}
        self.buff_events=defaultdict(list)
        self.ally_heals=defaultdict(list)
        for event in request.get("ally_heals",()):
            self.ally_heals[event["frame"]].append(event)
        for event in request.get("enemy_receiver_buffs",()):
            self.buff_events[event["frame"]].append(event)
        for key,row in self.world.stats.items():
            for buff in passive_buffs(tables,"enemies",row["prefab"]):
                attrs=buff.get("attributes") or {}
                if 13 in attrs.get("abnormalImmunes",()):
                    row["root_immune"]=True
                if buff.get("templateKey") in ("evade","evade_magic","evade_physical","evade_ranged"):
                    values={b["key"]:b["value"] for b in buff.get("blackboard") or ()}
                    if "prob" in values:
                        frame=self.world.births[key]["frame"]
                        self.buff_events[frame].append(dict(frame=frame,target=key,template=buff["templateKey"],prob=values["prob"]))
        self.spawn_counts=defaultdict(int)
        for birth in self.world.births.values():
            self.spawn_counts[birth["frame"]]+=1

    def log(self,kind,frame,**data):
        self.events.append(dict(kind=kind,frame=frame,**data))

    def alive(self,key):
        return self.states[key].hp>HP_EPSILON and key not in self.world.departed

    def ticks(self,seconds):
        return fixed_wait_frames(single(single(seconds)/self.dt))

    def area(self,key):
        return range_cells(self.card[key] or self.card["base_range"],self.placement,self.scene["rows"],self.scene["cols"])

    def deploy(self,frame,place):
        tile=self.world.tiles[place["row"],place["col"]]
        self.placement=dict(place,tile_z=tile["position"]["z"]-tile["locate_height_offset"])
        self.view="Back" if place["direction"]=="UP" else "Front"
        self.sign=-1 if place["direction"]=="LEFT" else 1
        self.generation=frame
        self.world.present=True
        self.world.deployment_frame=frame
        self.world.hp=_raw_f32(self.panel["maxHp"])
        self.sp=deploy_skill(self.tables,self.card,frame)[0].state
        self.cells=self.area("base_range")
        self.skill_cells=self.area("skill_range")
        self.next_attack=frame+_birth(self.tables,self.card,self.ev,self.view)["born_exit_update"]+1
        self.log("deployed",frame,operator_id=self.uid)

    def clear_owner(self):
        self.pending.clear()
        self.generation=None

    def pool(self,frame,cells):
        return [r for r in self.world.snapshot(frame) if self.alive(r["id"]) and
                circle_in_range(r["row"],r["col"],self.radii[r["id"]],cells,box_query=True)]

    def release_attack(self,frame,row,ability,interval,mode=None,**data):
        if row["col"]!=self.placement["col"]:
            self.sign=1 if row["col"]>self.placement["col"] else -1
        shot=ranged_shot_release(self.tables,self.ev,ability,attack_frame=frame,attack_interval=interval,
                                 facing=self.view,mode_path=mode)
        shot.update(data,target=row["id"],projectile=ability["_projectileKey"],sign=self.sign,generation=self.generation)
        self.pending.append(shot)
        self.next_attack=frame+self.ticks(interval)
        return shot

    def projectile_receiver(self,key,frame,birth):
        return self.world.by_id(key,frame,projectile_born=birth)

    def bolts(self,frame,*,before_birth=None):
        for shot in self.flights[:]:
            if shot.get("advanced")==frame or before_birth is not None and shot["release_frame"]>=before_birth:
                continue
            shot["advanced"]=frame
            key=shot["target"]
            r=self.projectile_receiver(key,frame,shot["release_frame"]) if self.alive(key) else None
            if r:
                dest=stable_enemy_mount(self.tables,r)
                dest=(*dest[:2],dest[2]+r.get("height_offset",0))
                if shot["motion"]=="ParacurveMovement":
                    pos,hit=paracurve_horizontal_step(shot["position"],dest,shot["speed"],self.dt,True)
                else:
                    trace=ordinary_trace_step(shot["position"],dest,effective_dt=self.dt,round_position=True,speed=shot["speed"])
                    pos,hit=trace.position,trace.reached
                shot["position"]=pos
                if not hit and frame<shot["expiry"]:
                    continue
                if not self.invincible(key,frame):
                    self.impact(frame,r,shot)
            self.flights.remove(shot)
        if before_birth is not None:
            return
        for shot in self.pending[:]:
            if frame!=shot["release_frame"]:
                continue
            self.pending.remove(shot)
            key=shot["target"]
            r=self.world.by_id(key,frame) if self.alive(key) else None
            if r is None:
                continue
            shot["position"],_=stationary_cast_mounts(self.tables,self.uid,shot["clock"],self.placement,r,self.view,
                                                       caster_face_sign=shot["sign"])
            prefab=self.ev["projectiles"][shot["projectile"]]["logic"]
            mover=_movement(prefab)
            body=next(c["fields"] for c in prefab["components"] if c["script"]=="SimpleProjectile")
            shot.update(motion=mover["script"],speed=mover["fields"]["_speed"],expiry=frame+self.ticks(body["_lifeTime"]))
            self.flights.append(shot)
            self.log("projectile_release",frame,target=key,projectile=shot["projectile"])

    def invincible(self,key,frame):
        flags=self.world.wave.flags
        return bool(flags and "INVINCIBLE" in flags.at(key,frame))

    def resistance(self,key,frame):
        flags=self.world.wave.flags
        ratio=.5 if flags and "STATUS_RESISTANCE" in flags.at(key,frame) else 1.
        for span in self.request.get("enemy_status_resistance",()):
            if span["target"]==key and span["start_frame"]<=frame<=span["end_frame"]:
                ratio=span["remaining_ratio"]
        return ratio

    def control(self,key,frame,ratio=0):
        if key not in self.walks and key in self.world.positions[frame]:
            self.walks[key]=self.world.start_walk(self.tables,key,frame,ratio)
        elif key in self.walks and ratio:
            walk=self.walks[key]
            walk.slow=movement_cap(move_attribute(walk.route["base_move_speed"],ratio_delta=ratio),walk.route["level_move_multiplier"])

    def movement(self,key,frame):
        return False,False,0

    def update_walks(self,frame):
        for key,walk in tuple(self.walks.items()):
            paused,slowed,ratio=self.movement(key,frame)
            blocked=any(key in b.manager.blocked for b in self.blockers.values())
            if self.alive(key):
                walk=self.world.step_walk(self.tables,walk,key,frame,ratio,slowed=slowed,paused=paused or blocked)
            if walk and self.alive(key):
                self.walks[key]=walk
            else:
                del self.walks[key]
        if self.blockers:
            hp={k:s.hp for k,s in self.states.items()}
            for uid,b in self.blockers.items():
                for key,candidate in b.candidates.items():
                    flags=self.world.wave.flags.at(key,frame) if self.world.wave.flags else ()
                    candidate["block_free"]=bool(set(flags).intersection(("BLOCK_FREE","LEVITATE","DISAPPEARED"))) or self.unblockable(key,frame)
                    if candidate["block_free"] and key in b.manager.blocked:
                        b.manager.release(key)
                        b.stable.pop(key,None)
                        b.tweens.pop(key,None)
                        self.melee_clocks.pop((uid,key),None)
                        self.log("release_block",frame,target=key,holder=uid)
                    candidate["blocked_elsewhere"]=any(key in o.manager.blocked for u,o in self.blockers.items() if u!=uid)
                before=set(b.manager.blocked)
                b.update(frame,self.world.stats,hp,self.events,positions=self.world.positions[frame])
                for key in b.manager.blocked:
                    if key not in before:
                        self.control(key,frame)
                        self.melee_clocks[uid,key]=(frame+1,None)
                        if self.world.incoming and key in self.world.incoming.enemies:
                            self.world.incoming.enemies[key]["shot"]=None
                    self.world.positions[frame][key]=b.candidates[key]["position"]
                    if key in self.walks:
                        self.walks[key].position=b.candidates[key]["position"]
                    self.blocked_attack(frame,uid,key)
            for key,row in self.world.stats.items():
                row["blocked"]=any(key in b.manager.blocked for b in self.blockers.values())

    def unblockable(self,key,frame):
        return False

    def blocked_attack(self,frame,uid,key):
        w=self.world
        unit=self.units[uid]
        if unit["hp"]<=HP_EPSILON:
            return
        enemy=w.stats[key]["enemy_key"]
        if enemy not in self.melee_profiles:
            self.melee_profiles[enemy]=blocked_enemy_attack(self.tables,w.wave,w.incoming,key)
        delay,period,power,magical=self.melee_profiles[enemy]
        ready,hit=self.melee_clocks[uid,key]
        if frame==ready:
            self.melee_clocks[uid,key]=frame+period,frame+delay
        if frame!=hit:
            return
        if magical:
            packet=ordinary_arts_hit(attack_raw=power,resistance_raw=w.receiver.resistances[uid],hp_raw=unit["hp"])
        else:
            packet=ordinary_physical_hit(attack_raw=power,defense_raw=w.receiver.defenses[uid],hp_raw=unit["hp"])
        loss=packet.damage_raw
        if uid==self.uid:
            applied=self.receive(frame,key,loss,"MAGICAL" if magical else "PHYSICAL")
            if applied is not None:
                loss=applied
            unit["hp"]=w.hp
        else:
            unit["hp"]=packet.hp_after_raw
        self.log("blocked_enemy_hit",frame,source=key,holder=uid,damage_q32=loss,holder_hp_q32=unit["hp"])
        if w.incoming and uid in w.incoming.units:
            w.incoming.units[uid]["hp"]=unit["hp"]
        if unit["hp"]<=HP_EPSILON:
            self.orders.leave(frame,uid,manual=False)
            if uid==self.uid:
                w.remove_owner(frame,self.events,"operator_defeated")
                self.clear_owner()
            else:
                self.log("operator_defeated",frame,operator_id=uid)
                if w.incoming:
                    w.incoming.withdraw(uid)

    def sync_cards(self,frame):
        w=self.world
        self.orders.advance(frame,self.events)
        for uid in self.units.keys()-self.orders.placed.keys():
            if self.units[uid].get("summoner"):
                continue
            self.units.pop(uid)
            b=self.blockers.pop(uid,None)
            if b:
                for k in b.manager.blocked:
                    w.stats[k]["blocked"]=False
            if w.incoming:
                w.incoming.withdraw(uid)
        for uid,place in self.orders.placed.items():
            if uid in self.units:
                continue
            panel=w.cards[uid]["panel_attributes"]
            unit=dict(placement=place,hp=_raw_f32(panel["maxHp"]),maximum=_raw_f32(panel["maxHp"]))
            unit["healable"]=not any(7 in (b.get("attributes") or {}).get("abnormalFlags",())
                                      for b in passive_buffs(self.tables,"characters",uid))
            shape=self.tables._component_fields("characters",uid,("m_Radius",),"ranged_ally_collision",component_type="CircleCollider2D")
            unit["radius"]=shape["m_Radius"]
            self.units[uid]=unit
            if w.incoming:
                w.incoming.units[uid]=dict(w.unit_templates[uid],placement=place.copy(),hp=unit["hp"])
                w.incoming.move_holder(uid,place["row"],place["col"])
            capacity=int(self.tables.read("excel/character_table.json")[uid]["phases"][w.cards[uid]["elite"]]["attributesKeyFrames"][-1]["data"]["blockCnt"])
            if capacity and w.scene.tile(place["row"],place["col"])["heightType"]=="LOWLAND":
                candidates=[dict(r,row=0,col=0,instance_uid=w.births[k]["order"],motion_mode="FLY" if r.get("airborne") else "WALK",
                                 special_condition_passed=not r.get("block_free",False)) for k,r in w.stats.items()]
                self.blockers[uid]=StationaryBlockSearch(self.tables,dict(position=(place["row"],place["col"]),
                    capacity=capacity,mode="WALK",first_search_frame=frame+1),candidates)
            if uid==self.uid:
                self.deploy(frame,place)
        for action in self.orders.orders.get(frame,()):
            uid=action["operator_id"]
            if action.get("kind")=="move_ally" and uid in self.units and uid!=self.uid:
                place=self.units[uid]["placement"]
                self.orders.field.occupied.discard((place["row"],place["col"]))
                place.update(row=action["row"],col=action["col"])
                self.orders.field.occupied.add((place["row"],place["col"]))
                if w.incoming:
                    w.incoming.move_holder(uid,place["row"],place["col"])
                if uid in self.blockers:
                    self.blockers[uid].manager.position=(place["row"],place["col"])

    def receive(self,frame,source,loss,damage_type):
        self.world.hp=max(0,self.world.hp-loss)

    def tick(self,frame):
        pass

    def button(self,frame,action):
        pass

    def after_frame(self,frame):
        pass

    def before_incoming(self,frame):
        pass

    def player_action(self,frame,action):
        if not self.world.present:
            return
        if action.get("kind")=="skill":
            self.button(frame,action)
        elif action.get("kind")=="withdraw":
            self.withdraw(frame)

    def run(self):
        w=self.world
        reason="horizon"
        for frame in range(self.request["end_frame"]+1):
            self.sync_cards(frame)
            actions=[a for a in self.orders.orders.get(frame,()) if a["operator_id"]==self.uid]
            for a in actions:
                if a.get("kind")=="servant_withdraw" and a.get("phase")=="before_tick":
                    self.player_action(frame,a)
            for event in self.buff_events.get(frame,()):
                key=event["target"]
                if key not in self.receiver_buffs:
                    receiver=self.receiver_buffs[key]=ReceiverBuffs(self.tables,self.rng)
                    self.states[key].on_take_ordinary_damage=receiver.damage
                self.receiver_buffs[key].attach(event)
            for k,state in self.states.items():
                if w.births[k]["frame"]<=frame and self.alive(k):
                    before=len(state.events)
                    state.tick(frame)
                    for event in state.events[before:]:
                        self.events.append(dict(event,target=k))
            w.allied_impacts(frame,self.states,self.events,arts_modifier=getattr(self,"ally_magic",None))
            for hit in self.corrections.get((frame,"before_attack_search"),()):
                self.states[hit["target"]].apply_buildup(frame,hit["element"],hit["amount_q32"])
            self.before_incoming(frame)
            w.receive(frame,self.states,self.events,owner_damage=self.receive,taunt=getattr(self,"taunts",None))
            if w.incoming:
                for uid,unit in self.units.items():
                    live=w.incoming.units.get(uid)
                    unit["hp"]=live["hp"] if live else 0
                    if not live:
                        self.orders.leave(frame,uid,manual=False)
                        if uid==self.uid and self.generation is not None:
                            self.clear_owner()
            for event in self.ally_heals.get(frame,()):
                uid=event["target"]
                unit=self.units.get(uid)
                if unit and unit["hp"]>HP_EPSILON and unit["healable"]:
                    amount=_raw_f32(event["amount"])
                    restored=min(amount,unit["maximum"]-unit["hp"])
                    unit["hp"]+=restored
                    if uid==self.uid:
                        w.hp=unit["hp"]
                    if w.incoming:
                        w.incoming.units[uid]["hp"]=unit["hp"]
                    self.log("ally_heal",frame,target=uid,displayed_heal_q32=amount,restored_q32=restored)
            self.update_walks(frame)
            if w.incoming:
                w.incoming.blocked_sources={key for b in self.blockers.values() for key in b.manager.blocked}
            if w.settle_exits(frame,self.alive) is not None:
                reason="stage_defeat"
                break
            self.tick(frame)
            for hit in self.corrections.get((frame,"after_attack_search"),()):
                self.states[hit["target"]].apply_buildup(frame,hit["element"],hit["amount_q32"])
            for a in actions:
                if a.get("kind")!="skill" and a.get("phase")=="before_coroutines":
                    self.player_action(frame,a)
            self.rng.advance_spawn_offsets(self.spawn_counts.get(frame,0))
            self.after_frame(frame)
            for a in actions:
                if a.get("kind")=="servant_withdraw" and a.get("phase")=="before_tick":
                    continue
                if a.get("kind")=="skill" or a.get("phase")!="before_coroutines":
                    self.player_action(frame,a)
            if w.settled(frame,self.states,self.events):
                reason="stage_victory_projected"
                break
        return w.stage_result(dict(stage=self.stage["code"],deployment=self.placement,operator_id=self.uid,skill=self.slot,
            module=self.card["module"],module_level=self.card["module_level"],events=self.events,through_frame=frame,
            stop=reason,status=reason,source=self.tables.source,remaining_hp_q32={k:s.hp for k,s in self.states.items()},
            sp=self.sp.points if self.sp else None,source_hp_q32=w.hp,rng=dict(seed=self.request.get("battle_random_seed",0),draws=self.rng.draws)))

    def withdraw(self,frame):
        self.world.remove_owner(frame,self.events,"withdraw")
        self.orders.leave(frame,self.uid,manual=True)
        self.clear_owner()

    @classmethod
    def stage_run(cls,tables,request):
        return run_with_phalanx_waves(lambda t,r,s:cls(t,r,s).run(),tables,request)
