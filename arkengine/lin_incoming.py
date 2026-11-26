"""V079 ordinary hostile projectiles against Lin and the recipients of her barrier."""
from collections import defaultdict
from math import floor,hypot

from .animation_assets import read_spine_events
from .baked_mount import baked_position,mount_parents,transform_point
from .bombardier_attack import _operator_hit_offset
from .caster_prefabs import _prefab
from .damage import HP_EPSILON,ordinary_physical_hit,ordinary_arts_hit
from .fixed import ONE,mul
from .mystic_cycle import baked_key_sample
from .mystic_timing import fixed_wait_frames
from .opening_scene import bundle_environment
from .projectile_scene import projectile_profiles,stage_tile_positions
from .skill_recovery import READY_EPSILON
from .stage_channel import COCKTAIL_THROWER,UnsupportedChannelScene
from .trace_motion import ordinary_trace_step,paracurve_horizontal_step
from .volley_damage import _raw_f32


CROSSBOWS={"enemy_1003_ncbow","enemy_1003_ncbow_2"}


def ordinary_ranged(scene,key):
    prefab=scene.enemies[key]["prefabKey"]["m_value"] or key
    return prefab in CROSSBOWS|{COCKTAIL_THROWER,"enemy_1011_wizard","enemy_1011_wizard_2","enemy_1019_jshoot","enemy_1023_jmage"}


def _ranged_attack(tables,unit,enemy):
    prefab=unit["prefabKey"]["m_value"] or enemy
    asset=_prefab(tables,"enemies",prefab)
    attack,=(c["fields"] for c in asset["components"] if c["script"]=="RangedAttack")
    animator,=(c["fields"] for c in asset["components"] if c["script"]=="SingleSpineAnimator")
    clip=next(a for a in animator["_animations"] if a["animKey"]==attack["_animKey"])
    bake=tables.read(f"bakemuzzledata/enemy/{prefab}.json")["bakedAnimDatas"]["Default"]
    event=bake["bakedEvents"][clip["animName"]]
    track=bake["bakedMPDatas"][clip["animName"]][str(attack["_mountPointType"])]
    dt=tables.fixed_timestep()
    step=int(dt*ONE)
    attrs=unit["attributes"]
    interval=_raw_f32(attrs["baseAttackTime"]["m_value"]*100/attrs["attackSpeed"]["m_value"])
    source=asset["source"]
    raw,_=tables.verified_bundle(source["member"])
    root=bundle_environment(raw).container[source["container"]].deref()
    objects=root.assets_file.objects
    mount=objects[animator["_muzzleTransform"]["m_PathID"]].read_typetree()
    parents=mount_parents(objects,mount,root.path_id)[:-1]
    curved=prefab==COCKTAIL_THROWER
    shot=attack["_projectileKey"]
    if curved:
        duration,speed,lifetime=_cocktail_assets(tables,asset,objects,prefab,clip,event,shot)
    else:
        duration=(track["frameCount"]-1)/(30*track["bakedStepInterval"])
        parts=_prefab(tables,"[uc]projectiles",shot)["components"]
        mover=next(c["fields"] for c in parts if c["script"]=="AdvancedMovement")
        life=next(c["fields"] for c in parts if c["script"]=="SimpleProjectile")
        speed,lifetime=mover["_speed"],life["_lifeTime"]
    event_seconds=event["eventTime"][event["events"].index("OnAttack")]
    speed_ratio=max(clip["speed"],duration/(interval/ONE))
    wait,seconds=baked_key_sample(event_seconds,speed_ratio,dt)
    return dict(radius=unit["rangeRadius"]["m_value"],attack=mul(_raw_f32(attrs["atk"]["m_value"]),_raw_f32(attack["_atkScale"])),
                wait=wait,duration=fixed_wait_frames(duration/speed_ratio/dt),period=(interval-READY_EPSILON+step)//step,
                speed=speed,life_ticks=fixed_wait_frames(lifetime/dt)+1,curved=curved,
                magical=attack["_damageType"]==2,event_kind="cocktail" if curved else "enemy_spell" if attack["_damageType"]==2 else "crossbow",local=baked_position(track,seconds,dt),parents=parents)


def _cocktail_assets(tables,asset,objects,prefab,clip,event,shot):
    cache=("lin_cocktail_assets",prefab)
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    skeleton,=(c for c in asset["components"] if c["script"]=="SkeletonAnimation")
    pointer=skeleton["fields"]["skeletonDataAsset"]
    owner=objects[skeleton["id"]]
    external=owner.assets_file.externals[pointer["m_FileID"]-1].path.rsplit("/",1)[-1]
    raw,source=tables.verified_bundle("refs/arts/enm_art_base_0.ab")
    data,=(o for o in bundle_environment(raw).objects if o.path_id==pointer["m_PathID"] and o.assets_file.name==external)
    ref=data.read_typetree()["skeletonJSON"]
    text=data.assets_file.objects[ref["m_PathID"]].read_typetree()
    decoded=read_spine_events(text["m_Script"])
    native_clip,=(a for a in decoded["animations"] if a["name"]==clip["animName"])
    if (not pointer["m_FileID"] or ref["m_FileID"] or text["m_Name"]!=prefab+".skel"
            or [e["name"] for e in native_clip["events"]]!=event["events"]
            or abs(native_clip["events"][0]["time"]-event["eventTime"][0])>1e-6):
        raise ValueError("鸡尾酒投掷者的攻击动画与烘焙关键帧不符")
    projectile=_prefab(tables,"[uc]projectiles",shot)
    parts={c["script"]:c["fields"] for c in projectile["components"] if c["type"]=="MonoBehaviour" and c["enabled"]}
    mover,life=parts["ParacurveMovement"],parts["SimpleProjectile"]
    if (set(parts)!={"ParacurveMovement","SimpleProjectile","EffectBehaviour"}
            or any(mover[k] for k in ("_delayTime","_delayAfterReached","_onlyReachedInTime","_comeBack",
                "_useRandomHeight","_randomInverseParacurve","_flyToPredefinedLocation","_flyToTargetLocationOnly",
                "_useSourceDirection","_useTargetDirection","_useStartToTarget","_awayFromSourceAsDirection",
                "_useSourceDirectionRandomArcAngleStart","_useSourceToTargetRandomArcAngleStart"))
            or not life["_alwaysHitTraceTargetInTheEnd"] or mover["_speed"]<=0
            or any(life[k] for k in ("_randomDelayToBorn","_stopWhenSourceInvalid","_containsChildrenBehaviours",
                "_fireProjectileReachedEvent","_alwaysReachInTheEnd","_alwaysHitTraceTargetWhenReached"))
            or any(life["_actionController"].values())):
        raise ValueError("鸡尾酒弹体的抛物线追踪或单靶命中配置已变化")
    tables.source["lin_cocktail_attack"]=dict(animation=source,skeleton_sha256=decoded["skeletonSha256"],
                                              projectile=projectile["source"])
    result=native_clip["duration"],mover["_speed"],life["_lifeTime"]
    tables._prefab_data[cache]=result
    return result


class HighlandCrossbows:
    def __init__(self,tables,wave,placements,cards,*,live_projectiles=False,allow_thrower=False,track_stuns=False):
        self.attack_ratio=None
        self.snow_control=None
        self.wave=wave
        self.dt=tables.fixed_timestep()
        self.events=[]
        self.pending=defaultdict(list)
        self.enemies={}
        self.units={}
        self.live_projectiles=live_projectiles
        self.arrows=[]
        self.moving_holders=set()
        self.tracking=[]
        profiles={}
        for key,birth in wave.births.items():
            enemy=birth["enemy_key"]
            if enemy not in CROSSBOWS and not (allow_thrower and ordinary_ranged(wave.scene,enemy)):
                continue
            if enemy not in profiles:
                profiles[enemy]=_ranged_attack(tables,wave.scene.enemies[enemy],enemy)
            self.enemies[key]=dict(profile=profiles[enemy],delay=0,ready=0,hold_until=0,shot=None)
        if not self.enemies and not track_stuns:
            return
        if not live_projectiles:
            for key in wave.births:
                self.enemies.setdefault(key,dict(profile=None,delay=0,ready=0,hold_until=0,shot=None))
        self.tiles=stage_tile_positions(tables,wave.scene)
        for order,placement in enumerate(placements):
            key=placement["operator_id"]
            offset=_operator_hit_offset(tables,key,cards[key].get("battle_skin","defaultskin"))
            tile=self.tiles[placement["row"],placement["col"]]
            if offset is None:
                offset=(0.,0.,0.)
                wave.assumptions.append(dict(kind="rule_applied",frame=placement["frame"],target=key,
                    rule="tile_center_for_variable_operator_hit_mount"))
            shape=tables._component_fields("characters",key,("m_Radius","m_Offset","m_Enabled"),
                                           "lin_crossbow_receiver_radius",component_type="CircleCollider2D")
            if any(shape["m_Offset"].values()) or not shape["m_Enabled"]:
                raise UnsupportedChannelScene("弩手需要原生部署碰撞体")
            panel=cards[key]["panel_attributes"]
            self.units[key]=dict(placement=placement,order=order,radius=shape["m_Radius"],
                hp=_raw_f32(panel["maxHp"]),defense=_raw_f32(panel["def"]),
                offset=offset,mount=self.hit_mount(tile,offset))
        self.positions={f:dict(p) for f,p in wave.positions.items()}
        self.active={f:set(keys) for f,keys in wave.active.items()}
        self.history=dict(wave.history)
        self.exits={key:wave.escapes.get(key) for key in self.enemies}
        first_deploy=min(row["frame"] for row in placements)
        for key,frame in self.exits.items():
            if frame is not None and frame>=first_deploy:
                wave.escapes.pop(key)

    @staticmethod
    def hit_mount(tile,offset):
        x,y,z=(tile["position"][axis]+delta for axis,delta in zip("xyz",offset))
        return x,y,z-tile["locate_height_offset"]

    def move_holder(self,key,row,col):
        unit=self.units[key]
        unit["placement"].update(row=row,col=col)
        unit["mount"]=self.hit_mount(self.tiles[row,col],unit["offset"])

    def _fire(self,frame,key,source,position):
        target=source["shot"]
        source["shot"]=None
        aim=self.units[target]
        if aim["hp"]<=HP_EPSILON:
            return
        row,col=position
        tile=self.tiles.get((floor(row+.5),floor(col+.5)))
        height=tile["position"]["z"]-tile["locate_height_offset"] if tile else 0.
        profile=source["profile"]
        x,y,z=profile["local"]
        facing=1 if aim["placement"]["col"]>=col else -1
        shifted=transform_point((facing*x,y,facing*z),profile["parents"])
        point=tuple(v+d for v,d in zip((col,row,height),shifted))
        attack=profile["attack"]
        if self.attack_ratio:
            attack=mul(attack,ONE+self.attack_ratio(key))
        if self.live_projectiles:
            self.arrows.append(dict(born=frame,source=key,target=target,attack=attack,speed=profile["speed"],
                                    position=point,scale=1,profile=profile))
            self.events.append(dict(kind=profile["event_kind"]+"_release",frame=frame,source=key,holder=target))
            return
        if target in self.moving_holders:
            self.tracking.append(dict(source=key,target=target,profile=profile,attack=attack,position=point,born=frame))
            self.events.append(dict(kind=profile["event_kind"]+"_release",frame=frame,source=key,holder=target))
            return
        for tick in range(1,profile["life_ticks"]+1):
            if profile["curved"]:
                point,reached=paracurve_horizontal_step(point,aim["mount"],profile["speed"],self.dt,True)
            else:
                move=ordinary_trace_step(point,aim["mount"],effective_dt=self.dt,round_position=True,speed=profile["speed"])
                point,reached=move.position,move.reached
            if reached or tick==profile["life_ticks"]:
                self.pending[frame+tick].append((key,target,attack))
                self.events.append(dict(kind=profile["event_kind"]+"_release",frame=frame,source=key,holder=target,
                                        impact_frame=frame+tick))
                return

    def advance(self,frame,taunt,*,before_fire=None,keys=None,interrupt_delay=None):
        for key in self.enemies if keys is None else keys:
            state=self.enemies[key]
            if self.wave.attrs[key]["hp_q32"]<=HP_EPSILON:
                self.wave.active[frame].discard(key)
                continue
            cursor=frame-state["delay"]
            prior=cursor-1
            rerouted=state.get("rerouted",False)
            position=(self.wave.positions.get(frame-1,{}) if rerouted else self.positions.get(prior,{})).get(key)
            if before_fire and frame==state.get("interrupt_at") and state["shot"] is not None:
                before_fire(frame,key,position)
            if frame==state.get("release") and state["shot"] is not None:
                if before_fire is None or before_fire(frame,key,position):
                    self._fire(frame,key,state,position)
                else:
                    state["shot"]=None
            active=self.wave.active.get(frame-1,()) if rerouted else self.active.get(prior,())
            control=self.snow_control
            disabled=control and (key in control.attracted or key in control.motions or key in control.blocked or control.frozen.get(key,0)>frame)
            disabled=disabled or key in getattr(self,"blocked_sources",()) or frame<=max(state.get("fear_until",-1),state.get("sleep_until",-1))
            if not disabled and state["profile"] and frame>=state["hold_until"] and frame>=state["ready"] and key in active:
                state["ready"]=frame+3
                targets=[]
                for holder,target in self.units.items():
                    placed=target["placement"]
                    if (placed["frame"]<frame and target["hp"]>HP_EPSILON and
                            hypot(placed["row"]-position[0],placed["col"]-position[1])<=state["profile"]["radius"]+target["radius"]):
                        targets.append((taunt.get(holder,0),placed["frame"],target["order"],holder))
                if targets:
                    state["shot"]=max(targets)[-1]
                    profile=state["profile"]
                    factor=1.
                    if control and any(t>frame for t in control.cold.get(key,())):
                        aspd=self.wave.scene.enemies[self.wave.births[key]["enemy_key"]]["attributes"]["attackSpeed"]["m_value"]
                        factor=aspd/max(10,aspd-30)
                    state.update(release=frame+round(profile["wait"]*factor),hold_until=frame+round(profile["duration"]*factor)+2,
                                 ready=frame+round(profile["period"]*factor))
                    if interrupt_delay:
                        delay=interrupt_delay(key)
                        state["interrupt_at"]=frame+delay if delay is not None and frame+delay<state["release"] else None
                    self.events.append(dict(kind=profile["event_kind"]+"_start",frame=frame,source=key,holder=state["shot"]))
            if frame<state["hold_until"]:
                state["delay"]+=1
                cursor-=1
            if rerouted:
                self.wave.active[frame].discard(key)
                if key in active:
                    self.wave.active[frame].add(key)
                    self.wave.positions[frame][key]=position
                continue
            self.wave.history[frame]=[(kind,row) for kind,row in self.wave.history.get(frame,()) if row["instance_id"]!=key]
            self.wave.history[frame].extend((kind,row) for kind,row in self.history.get(cursor,()) if row["instance_id"]==key)
            if cursor==self.exits[key]:
                self.wave.escapes[key]=frame
            if key in self.active.get(cursor,()):
                self.wave.active[frame].add(key)
                self.wave.positions[frame][key]=self.positions[cursor][key]
            else:
                self.wave.active[frame].discard(key)

    def prepare_retiming(self,key,frame):
        state=self.enemies[key]
        cursor=frame+1-state["delay"]
        if self.holding(key,frame) or key not in self.active.get(cursor,()):
            return False
        self.wave.positions[frame+1][key]=self.positions[cursor][key]
        return True

    def holding(self,key,frame):
        return frame<self.enemies[key]["hold_until"]

    def advance_arrows(self,frame,scale_at):
        for arrow in tuple(self.arrows):
            if arrow["born"]>=frame:
                continue
            holder=arrow["target"]
            if self.units[holder]["hp"]<=HP_EPSILON:
                self.arrows.remove(arrow)
                continue
            scale=scale_at(arrow["position"])
            if scale!=arrow["scale"]:
                arrow["scale"]=scale
                self.events.append(dict(kind="hostile_projectile_speed",frame=frame,source=arrow["source"],
                                        born=arrow["born"],factor=scale))
            profile=arrow["profile"]
            if profile["curved"]:
                point,reached=paracurve_horizontal_step(arrow["position"],self.units[holder]["mount"],arrow["speed"]*scale,self.dt,True)
            else:
                moved=ordinary_trace_step(arrow["position"],self.units[holder]["mount"],effective_dt=self.dt,
                                          round_position=True,speed=arrow["speed"]*scale)
                point,reached=moved.position,moved.reached
            arrow["position"]=point
            if reached or frame-arrow["born"]>=profile["life_ticks"]:
                self.arrows.remove(arrow)
                self.pending[frame].append((arrow["source"],holder,arrow["attack"]))

    def eliminate(self,inside):
        deleted=[shot for shot in self.arrows if inside(shot["position"])]
        for shot in deleted:
            self.arrows.remove(shot)
        return [dict(source=shot["source"],born=shot["born"]) for shot in deleted]

    def withdraw(self, holder):
        self.units.pop(holder,None)
        for enemy in self.enemies.values():
            if enemy["shot"]==holder:
                enemy["shot"]=None
        for pending in self.pending.values():
            pending[:]=[shot for shot in pending if shot[1]!=holder]
        self.arrows[:]=[shot for shot in self.arrows if shot["target"]!=holder]
        self.tracking[:]=[shot for shot in self.tracking if shot["target"]!=holder]

    def stun(self,key,until):
        state=self.enemies[key]
        state["shot"]=None
        state["hold_until"]=max(state["hold_until"] if state["profile"] is None else 0,until)

    def hits(self,frame,defense_bonus,*,defense_q32=None,resistance_q32=None):
        for source,holder,attack in self.pending.pop(frame,()):
            unit=self.units.get(holder)
            if unit and unit["hp"]>HP_EPSILON:
                defense=(defense_q32 or {}).get(holder)
                if defense is None:
                    defense=mul(unit["defense"],ONE+_raw_f32(defense_bonus.get(holder,2)))
                if self.enemies[source]["profile"].get("magical"):
                    damage=ordinary_arts_hit(attack_raw=attack,resistance_raw=resistance_q32[holder],hp_raw=unit["hp"]).damage_raw
                else:
                    damage=ordinary_physical_hit(attack_raw=attack,defense_raw=defense,hp_raw=unit["hp"]).damage_raw
                yield source,holder,damage

    def damage_events(self,frame,defenses,resistances,external):
        for shot in tuple(self.tracking):
            if shot["born"]==frame:
                continue
            holder=shot["target"]
            aim=self.units[holder]["mount"]
            profile=shot["profile"]
            if profile["curved"]:
                point,reached=paracurve_horizontal_step(shot["position"],aim,profile["speed"],self.dt,True)
            else:
                moved=ordinary_trace_step(shot["position"],aim,effective_dt=self.dt,round_position=True,speed=profile["speed"])
                point,reached=moved.position,moved.reached
            shot["position"]=point
            if reached or frame-shot["born"]>=profile["life_ticks"]:
                self.pending[frame].append((shot["source"],holder,shot["attack"]))
                self.tracking.remove(shot)
        options=dict(defense_q32=defenses)
        if any((e["profile"] or {}).get("magical") for e in self.enemies.values()):
            options["resistance_q32"]=resistances
        for source,holder,amount in self.hits(frame,{},**options):
            profile=self.enemies[source]["profile"]
            yield source,holder,amount,profile["event_kind"],dict(damage_type="MAGICAL" if profile.get("magical") else "PHYSICAL")
        for hit in external.get(frame,()):
            holder=hit["holder"]
            unit=self.units.get(holder)
            if not unit or unit["placement"]["frame"]>frame or unit["hp"]<=HP_EPSILON:
                continue
            if "incoming_after_defense" in hit:
                amount=_raw_f32(hit["incoming_after_defense"])
            else:
                attack=_raw_f32(hit["attack"])
                if hit["damage_type"]=="PHYSICAL":
                    amount=ordinary_physical_hit(attack_raw=attack,defense_raw=defenses[holder],hp_raw=unit["hp"]).damage_raw
                elif hit["damage_type"]=="MAGICAL":
                    amount=ordinary_arts_hit(attack_raw=attack,resistance_raw=resistances[holder],hp_raw=unit["hp"]).damage_raw
                else:
                    amount=attack
            yield hit["source"],holder,amount,"incoming",hit


def incoming_by_frame(request):
    frames=defaultdict(list)
    for hit in request.get("incoming_hits",()):
        frames[hit["frame"]].append(hit)
    return frames
