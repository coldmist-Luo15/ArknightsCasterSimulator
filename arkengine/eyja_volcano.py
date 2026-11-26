"""Volcano's attack timeout, repeated deterministic selections and live parabolic projectiles."""
from collections import Counter
from math import hypot

from .animation_assets import read_skin_actions
from .attack_range import range_cells
from .baked_mount import baked_position,single,transform_point
from .caster_equipment_traits import _trait_values
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .core_caster_skills import volcano_selection
from .damage import HP_EPSILON,ordinary_arts_hit
from .target_priority import native_box_candidates
from .elemental_projectiles import moving_ranged_shot, stationary_cast_mounts
from .fixed import ONE,mul
from .mystic_timing import fixed_wait_frames
from .mystic_projectiles import _settled_cast_facing
from .trace_motion import paracurve_horizontal_step
from .volley_damage import _raw_f32


def _origins(tables,card,ev,place,enemy,age):
    """Enclose the active channel and idle muzzle in map XY."""
    direction = place["direction"]
    deployment_view = "Back" if direction == "UP" else "Front"
    duration = card["skill_row"]["duration"]
    cache = ("volcano_muzzle_bounds", card["operator_id"], direction, duration)
    if cache not in tables._prefab_data:
        uid=card["operator_id"]
        config, = (r["fields"] for r in ev["skill"]["components"] if r["script"] == "ThreePartChannelingAnimation")
        if any(config[k] for k in ("_overrideUpAnimation", "_overrideDownAnimation", "_faceToFront", "_faceToDefault")):
            raise ValueError("火山的分视图动画或面向规则已经变化")
        channel = config["_default"]
        actions=["Idle",*channel.values()]
        skin=read_skin_actions(tables,"chararts/"+uid+".ab","defaultskin",actions,[(2,"muzzlaTransform")],allow_unbound=True)
        if direction in ("UP", "DOWN"):
            sign = skin["default_horizontal_face_sign"]
        else:
            sign = 1 if direction == "RIGHT" else -1
        graph=tables._component_fields("characters",uid,("m_LocalPosition","m_LocalRotation","m_LocalScale"),
                                      "character_graphic",component_type="Transform",children=("Graphic",))
        parent=dict(position=graph["m_LocalPosition"],rotation=graph["m_LocalRotation"],scale=graph["m_LocalScale"])
        facing = deployment_view
        view = skin["views"][facing.lower()]
        missing = [action for action in channel.values() if view["actions"][action].get("status")]
        if facing == "Back" and len(missing) == len(channel):
            # PlayAnimationInternal turns to the opposite view when only it has the requested clip.
            facing = "Front"
            view = skin["views"]["front"]
        if any(view["actions"][action].get("status") for action in actions):
            raise ValueError("火山各动作使用了不同的有效面向，需要逐段切换")
        parents = [*view["mount_parents"][2], parent]
        tracks = tables.read(ev["animations"]["file"])["bakedAnimDatas"][facing]["bakedMPDatas"]
        corners=[]
        loop_corners=[]
        for action in actions:
            clip = view["actions"][action]
            if clip["events"] or clip["speed"]!=1:
                raise ValueError("火山动作出现独立攻击事件或速率变化")
            track=tracks[clip["clip"]]["2"]
            step=int(tables.fixed_timestep()*ONE)//track["bakedStepInterval"]/ONE
            for i in range(track["frameCount"]):
                x,y,z=baked_position(track,step*i,tables.fixed_timestep())
                offset = transform_point((sign*x, y, sign*z), parents)
                corners.append(offset)
                if action == channel["loopAnim"]:
                    loop_corners.append(offset)
        clips = view["actions"]
        begin,end=(clips[channel[k]]["duration"] for k in ("beginAnim","endAnim"))
        if begin+end>=card["skill_row"]["duration"] or not clips[channel["loopAnim"]]["loop"]:
            raise ValueError("火山三段动作发生压缩或循环方式变化")
        def bounds(points):
            return [(min(x[j] for x in points),max(x[j] for x in points)) for j in range(2)]
        # UpdatePlaybackSpeed 0x3074b40..60 leaves native playback speed at one
        # when both non-loop tracks fit. MoveNext 0x307555c..64 waits for Begin,
        # then 0x3075028 plays Loop. The boundary samples retain the wider enclosure.
        dt=tables.fixed_timestep()
        tables._prefab_data[cache] = (bounds(corners), bounds(loop_corners), fixed_wait_frames(begin/dt)+1,
                                      fixed_wait_frames((duration-end)/dt)-1, sign, facing)
    full, loop, after_begin, before_end, sign, facing = tables._prefab_data[cache]
    bounds=loop if after_begin<age<before_end else full
    # Without a previous attack, a vertical face change retains the prefab's horizontal sign.
    center=(place["col"]+sum(bounds[0])/2,place["row"]+sum(bounds[1])/2)
    radius=hypot(*(b-a for a,b in bounds))/2+1e-5
    _, mount = stationary_cast_mounts(tables, card["operator_id"], dict(action="Idle"), place, enemy,
                                      facing, sample_seconds=0, caster_face_sign=sign)
    return center,radius,mount


def _arrival(tables,card,ev,world,place,enemy,release,until,speed,age):
    pos,error,mount=_origins(tables,card,ev,place,enemy,age)
    dt=tables.fixed_timestep()
    earliest=None
    for f in range(release+1,until+1):
        live=world.by_id(enemy["id"],f,projectile_born=release)
        if live is None:
            break
        target=(mount[0]+live["col"]-enemy["col"],mount[1]+live["row"]-enemy["row"])
        before=hypot(target[0]-pos[0],target[1]-pos[1])
        pos,_=paracurve_horizontal_step(pos,target,speed,dt,True)
        # Moving toward the same goal by a capped distance never increases separation.
        # At these map dimensions, two float/1e-4-rounded stores add less than .0002.
        error=.0001 if before+error<single(speed*dt)-1e-5 else error+.0002
        gap=hypot(target[0]-pos[0],target[1]-pos[1])
        if earliest is None and gap-error<=.100001:
            earliest=f
        if gap+error<.099999:
            return earliest,f
    return earliest,None


def run_volcano(tables,card,ev,event,world,button,sp,talent,events,targets,born_end,stream):
    press,end,place=button["frame"],event["end_frame"],event["placement"]
    hp={key:_raw_f32(row["hp"]) for key,row in world.stats.items()}
    arrivals={}
    def stop(reason,frame,**extra):
        return dict(events=events,through_frame=frame,stop=reason,sp=sp.points,hp_q32=hp,arrival_windows=arrivals,**extra)
    if max(event["rows"],event["cols"]) > 40:
        return stop("requires_volcano_map_precision", press)
    accepted=resolve_cast_start(tables,ev,frame=press,phase="after_tick",sp=sp.maximum,
                                owner_state="born" if press<born_end else "idle")
    if accepted.get("spell_frame") is None:
        return stop(accepted["status"],press)
    mode=accepted["spell_frame"]
    if mode<born_end:
        return stop("requires_volcano_switch_during_birth",mode)
    profile=default_attack(tables,card["operator_id"],1)
    parts=ev["character"]["components"]
    a,=(c["fields"] for c in parts if c["id"]==profile["attack_component"])
    selector,=(c for c in parts if c["id"]==a["_selector"]["m_PathID"])
    projectile=a["_projectileKey"]
    trigger,=(c for c in parts if c["script"]=="SelectorTrigger" and c["game_object"]==selector["game_object"])
    mode_fields,=(c["fields"] for c in parts if c["id"]==profile["mode_component"])
    if (a["_animKey"] or not a["_waitForAttackEvent"] or a["_timeMode"]!=0 or a["_faceToTarget"] or a["_useCachedAtkOnly"] or
            (a["_selectTargetSource"],a["_selectTargetTiming"],a["_alwaysIncludeTarget"])!=(1,0,1) or
            a["_interuptIfTargetDead"] or a["_emitToInputPosWhenTargetIsInvalid"] or a["_allowNoTarget"] or
            mode_fields["_attackTrigger"]["m_PathID"]!=trigger["id"] or
            trigger["fields"]["_keepTarget"] or trigger["fields"]["_minTargetNum"]!=1 or
            selector["script"]!="RandomSelector"):
        raise ValueError("火山的空攻击动作、面向或随机索敌规则已变化")
    movers=set()
    for obj in ev["projectiles"][projectile].values():
        fields={c["script"]:c["fields"] for c in obj["components"] if c["enabled"] and c["active"]}
        movement,life=fields["ParacurveMovement"],fields["SimpleProjectile"]
        if (movement["_delayAfterReached"] or movement["_flyToTargetLocationOnly"] or
                not life["_alwaysHitTraceTargetInTheEnd"] or life["_maxHitNum"]!=1 or
                life["_managedBySource"] or life["_stopWhenSourceInvalid"] or "HitBehaviour" in fields):
            raise ValueError("火山抛物线与单靶命中配置已变化")
        movers.add(movement["_speed"])
    speed,=movers
    bb={r["key"]:r["value"] for r in ev["skill_blackboard"]}
    shape=range_cells(card["skill_range"],place,event["rows"],event["cols"])
    base=card["panel_attributes"]
    interval=single(single(base["baseAttackTime"]+bb["base_attack_time"])*100/(base["attackSpeed"]+talent["attack_speed"]))
    period=fixed_wait_frames(single(interval/tables.fixed_timestep()))
    # The default skin has no OnAttack in its three channel tracks. Native animation
    # behaviour emits entity events 20 and 21, while the empty ranged attack waits for 19.
    expire=press+fixed_wait_frames(card["skill_row"]["duration"]/tables.fixed_timestep())
    power=mul(_raw_f32(base["atk"]),ONE+_raw_f32(talent["caster_attack_bonus"])+_raw_f32(bb["atk"]))
    trait = _trait_values(card)
    pen = _raw_f32(trait.get("magic_resist_penetrate_fixed",0))
    ordinary_power = mul(_raw_f32(base["atk"]), ONE + _raw_f32(talent["caster_attack_bonus"]))
    ordinary_shape = range_cells(card["base_range"], place, event["rows"], event["cols"])
    ordinary_interval = single(single(base["baseAttackTime"])*100/(base["attackSpeed"]+talent["attack_speed"]))
    ordinary_period = fixed_wait_frames(single(ordinary_interval/tables.fixed_timestep()))
    ordinary = default_attack(tables, card["operator_id"])["fields"]
    if ordinary["_useCachedAtkOnly"]:
        raise ValueError("火山以外的普攻改为只读缓存攻击力")
    maximum=int(bb["attack@max_target"])
    poll=mode+1
    continuation=False
    release=None
    locked=[]
    landings={}
    unresolved={}
    ordinary_releases, ordinary_landings = {}, {}
    ordinary_poll = born_end + 1
    idle_face = event["view"].lower()
    previous_face = idle_face
    births=Counter(b["frame"] for b in world.births.values()) if stream and event.get("native_spawn_random") else Counter()

    def select(f,after_objects):
        nonlocal poll,continuation,release,locked
        rows=[v for v in targets(f,shape,after_objects=after_objects) if hp[v["id"]]>HP_EPSILON]
        if not rows:
            poll=f+3
            continuation=False
            return
        ordered=native_box_candidates(world,(v["id"] for v in rows))
        if after_objects and births[f] and len(ordered)>1:
            return "requires_volcano_spawn_and_search_random_order"
        before=stream.draws if stream else 0
        selection=volcano_selection(ordered,maximum,stream)
        locked=selection["targets"]
        events.append(dict(kind="volcano_attack",frame=f,target_set=list(locked),
                           trigger_target=selection["trigger_target"],cast_selection=selection["cast_selection"],
                           eligible_in_uid_order=ordered,stream_draws=[before,stream.draws if stream else 0],
                           interval_ticks=period,rng_draws=stream.draws if stream else 0))
        release=f+period
        # The empty animation waits the full effective interval. Post-delay has
        # zero residual, but WaitForFixedSeconds still yields one update.
        poll=release+1
        continuation=True
    for f in range(event["frame"]+1,end+1):
        if births[f-1]:
            stream.advance_spawn_offsets(births[f-1])
        defeat = world.settle_exits(f-1, lambda k: hp[k] > HP_EPSILON)
        if defeat is not None or world.all_processed(hp):
            return stop("all_enemies_processed" if defeat is None else "stage_defeat",
                        f-1 if defeat is None else defeat)
        # Projectile updates precede end-of-attack coroutine callbacks in this frame.
        # An ordinary idle poll occurs on the owner's object turn, before its projectiles.
        if f < expire and f==poll and not continuation:
            select(f,False)
        if f==mode:
            events.append(dict(kind="volcano_mode",frame=f))
        if f == expire + 1:
            events.append(dict(kind="volcano_mode_ended", frame=expire))
        if f == ordinary_poll:
            rows = [v for v in targets(f, ordinary_shape) if hp[v["id"]] > HP_EPSILON]
            if not rows:
                ordinary_poll = f+3
                previous_face = idle_face
            else:
                enemy = world.select(rows, f) if len(rows)>1 else rows[0]
                key = enemy["id"]
                samples = {t: world.positions[t].get(key) for t in (f-1, f)}
                mount = _settled_cast_facing(place, samples, (f,), f+10, tables.fixed_timestep(), .15, previous_face)
                if not mount or len(mount[1]) != 1:
                    return stop("requires_volcano_default_attack_facing", f-1)
                (previous_face,), _ = mount
                shot = moving_ranged_shot(tables, ev, ordinary, dict(event, view=previous_face.capitalize()), world,
                    enemy, frame=f, interval=ordinary_interval, mode_path=None, through=end, allow_target_exit=True)
                ordinary_releases[shot["release_frame"]] = (key, shot.get("reach_frame"))
                events.append(dict(kind="ordinary_attack", frame=f, target=key, release_frame=shot["release_frame"]))
                ordinary_poll = f+ordinary_period
        for key in unresolved.get(f,()):
            if hp[key]>HP_EPSILON:
                return stop("requires_channel_mount_phase",f-1)
        if f <= press or f >= expire+2:
            sp.advance(int(tables.fixed_timestep()*ONE))
        for kind, lands in (("volcano_hit", landings), ("ordinary_hit", ordinary_landings)):
            for key in lands.get(f, ()):
                if hp[key] <= HP_EPSILON:
                    continue
                target = world.stats[key]
                packet = ordinary_arts_hit(attack_raw=power if mode < f <= expire else ordinary_power,
                                           resistance_raw=_raw_f32(target["res"])-pen, hp_raw=hp[key])
                hp[key] = packet.hp_after_raw
                if (f <= press or f >= expire+2) and target["level"] in ("ELITE", "BOSS"):
                    sp.points = min(sp.maximum, sp.points+int(trait.get("sp",0)))
                events.append(dict(kind=kind, frame=f, receiver=key, damage_q32=packet.damage_raw, hp_q32=hp[key]))
        if f in ordinary_releases:
            key, hit = ordinary_releases.pop(f)
            if hp[key] <= HP_EPSILON or world.by_id(key, f, projectile_born=f) is None:
                return stop("volcano_default_attack_target_invalid_before_release", f-1)
            events.append(dict(kind="projectile_release", frame=f, target=key, projectile=ordinary["_projectileKey"]))
            if hit is not None:
                ordinary_landings.setdefault(hit, []).append(key)
        if f==press:
            if sp.points<sp.cost:
                return stop("insufficient_sp",f)
            sp.points=accepted["sp_after"]
            events.append(dict(kind="volcano_cast",frame=f,sp=sp.points))
            for tick,(key,_) in ordinary_releases.items():
                events.append(dict(kind="ordinary_windup_cancelled",frame=f,target=key,scheduled_release=tick))
            ordinary_releases.clear()
            ordinary_poll=expire+1
        if f >= expire:
            continue
        if f==poll and continuation:
            if reason:=select(f,True):
                return stop(reason,f-1)
        if f==release:
            alive=[v for key in locked if hp[key]>HP_EPSILON and (v:=world.by_id(key,f,projectile_born=f)) is not None]
            if not alive:
                # First spell on a wholly invalid input finishes as TARGET_DEAD,
                # clears the ordinary attack cooldown and re-searches this frame.
                events.append(dict(kind="volcano_spell_no_target",frame=f,target_set=list(locked)))
                release=None
                if reason:=select(f,True):
                    return stop(reason,f-1)
                continue
            events.append(dict(kind="volcano_projectiles",frame=f,projectile=projectile,target_set=[v["id"] for v in alive]))
            for v in alive:
                key=v["id"]
                window=_arrival(tables,card,ev,world,place,v,f,end,speed,f-press)
                arrivals[key]=window
                earliest,latest=window
                if earliest is not None:
                    (landings if earliest==latest else unresolved).setdefault(earliest,[]).append(key)
    return stop("horizon", end)
