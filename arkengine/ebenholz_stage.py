"""Ebenholz's S3 attack, projectile and receiver callbacks on the stage clock."""
from math import floor
from collections import defaultdict

from dataclasses import replace
from .attack_range import circle_in_range,range_cells
from .attack_switch import attack_switches
from .baked_mount import baked_position, transform_point
from .battle_outcome import ordinary_battle_outcome
from .damage import HP_EPSILON, ordinary_arts_hit
from .enemy_elements import EnemyElements
from .fixed import mul
from .health_bounds import ordinary_life_points
from .lin_incoming import ordinary_ranged, HighlandCrossbows
from .lin_interactions import EBENHOLZ
from .mode_replay import _simulate,_attack_frames
from .mystic_cycle import ordinary_profile
from .mystic_damage import delta_projectile_impact, mystic_attack_inputs
from .mystic_projectiles import _release_layout, _settled_cast_facing
from .mystic_timing import ebenholz_timing
from .phalanx_wave import PhalanxWave, run_with_phalanx_waves
from .projectile_scene import enemy_hit_mount, stage_tile_positions, projectile_profiles
from .skill_recovery import deploy_skill
from .stage_channel import prepare_channel
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32, _talent_distances


class EbenholzBattle:
    def __init__(self, battle, request, events, *, wave=None):
        self.battle, self.events = battle, events
        self.tables, self.card = battle.tables, battle.card
        self.end = request["end_frame"]
        self.wave = wave or PhalanxWave(battle, self.end, allow_later_waves=True, absolute_route_times=True,
                                native_first_walk=True, allow_partial_movement=True)
        self.update_range()
        self.levels = {k:battle.scene.enemy_level(b["enemy_key"]) for k,b in self.wave.births.items()}
        self.hp = _raw_f32(self.card["panel_attributes"]["maxHp"])
        self.dt = self.tables.fixed_timestep()
        self.initial_life = self.life = battle.scene.level["options"]["maxLifePoint"]
        self.deaths, self.exits, self.escapes = {}, {}, []
        self.receivers, self.shots = {}, []
        self.damage = {m:mystic_attack_inputs(self.tables,self.card,mode=m,live_element_receiver=True)
                       for m in ("Default","S3")}
        self.delta = (self.card["module_info"] or {}).get("typeIcon")=="msc-d"
        self.tiles = stage_tile_positions(self.tables,battle.scene)
        self.mounts = {}
        self.fixed_penetration = None
        self.face = "back" if battle.deploy["direction"]=="UP" else "front"
        self.sign = -1 if battle.deploy["direction"]=="LEFT" else 1
        self.positions = {}
        self.mode = "Default"
        self.incoming = None
        if wave is None and any(ordinary_ranged(battle.scene,b["enemy_key"]) for b in self.wave.births.values()):
            self.incoming = HighlandCrossbows(self.tables,self.wave,[battle.deploy],{EBENHOLZ:self.card},allow_thrower=True)
            self.incoming.events = events
            self.incoming.attack_ratio=lambda key:self.receivers[key].atk_ratio if key in self.receivers else 0
        self.schedule = battle.opening["wave_schedule"]
        self.roster = {e["instance_id"] for e in self.schedule["events"] if e["kind"]=="spawn"}
        self.victory_at = None

    def update_range(self):
        grid=self.battle.scene.level["mapData"]["map"]
        self.cells=range_cells(self.card["base_range"],self.battle.deploy,len(grid),len(grid[0]))

    @property
    def owner_alive(self):
        return self.hp>HP_EPSILON

    def flags_at(self,key,frame):
        return self.wave.flags.at(key,frame)

    def valid(self,key,frame):
        return (key in self.wave.active[frame] and key not in self.deaths and key not in self.exits
                and self.wave.targetable(key,frame))

    def candidates(self,frame,late):
        targets = self.wave.search(frame,cells=self.cells,observer_born=self.battle.deploy["frame"]) if not late else [
            k for k,e in self.wave.snapshot(frame).items() if circle_in_range(e["row"],e["col"],e["radius"],self.cells,box_query=True)]
        return targets

    def priority(self,keys,frame,late):
        return dict(target=self.wave.attack_order(keys,frame,after_objects=late,observer_born=self.battle.deploy["frame"])[0])

    def release(self,mode,key,frame,normal,extra,cast):
        layout=self.layouts[mode]
        owner=self.battle.deploy
        start=cast["start"]
        samples={f:self.wave.positions.get(f,{}).get(key) for f in (start-1,start)}
        aim=_settled_cast_facing(owner,samples,[start],frame,self.dt,layout["facing_seconds"] or 0,self.face,self.sign)
        if aim and len(aim[1])==1:
            self.face,self.sign=aim[0][0],aim[1][0]
        else:
            row,col=samples[start] or self.positions[key]
            dy,dx=row-owner["row"],col-owner["col"]
            if abs(dy)>abs(dx)+.25*(dx*dx+dy*dy)**.5:
                self.face="back" if dy>0 else "front"
            if abs(dx)>.2:
                self.sign=1 if dx>0 else -1
            self.events.append(dict(kind="rule_applied",frame=frame,target=key,rule="cast_start_direction_during_turn"))
        bake=self.tables.read(self.profile["bake_file"])["bakedAnimDatas"][self.face.title()]
        tracks=bake["bakedMPDatas"][layout["clip"]]
        base=self.tiles[owner["row"],owner["col"]]["position"]
        parents=[*self.profile["mount_parents"][self.face],self.profile["graphic_parent"]]
        for kind,count in (("main",1),("stored",normal),("extra",extra)):
            projectile,mounts=layout[kind]
            for mount in mounts[:count]:
                x,y,z=baked_position(tracks[str(mount)],cast["animation_time"],self.dt)
                offset=transform_point((self.sign*x,y,self.sign*z),parents)
                point=tuple(base[a]+v for a,v in zip("xyz",offset))
                self.shots.append(dict(target=key,packet=kind,projectile=projectile,mode=mode,
                                       position=point,born=frame))

    def receiver_mount(self,key,frame):
        birth=self.wave.births[key]
        enemy=birth["enemy_key"]
        prefab=self.battle.scene.enemies[enemy]["prefabKey"]["m_value"] or enemy
        if prefab not in self.mounts:
            asset=enemy_hit_mount(self.tables,prefab)
            if asset:
                dx,dy,dz=transform_point(baked_position(asset["hit_track"],0,self.dt),asset["parents"])
                self.mounts[prefab]=dx,dy,dz-asset["height"]
            else:
                self.mounts[prefab]=(0,0,0)
                self.events.append(dict(kind="rule_applied",frame=frame,target=key,rule="route_center_for_variable_hit_mount"))
        row,col=self.positions[key]
        dx,dy,dz=self.mounts[prefab]
        tile=self.tiles.get((floor(row+.5),floor(col+.5)))
        ground=tile["position"]["z"]-tile["locate_height_offset"] if tile else 0.
        return col+dx,row+dy,dz+ground

    def element_events(self,key,state,frame,start):
        self.events.extend(dict(e,target=key) for e in state.events[start:])
        self.wave.attrs[key]["hp_q32"]=state.hp
        if state.hp<=HP_EPSILON:
            self.deaths[key]=frame

    def arts(self,key,frame,attack,scale,source):
        enemy=self.wave.attrs[key]
        if not scale or enemy["hp_q32"]<=HP_EPSILON or "INVINCIBLE" in self.flags_at(key,frame):
            return
        packet=ordinary_arts_hit(attack_raw=mul(attack,_raw_f32(scale)),hp_raw=enemy["hp_q32"],
                                 resistance_raw=enemy["resistance_q32"],fixed_res_penetration_raw=self.fixed_penetration() if self.fixed_penetration else 0)
        enemy["hp_q32"]=packet.hp_after_raw
        self.events.append(dict(kind="arts_damage",frame=frame,target=key,source=source,
                                damage_q32=packet.damage_raw,hp_after_q32=packet.hp_after_raw))
        if not packet.alive_after:
            self.deaths[key]=frame

    def impact(self,shot,frame):
        key=shot["target"]
        mode=self.mode
        pos=self.positions[key]
        spec=self.damage[mode]
        peers={k:p for k,p in self.positions.items() if k!=key and k not in self.deaths and k not in self.exits
               and self.wave.births[k]["frame"]<frame}
        self.events.append(dict(kind="projectile_impact",frame=frame,target=key,packet=shot["packet"],
                                launch_frame=shot["born"],launch_mode=shot["mode"],source_mode=mode))
        if "INVINCIBLE" in self.flags_at(key,frame):
            return
        if self.delta:
            if key not in self.receivers:
                attrs=self.battle.scene.enemies[self.wave.births[key]["enemy_key"]]["attributes"]
                self.receivers[key]=EnemyElements.from_scene_enemy(self.tables,dict(level=self.levels[key],
                    hp_q32=self.wave.attrs[key]["hp_q32"],ep_resistance_q32=_raw_f32(attrs["epResistance"]["m_value"]),
                    ep_damage_resistance_q32=_raw_f32(attrs["epDamageResistance"]["m_value"])))
            state=self.receivers[key]
            offset=len(state.events)
            delta_projectile_impact(self.tables,self.card,state,frame,dict(mode=mode,launch_mode=shot["mode"],
                projectile=shot["packet"],target_center=pos,other_living_centers=list(peers.values())),
                max(0,self.wave.attrs[key]["resistance_q32"]-self.fixed_penetration()) if self.fixed_penetration else self.wave.attrs[key]["resistance_q32"])
            self.element_events(key,state,frame,offset)
        else:
            distances={k:_talent_distances(pos,p,spec["talent_radius"]) for k,p in peers.items()}
            if not any(d[1] for d in distances.values()):
                self.arts(key,frame,spec["attack_q32"],spec["isolated_scale"],"ebnhlz_t_2")
            elif spec["splash_scale"]:
                for peer,distance in distances.items():
                    if distance[2] and self.valid(peer,frame):
                        self.arts(peer,frame,spec["attack_q32"],spec["splash_scale"],"ebnhlz_t_2_splash")
            self.arts(key,frame,spec["attack_q32"],self.damage[shot["mode"]][shot["packet"]+"_scale"],shot["projectile"])

    def outcome(self,frame):
        case=dict(through_frame=frame,exit_coverage=True,exits=self.escapes,unresolved=[],finishes=[
            dict(instance=k,first_possible_frame=f,finished_by_frame=f,scheduler_killed_by_frame=f)
            for k,f in self.deaths.items()])
        life=ordinary_life_points(self.initial_life,[case],frame)
        return ordinary_battle_outcome(self.battle.scene,self.schedule,[case],life,self.dt,use_stage_objective=True)

    def advance(self,frame):
        wave=self.wave
        if self.incoming and frame>=self.battle.deploy["frame"]:
            self.incoming.advance(frame,{})
            for source,_,loss in self.incoming.hits(frame,{},defense_q32={EBENHOLZ:_raw_f32(self.card["panel_attributes"]["def"])},
                    resistance_q32={EBENHOLZ:_raw_f32(self.card["panel_attributes"]["magicResistance"])}):
                self.hp=max(0,self.hp-loss)
                self.incoming.units[EBENHOLZ]["hp"]=self.hp
                self.events.append(dict(kind="incoming_hit",frame=frame,source=source,hp_q32=self.hp))
                if not self.owner_alive:
                    self.events.append(dict(kind="operator_defeated",frame=frame,operator_id=EBENHOLZ))
        self.positions.update({k:p for k,p in wave.positions.get(frame,{}).items() if p is not None})
        for key,tick in wave.escapes.items():
            if tick!=frame or key in self.deaths:
                continue
            loss=self.battle.scene.enemies[wave.births[key]["enemy_key"]]["lifePointReduce"]["m_value"]
            self.life=max(0,self.life-loss)
            self.exits[key]=frame
            self.events.append(dict(kind="enemy_escaped",frame=frame,enemy=key,remaining_life=self.life))
            self.escapes.append(dict(instance=key,frame=frame,scheduler_release_window=[frame,frame],
                                     outcomes=["reached_exit"],life_loss_min=loss,life_loss_max=loss))
            if self.life==0:
                return "stage_defeat"
        self.deliver(frame)
        if self.victory_at is None and not self.schedule["pending"] and self.roster==self.deaths.keys()|self.exits.keys():
            window=self.outcome(self.end).get("finish_game_projection",{})
            if window:
                self.victory_at=window["frames"][1]
        if frame==self.victory_at:
            return "stage_victory_projected"


    def deliver(self,frame):
        for key,state in self.receivers.items():
            if key not in self.exits and key not in self.deaths:
                state.hp=self.wave.attrs[key]["hp_q32"]
                start=len(state.events)
                state.tick(frame)
                self.element_events(key,state,frame,start)
        for shot in tuple(self.shots):
            key=shot["target"]
            if key in self.deaths or key in self.exits:
                self.shots.remove(shot)
                continue
            move=ordinary_trace_step(shot["position"],self.receiver_mount(key,frame),effective_dt=self.dt,
                                     round_position=True,speed=self.speeds[shot["projectile"]])
            shot["position"]=move.position
            if move.reached or (frame-shot["born"])*self.dt>=self.lifetimes[shot["projectile"]]:
                self.shots.remove(shot)
                self.impact(shot,frame)


def run_ebenholz_stage(tables,request):
    return run_with_phalanx_waves(_run_ebenholz_stage,tables,request)


def _run_ebenholz_stage(tables,request,releases):
    owner,*buttons=request["actions"]
    first,end=owner["frame"],request["end_frame"]
    opening={k:v for k,v in request.items() if k!="battle_random_seed"}
    battle=prepare_channel(tables,dict(opening,actions=[{k:v for k,v in owner.items() if k!="kind"}]),
        operator=EBENHOLZ,skill=3,allow_idle=True,allow_no_module=True,contact_checked_during_replay=True,
        completing_waves=releases,allow_partial_movement=True)
    events=[dict(kind="deploy",frame=first,operator_id=EBENHOLZ)]
    world=EbenholzBattle(battle,request,events)
    profile,switch,changes,hit_modes,skill_events=_attack_schedule(world,buttons)
    def advance(frame):
        world.mode=hit_modes.get(frame,world.mode) if world.owner_alive else "Default"
        return world.advance(frame)
    begin=min(first,min((b["frame"] for b in world.wave.births.values()),default=first))
    stop=None
    for frame in range(begin,first+1):
        stop=advance(frame)
        if stop:
            break
    if stop or first==end:
        result=dict(events=[],last_frame=frame,stop=stop or "horizon",storage=dict(normal=0,extra=0))
    else:
        result=_simulate(switch,changes,{}, {},world.levels,None,world.release,first+1,
                         first+1+profile["birth_updates"],end,profile["step_q32"],None,projectile_tick=advance,live=world)
    through=result["last_frame"]
    died=next((e["frame"] for e in events if e["kind"]=="operator_defeated"),end+1)
    events.extend(e for e in skill_events if e["frame"]<died)
    events.extend(result["events"])
    events.extend(world.wave.applied_rules(through,world.deaths|world.exits))
    return dict(stage=battle.opening["code"],operator_id=EBENHOLZ,skill=3,source=tables.source,through_frame=through,
                events=sorted((e for e in events if e["frame"]<=through),key=lambda e:e["frame"]),stop=result["stop"],
                storage=result["storage"],owner_hp_q32=world.hp,remaining_life=world.life,
                spawns=world.wave.spawns(through),battle_outcome=world.outcome(through),
                enemy_hp_q32={k:v["hp_q32"] for k,v in world.wave.attrs.items() if world.wave.births[k]["frame"]<=through},
                wave_schedule_pending=world.schedule["pending"],scheduler_release_frames={**world.deaths,**world.exits})


def _attack_schedule(world,buttons):
    battle,tables=world.battle,world.tables
    first,end=battle.deploy["frame"],world.end
    timing=ebenholz_timing(tables,battle.card)
    profile=world.profile=ordinary_profile(tables,battle.card,timing)
    gauge,sp=deploy_skill(tables,battle.card,first)
    # The skill gauge is independent of attacks and target deaths. Resolve the
    # button phases once, then apply each transition to the running attack FSM.
    class SkillClock:
        def emit(self,kind,**fields):
            if kind!="natural_sp":
                skill_events.append(dict(kind=kind,frame=self.frame,**fields))
    clock=SkillClock()
    skill_events=[]
    by_frame=defaultdict(list)
    for button in buttons:
        by_frame[button["frame"]].append(button)
    for clock.frame in range(first,end+1):
        if clock.frame>first:
            gauge.tick(clock)
        for button in by_frame[clock.frame]:
            if button.get("phase","input")=="input":
                gauge.press(tables,clock)
        gauge.after_input(clock)
        gauge.after_fixed(clock)
        for button in by_frame[clock.frame]:
            if button.get("phase")=="after_tick":
                gauge.press(tables,clock,phase="after_tick")
    switch=attack_switches(tables,battle.card,dict(profile=profile,scenarios=[]),timing,sp,include_ordinary=True)
    changes={(b["effect_frame"],"tick" if b["effect_phase"]=="input" else b["effect_phase"]):
             (b["destination_mode"],b["input"]) for b in switch["buttons"]}
    for frame in switch["natural_returns_to_default"]:
        phase="unity_fixed_resume" if frame in switch.get("natural_after_fixed",()) else "tick"
        changes[frame,phase]=("Default","natural_end")
    world.layouts={m:_release_layout(tables,profile,m) for m in switch["modes"]}
    keys={layout[k][0] for layout in world.layouts.values() for k in ("main","stored","extra")}
    specs={k:next(iter(v.values())) for k,v in projectile_profiles(tables,sorted(keys)).items()}
    world.speeds={k:v["movement"]["_speed"] for k,v in specs.items()}
    world.lifetimes={k:v["lifecycle"]["_lifeTime"] for k,v in specs.items()}
    hit_modes={frame+1:dest for (frame,_),(dest,_) in sorted(changes.items(),
        key=lambda item:(item[0][0],("tick","unity_fixed_resume","after_tick").index(item[0][1])))}
    return profile,switch,changes,hit_modes,skill_events


class AlliedEbenholz:
    """Drive the same attack coroutine against the squad's shared wave and HP."""
    def __init__(self,battle,request,wave,deploy,card,events,deaths):
        self.world=world=EbenholzBattle(replace(battle,deploy=deploy,card=card),request,events,wave=wave)
        world.deaths=deaths
        self.events=events
        self.first=deploy["frame"]
        self.end=request["end_frame"]
        buttons=[a for a in request["actions"] if a["operator_id"]==EBENHOLZ and a.get("kind")=="skill"]
        profile,switch,changes,self.hit_modes,skill_events=_attack_schedule(world,buttons)
        self.skills=defaultdict(list)
        for event in skill_events:
            self.skills[event["frame"]].append(event)
        self.clock=_attack_frames(switch,changes,{}, {},world.levels,None,world.release,self.first+1,
            self.first+1+profile["birth_updates"],self.end,profile["step_q32"],None,live=world)
        self.next=next(self.clock,None)
        self.index=0

    def step(self,frame,incoming):
        if frame<self.first:
            return
        world=self.world
        if incoming:
            receiver=incoming.units.get(EBENHOLZ)
            world.hp=receiver["hp"] if receiver else 0
        world.mode=self.hit_modes.get(frame,world.mode) if world.owner_alive else "Default"
        world.positions.update({k:p for k,p in world.wave.positions[frame].items() if p is not None})
        world.exits.update({k:f for k,f in world.wave.escapes.items() if f==frame})
        world.deliver(frame)
        if world.owner_alive:
            self.events.extend(self.skills.get(frame,()))
        while self.next and self.next[0]==frame:
            log=self.next[1]
            self.next=next(self.clock,None)
            self.events.extend(dict(e,operator_id=EBENHOLZ) for e in log[self.index:])
            self.index=len(log)
