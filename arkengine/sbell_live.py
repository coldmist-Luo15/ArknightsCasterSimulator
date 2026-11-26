"""Live snow listeners and the original checkpoints of their moving receivers."""
from collections import defaultdict
from math import floor

from .blocking import StationaryBlockSearch, enemy_block_profile
from .caster_prefabs import _prefab
from .damage import ordinary_physical_hit
from .enemy_speed import movement_cap,scaled_move_attribute
from .fear_navigation import GroundFearNavigation
from .fixed import ONE,mul
from .stage_route_cursor import ControlledRoute
from .attack_range import circle_in_range
from .forced_motion import step_push
from .mystic_cycle import baked_key_sample
from .phalanx_skills import _talent
from .phalanx_wave import ResumedGroundWalk
from .sbell_snow import SnowTileContact
from .skill_recovery import READY_EPSILON
from .stage_channel import UnsupportedChannelScene
from .volley_damage import _raw_f32


class SbellSnowWave:
    def __init__(self,battle,wave,base,events,defeated):
        self.tables,self.card,self.wave=battle.tables,battle.card,wave
        self.base,self.events,self.defeated=base,events,defeated
        self.routes,self.contacts,self.motions,self.attracted={},{},{},{}
        self.cold,self.frozen=defaultdict(list),{}
        self.blocked=set()
        self.navigation=None
        self.incoming=None
        self.ineligible={}
        self.status_spans=()
        self.ratios={}
        self.step=_raw_f32(self.tables.fixed_timestep())
        self.snow_slow=_raw_f32(_talent(self.card,0,"1")["move_speed"])
        self.resistances={k:d["resistance_q32"] for k,d in wave.attrs.items()}
        table=self.tables.read("buff_table.json")
        delta,=(a["value"] for a in table["c2e_freeze"]["attributes"]["attributeModifiers"]
                 if a["attributeType"]=="MAGIC_RESISTANCE" and a["formulaItem"]=="ADDITION")
        self.frozen_res=_raw_f32(delta)
        cold=self.tables.read("battle/buff_template_data.json")["c2e_cold"]["eventToActions"]
        if (cold["ON_BUFF_ENABLE"][-3]["_buffKeys"]!=["c2e_cold"] or
                cold["ON_BUFF_TRIGGER"][0]["_buffKey"]!="c2e_freeze[holder]"):
            raise ValueError("寒冷的相互触发或冻结持有者配置已变化")
        self.leave_cold=next((v["value"] for v in self.card["skill_row"]["blackboard"] if v["key"]=="talent@cold"),0)

    def route(self,key,frame):
        if key not in self.routes:
            try:
                route=ResumedGroundWalk(self.tables,self.wave,key,frame,0,already_retimed=True,live_checkpoints=True)
            except UnsupportedChannelScene:
                route=ControlledRoute(self.tables,self.wave,key,frame,0)
            if self.incoming:
                self.incoming.enemies[key]["rerouted"]=True
            self.routes[key]=route
            self.wave.escapes.pop(key,None)
        return self.routes[key]

    def set_frozen(self,key,frame,until):
        self.frozen[key]=max(until,self.frozen.get(key,0))
        self.wave.attrs[key]["resistance_q32"]=self.resistances[key]+self.frozen_res
        self.route(key,frame)
        if self.incoming:
            self.incoming.stun(key,self.frozen[key])

    def resist_ratio(self,key,frame):
        ratio=.5 if self.wave.flags and "STATUS_RESISTANCE" in self.wave.flags.at(key,frame) else 1.
        for span in self.status_spans:
            if span["target"]==key and span["start_frame"]<=frame<=span["end_frame"]:
                ratio=span["remaining_ratio"]
        return ratio

    def status_ticks(self,key,frame,seconds):
        return max(1,(_raw_f32(seconds*self.resist_ratio(key,frame))-READY_EPSILON)//self.step+1)

    def apply_cold(self,key,frame,seconds):
        until=frame+self.status_ticks(key,frame,seconds)
        waiting=self.cold[key]
        waiting[:]=[f for f in waiting if f>frame]
        unit=self.wave.scene.enemies[self.wave.births[key]["enemy_key"]]
        if waiting and not unit["attributes"]["frozenImmune"]["m_value"]:
            until=max(until,waiting.pop(0))
            self.set_frozen(key,frame,until)
            self.events.append(dict(kind="snow_frozen",frame=frame,enemy=key,expiry_frame=until))
        else:
            waiting.append(until)
            self.events.append(dict(kind="snow_cold",frame=frame,enemy=key,expiry_frame=until))

    def apply_push(self,key,frame,profile,motion):
        self.route(key,frame)
        self.motions[key]=profile,motion

    def get_navigation(self):
        if self.navigation is None:
            self.navigation=GroundFearNavigation(self.wave.scene,static_terrain=True)
        return self.navigation

    def apply_attraction(self,key,frame,selection):
        self.get_navigation()
        self.route(key,frame)
        self.blocked.discard(key)
        if self.incoming:
            self.incoming.enemies[key]["shot"]=None
        self.attracted[key]=selection["destination"],selection["expiry_frame"]

    def advance_resistance(self,frame):
        for key in self.cold.keys() | self.frozen.keys() | self.attracted.keys():
            ratio=self.resist_ratio(key,frame)
            prior=self.ratios.get(key,self.resist_ratio(key,frame-1))
            self.ratios[key]=ratio
            if ratio==prior:
                continue
            def expiry(end):
                ticks=max(0,end-frame)
                return frame+round(ticks*ratio/max(prior,.001))
            if key in self.cold:
                self.cold[key][:]=[expiry(end) for end in self.cold[key] if end>frame]
            if key in self.frozen and self.frozen[key]>frame:
                old=self.frozen[key]
                self.frozen[key]=expiry(old)
                if self.incoming and self.incoming.enemies[key]["hold_until"]==old:
                    self.incoming.enemies[key]["hold_until"]=self.frozen[key]
            if key in self.attracted:
                goal,until=self.attracted[key]
                self.attracted[key]=goal,expiry(until)

    def advance(self,frame):
        wave=self.wave
        for key,route in tuple(self.routes.items()):
            if wave.attrs[key]["hp_q32"]<=0 or key in wave.escapes and wave.escapes[key]<frame:
                wave.active[frame].discard(key)
                del self.routes[key]
                self.contacts.pop(key,None)
                self.motions.pop(key,None)
                continue
            forced=None
            if key in self.motions:
                profile,prior=self.motions[key]
                motion=step_push(self.tables,profile,prior,frame=frame,scene=wave.scene,continuous=True)
                if motion.velocity==(0.,0.) and prior.velocity!=(0.,0.) and motion.active:
                    self.events.append(dict(kind="snow_push_wall_contact",frame=frame,enemy=key,position=motion.position))
                forced=motion.position
                tile=tuple(floor(v+.5) for v in forced)
                if wave.scene.tile(*tile)["tileKey"]=="tile_hole":
                    wave.attrs[key]["hp_q32"]=0
                    self.defeated.append(key)
                    self.events.append(dict(kind="enemy_fell_in_hole",frame=frame,enemy=key,tile=tile))
                    wave.active[frame].discard(key)
                    continue
                self.events.append(dict(kind="snow_push_motion",frame=frame,enemy=key,position=forced,
                                        velocity=motion.velocity,active=motion.active))
                if motion.active:
                    self.motions[key]=profile,motion
                else:
                    del self.motions[key]
                    self.get_navigation()
            goal=None
            if key in self.attracted:
                goal,until=self.attracted[key]
                if frame>until:
                    self.events.append(dict(kind="snow_attraction_ended",frame=frame,enemy=key,original_goal=route.goal))
                    del self.attracted[key]
                    goal=None
            frozen=self.frozen.get(key,0)
            if frozen and frame>=frozen:
                wave.attrs[key]["resistance_q32"]=self.resistances[key]
                del self.frozen[key]
            contact=self.contacts.get(key)
            ratio=ONE+(contact.layers*self.snow_slow if contact else 0)
            speed=movement_cap(scaled_move_attribute(route.route["base_move_speed"],ratio),route.route["level_move_multiplier"])
            nav=self.get_navigation() if wave.attrs[key]["ground"] else None
            held=self.incoming and self.incoming.holding(key,frame)
            options=dict(speed_cap=speed,paused=frame<frozen or key in self.blocked or bool(held),
                         temporary_goal=goal,forced_position=forced,navigation=nav)
            try:
                walking=route.advance(wave,frame,False,**options)
            except UnsupportedChannelScene:
                route=self.routes[key]=ControlledRoute(self.tables,wave,key,frame,0,prior=route)
                wave.positions[frame][key]=route.position
                wave.active[frame].add(key)
                walking=True
            if not walking:
                del self.routes[key]
                wave.escapes[key]=frame
        return None

    def aura_members(self,frame):
        w=self.wave
        return sorted(k for k in w.active[frame] if w.attrs[k]["hp_q32"]>0 and
                      circle_in_range(*w.positions[frame][k],w.radii[k],w.cells,box_query=True))

    def contacts_at(self,frame,snow,old_snow,active,*,after_objects=False):
        wave=self.wave
        module=self.card["module_info"]
        members=None
        def aura_count():
            nonlocal members
            if members is None:
                members=self.aura_members(frame) if module and module["typeIcon"]=="plx-y" else []
            return len(members)
        def apply(key,cell,packet):
            self.events.append(dict(packet,enemy=key,tile=cell,aura_members=members))
            if "hp_after_q32" in packet:
                wave.attrs[key]["hp_q32"]=packet["hp_after_q32"]
                if not packet["hp_after_q32"]:
                    self.defeated.append(key)
                    self.contacts.pop(key,None)

        for key,birth in wave.births.items():
            target=wave.attrs[key]
            if target["hp_q32"]<=0 or not target["ground"]:
                continue
            pos=wave.positions[frame].get(key) if key in wave.active[frame] else None
            contact=self.contacts.get(key)
            cell=tuple(floor(v+.5) for v in pos) if pos is not None else None
            flags=wave.flags.at(key,frame) if wave.flags else ()
            excluded=bool(set(flags).intersection(("TARGET_FREE","INVISIBLE","INVINCIBLE")))
            if excluded:
                if contact is not None and (cell!=contact.tile or contact.poll.remaining<=self.step):
                    self.contacts.pop(key,None)
                    contact=None
                if contact is None:
                    self.ineligible.setdefault(key,frame)
                    continue
            elif key in self.ineligible:
                if (frame-self.ineligible[key])%15:
                    continue
                self.ineligible.pop(key)
            if contact is None and (cell is None or not snow.get(cell)):
                continue
            if contact is not None:
                layers=(old_snow if after_objects or birth["frame"]<wave.start else snow).get(contact.tile,0)
                for event in contact.tick(frame,target,layers,active,aura_count):
                    apply(key,contact.tile,event)
                if target["hp_q32"]<=0 or cell==contact.tile:
                    continue
                self.contacts.pop(key,None)
                teleport=any(row.get("instance_id")==key and (row.get("command") in ("DISAPPEAR","APPEAR_AT_POS","APPEAR_AT_TILE")
                             or kind=="route_teleport") for kind,row in wave.history.get(frame,()))
                if not teleport:
                    snow.pop(contact.tile,None)
                self.events.append(dict(kind="snow_tile_leave",frame=frame,enemy=key,tile=contact.tile,
                                        permanent=contact.tile in self.base))
                if self.card["skill"]==2 and active and pos is not None and not teleport:
                    self.apply_cold(key,frame,self.leave_cold)
            if excluded or cell is None or not snow.get(cell):
                continue
            self.route(key,frame)
            contact=self.contacts[key]=SnowTileContact(self.tables,self.card,cell,snow[cell])
            apply(key,cell,contact.enter(frame,target,active,aura_count()))


class FrozenSnowGoals:
    """The S2 token blocks ordinary ground enemies and receives their combat attacks."""
    def __init__(self,live):
        self.live=live
        self.tables,self.wave,self.events=live.tables,live.wave,live.events
        self.tokens,self.attack_profiles={},{}
        self.holders={}
        self.taunt={}
        self.world=None
        self.step=live.step

    def create(self,frame,row):
        tile=row["row"],row["col"]
        token=row["token"]
        prefab=_prefab(self.tables,"[uc]tokens",token)
        mode,=(r["fields"] for r in prefab["components"] if r["script"]=="UnitMode")
        if mode["_attack"]["m_PathID"] or not mode["_combat"]["m_PathID"]:
            raise ValueError("冻结保护目标的战斗模式已变化")
        unit=self.tables.read("excel/character_table.json")[token]
        attrs=unit["phases"][self.live.card["elite"]]["attributesKeyFrames"][0]["data"]
        targets=[]
        for index,(key,birth) in enumerate(self.wave.births.items()):
            enemy=self.wave.scene.enemies[birth["enemy_key"]]
            if not self.wave.attrs[key]["ground"]:
                continue
            asset=enemy["prefabKey"]["m_value"] or birth["enemy_key"]
            condition=enemy_block_profile(self.tables,asset)["special_condition"]
            if condition["_type"]:
                self.wave.assumptions.append(dict(kind="rule_applied",frame=max(frame,birth["frame"]),target=key,
                    rule="ordinary_block_capacity_with_special_enemy_condition",condition=condition))
            targets.append(dict(id=key,row=0,col=0,instance_uid=index,motion_mode="WALK",prefab=asset,
                                special_condition_passed=True))
        setup=dict(position=tile,capacity=attrs["blockCnt"],mode="WALK",first_search_frame=frame+1)
        self.tokens[tile]=dict(search=StationaryBlockSearch(self.tables,setup,targets),
                               hp=_raw_f32(attrs["maxHp"]),defense=_raw_f32(attrs["def"]),res=_raw_f32(attrs["magicResistance"]),attacks={})

        world=self.world
        if world and world.incoming:
            from .baked_mount import mount_parents,transform_point
            from .opening_scene import bundle_environment
            incoming=world.incoming
            holder=f"frozen_goal_{tile[0]}_{tile[1]}"
            self.holders[holder]=tile
            self.taunt[holder]=-1
            self.tokens[tile]["holder"]=holder
            shape=next(c["fields"] for c in prefab["components"] if c["type"]=="CircleCollider2D" and c["enabled"])
            raw,_=self.tables.verified_bundle(prefab["source"]["member"])
            root=bundle_environment(raw).container[prefab["source"]["container"]].deref()
            hit=next(c for c in prefab["components"] if c["path"].endswith("/HitPoint") and c["type"]=="Transform")
            transform=root.assets_file.objects[hit["id"]].read_typetree()
            offset=transform_point(tuple(transform["m_LocalPosition"][axis] for axis in "xyz"),
                                   mount_parents(root.assets_file.objects,transform,root.path_id)[:-1])
            state=self.tokens[tile]
            world.defenses[holder],world.resistances[holder]=state["defense"],state["res"]
            incoming.units[holder]=dict(placement=dict(row=tile[0],col=tile[1],frame=frame),order=frame,
                radius=shape["m_Radius"],hp=state["hp"],defense=state["defense"],offset=offset,
                mount=incoming.hit_mount(incoming.tiles[tile],offset))

    def remove(self,tile,frame,cause):
        state=self.tokens.pop(tile)
        for key in tuple(state["search"].manager.blocked):
            state["search"].manager.release(key)
            self.live.blocked.discard(key)
            self.events.append(dict(kind="release_block",frame=frame,target=key,tile=tile,cause=cause))
        holder=state.get("holder")
        if holder:
            self.holders.pop(holder,None)
            self.taunt.pop(holder,None)
            self.world.incoming.withdraw(holder)

    def damaged(self,tile,source,frame,after,amount):
        state=self.tokens[tile]
        state["hp"]=after
        if state.get("holder"):
            self.world.incoming.units[state["holder"]]["hp"]=after
        self.events.append(dict(kind="frozen_goal_hit",frame=frame,enemy=source,tile=tile,
                                damage_q32=amount,hp_after_q32=after))
        if after<=0:
            self.events.append(dict(kind="frozen_goal_destroyed",frame=frame,tile=tile))
            self.remove(tile,frame,"goal_destroyed")

    def _attack(self,key):
        enemy=self.wave.births[key]["enemy_key"]
        if enemy not in self.attack_profiles:
            self.attack_profiles[enemy]=blocked_enemy_attack(self.tables,self.wave,self.live.incoming,key)
        return self.attack_profiles[enemy]

    def advance(self,frame,frozen):
        wave=self.wave
        if not self.tokens:
            return
        hp={key:v["hp_q32"] for key,v in wave.attrs.items()}
        positions={key:wave.positions[frame][key] for key in wave.active[frame] if hp[key]>0 and wave.attrs[key]["ground"]}
        for tile,state in tuple(self.tokens.items()):
            search=state["search"]
            targets={k:dict(blocked=k in search.manager.blocked) for k in wave.births}
            choices={k:v for k,v in positions.items() if (k not in self.live.blocked or k in search.manager.blocked)
                     and k not in self.live.motions and k not in self.live.attracted}
            for key,candidate in search.candidates.items():
                flags=wave.flags.at(key,frame) if wave.flags else ()
                candidate["block_free"]=bool(set(flags).intersection(("BLOCK_FREE","LEVITATE","DISAPPEARED")))
                if candidate["block_free"]:
                    choices.pop(key,None)
            callbacks=[]
            search.update(frame,targets,hp,callbacks,positions=choices)
            for event in callbacks:
                key=event["target"]
                if event["kind"]=="register_block":
                    self.live.route(key,frame)
                    self.live.blocked.add(key)
                    state["attacks"][key]=frame+1,None
                else:
                    self.live.blocked.discard(key)
                    state["attacks"].pop(key,None)
                self.events.append(dict(event,tile=tile))
            for key in tuple(search.manager.blocked):
                if key in self.live.attracted or key in self.live.motions or key not in wave.active[frame] or search.candidates[key]["block_free"]:
                    search.manager.release(key)
                    self.live.blocked.discard(key)
                    search.stable.pop(key,None)
                    search.tweens.pop(key,None)
                    state["attacks"].pop(key,None)
                    self.events.append(dict(kind="release_block",frame=frame,tile=tile,target=key))
                    continue
                wave.positions[frame][key]=self.live.routes[key].position=targets[key]["position"]
                if self.live.frozen.get(key,0)>frame:
                    state["attacks"][key]=frame+1,None
                    continue
                if key not in state["attacks"]:
                    state["attacks"][key]=frame+1,None
                ready,impact=state["attacks"][key]
                if frame==ready:
                    delay,period,_,_=self._attack(key)
                    if any(t>frame for t in self.live.cold.get(key,())):
                        aspd=wave.scene.enemies[wave.births[key]["enemy_key"]]["attributes"]["attackSpeed"]["m_value"]
                        delay,period=(round(v*aspd/max(10,aspd-30)) for v in (delay,period))
                    state["attacks"][key]=frame+period,frame+delay
                if frame!=impact:
                    continue
                _,_,power,magical=self._attack(key)
                if magical:
                    from .damage import ordinary_arts_hit
                    hit=ordinary_arts_hit(attack_raw=power,resistance_raw=state["res"],hp_raw=state["hp"])
                else:
                    hit=ordinary_physical_hit(attack_raw=power,defense_raw=state["defense"],hp_raw=state["hp"])
                self.damaged(tile,key,frame,hit.hp_after_raw,hit.damage_raw)
                if tile not in self.tokens:
                    frozen.discard(tile)
                    break

def blocked_enemy_attack(tables,wave,incoming,key):
    birth=wave.births[key]
    unit=wave.scene.enemies[birth["enemy_key"]]
    step=_raw_f32(tables.fixed_timestep())
    prefab=unit["prefabKey"]["m_value"] or birth["enemy_key"]
    parts=(_prefab(tables,"enemies",prefab) or {}).get("components",())
    ability=next((c["fields"] for c in parts if c["path"]=="Default/Body/Combat" and c["script"]=="MeleeAttack"),None)
    attrs=unit["attributes"]
    interval=_raw_f32(attrs["baseAttackTime"]["m_value"]*100/max(10,attrs["attackSpeed"]["m_value"]))
    period=max(1,(interval-READY_EPSILON+step)//step)
    attack=_raw_f32(attrs["atk"]["m_value"])
    ranged=incoming.enemies.get(key,{}).get("profile") if incoming else None
    if ability is None and ranged:
        info=ranged["wait"],ranged["period"],ranged["attack"],ranged.get("magical",False)
    else:
        ability=ability or {}
        view=next((c["fields"] for c in parts if c["script"]=="SingleSpineAnimator"),{})
        clip=next((a for a in view.get("_animations",()) if a["animKey"]==ability.get("_animKey")),None)
        delay=max(1,period//2)
        if clip and ability.get("_waitForAttackEvent"):
            baked=tables.read(f"bakemuzzledata/enemy/{prefab}.json")["bakedAnimDatas"]["Default"]
            action=baked["bakedEvents"].get(clip["animName"],{})
            if "OnAttack" in action.get("events",()):
                key_time=action["eventTime"][action["events"].index("OnAttack")]
                delay,_=baked_key_sample(key_time,clip["speed"],tables.fixed_timestep())
        else:
            wave.assumptions.append(dict(kind="rule_applied",frame=wave.births[key]["frame"],target=key,
                rule="ordinary_blocked_enemy_attack_mid_interval",enemy=birth["enemy_key"]))
        info=delay,period,mul(attack,_raw_f32(ability.get("_atkScale",1))),ability.get("_damageType")==2
    return info
