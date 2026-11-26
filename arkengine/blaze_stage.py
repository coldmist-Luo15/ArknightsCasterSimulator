"""Blaze's ring, burning ground and ammunition modes on ordinary moving waves."""
from functools import partial
from collections import defaultdict

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .blaze_field import fire_tile
from .caster_ammo import CasterAmmoCounter
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_prefabs import caster_prefab_evidence
from .caster_start import resolve_cast_start
from .continuous_ranged import prepare_continuous_ranged
from .damage import HP_EPSILON
from .elemental_casters import BLAZE, _board
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .enemy_elements import EnemyElements
from .eyja_ignition import ignition_circle_overlap
from .fixed import ONE, mul
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import RecoveryTimer, deploy_skill
from .stage_deployment import RangedReentries
from .blaze_rekindle import BlazeRekindle
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def run_blaze_stage(tables, request):
    return run_with_phalanx_waves(_simulate, tables, request)


def _simulate(tables, request, releases):
    slot = request["squad"][0]["skill"]
    end = request["end_frame"]
    presses = {a["frame"] for a in request["actions"] if a.get("kind")=="skill"}
    deck_only = not any(a.get("kind","deploy")=="deploy" for a in request["actions"])
    card, stage, placed, scene, world = prepare_continuous_ranged(tables, request, BLAZE, releases, deck_only=deck_only)
    evidence = caster_prefab_evidence(tables, BLAZE, slot, skill_level=card["skill_level"],
                                      module_id=card["module"], module_level=card["module_level"])
    bb = {r["key"]: r["value"] for r in evidence["skill_blackboard"]}
    parts = {(r["path"], r["script"]):r["fields"] for r in evidence["character"]["components"]
             if r["enabled"] and r["active"]}
    ordinary = parts["Modes/Default/Body/Attack", "RangedAttack"]
    ordinary_parts = {r["script"]:r["fields"] for r in
                      evidence["projectiles"][ordinary["_projectileKey"]]["logic"]["components"]}
    mover = ordinary_parts["AdvancedMovement"]
    templates = evidence["templates"]
    place, view = scene["placement"], scene["view"]
    panel = card["panel_attributes"]
    atk, max_hp = _raw_f32(panel["atk"]), _raw_f32(panel["maxHp"])
    boost_until = -1
    trait, talent = _trait_values(card), _board(card, 0, "1")
    trait_scale = _raw_f32(trait.get("damage_scale", 1))
    dt = tables.fixed_timestep()
    step = _raw_f32(dt)
    normal_lifetime = (_raw_f32(ordinary_parts["SimpleProjectile"]["_lifeTime"])+step-1)//step
    sp = cast = counter = None
    last_placement = None
    normal_period = single(panel["baseAttackTime"] * 100 / panel["attackSpeed"])
    normal_wait = fixed_wait_frames(single(normal_period / dt))
    first = next_attack = attack_end = -1
    ring_expiry = -1
    ring_timer = hp_loss = None
    ring_atk = 0
    restore = unblock = None
    pending_bolts = []
    bolts, shells, events = [], [], []
    fields, field_dots, field_walks = set(), {}, {}
    stuns = {}
    controls, heals = defaultdict(list), defaultdict(list)
    for event in request.get("owner_channel_events",()):
        controls[event["frame"]].append(event)
    for event in request.get("ally_heals",()):
        if event["target"]==BLAZE:
            heals[event["frame"]].append(event)
    disabled_until, disarmed = -1, False
    receivers = {k:EnemyElements.from_scene_enemy(tables, dict(s, hp_q32=_raw_f32(s["hp"])))
                 for k,s in world.stats.items()}
    radii = {k:tables.enemy_circle(s["prefab"]) for k,s in world.stats.items()}
    resistance = {k:_raw_f32(s["res"]) for k,s in world.stats.items()}
    clock = run_committed_cast(tables, evidence, horizon=0, facing=view, direction=placed["direction"]).get("natural_end")
    cards = RangedReentries(tables,stage,world,request,first=-1)
    life = BlazeRekindle(tables,card,evidence,world,events)
    world.present, world.hp = False, 0
    if world.incoming:
        world.incoming.units.clear()

    if slot == 1:
        ring, = templates["blaze2_s_1"]["eventToActions"]["ON_BUFF_TRIGGER"]
        ring_radius = ring["_rangeRadius"]
        damage = templates["blaze2_s_1[aura]"]["eventToActions"]["ON_BUFF_START"][0]
        if not damage["_assignRealDamageToBB"] or not damage["_noSourceDamage"]:
            raise ValueError("烛煌火圈需要快照和生命差回读")
    elif slot == 2:
        attack = parts["Modes/S2/Body/Attack", "RangedAttack"]
        field_key = attack["_projectileKey"]
        field_life = next(r["fields"]["_lifeTime"] for r in evidence["projectiles"][field_key]["logic"]["components"]
                          if r["script"]=="SimpleProjectile")
        field_lifetime = (_raw_f32(field_life)+step-1)//step
        mover_s2, = (r["fields"] for r in evidence["projectiles"][field_key]["logic"]["components"]
                     if r["script"] == "AdvancedMovement")
        listener, = (r["fields"] for r in evidence["character"]["components"]
                     if r["active"] and r["enabled"] and r["path"] == "Modes/S2/Body/Talents/3"
                     and r["script"] == "BuffToEnemyTileListener" and r["fields"]["_buffsWhenCasted"])
        field_buff, = listener["_buffsWhenCasted"]
        field_interval, = (b["value"] for b in field_buff["blackboard"] if b["key"] == "interval")
    elif slot == 3:
        attack = parts["Modes/S3/Body/Attack", "RangedAttack"]
        shell_key = attack["_projectileKey"]
        shell_parts = {r["script"]:r["fields"] for r in evidence["projectiles"][shell_key]["logic"]["components"]}
        projectile_life = shell_parts["SimpleProjectile"]
        shell_radius = shell_parts["CircleCollider2D"]["m_Radius"]
        delay = (_raw_f32(projectile_life["_lifeTime"]) + step - 1) // step
        loss_buff, = (b for r in evidence["skill"]["components"] if r["script"] == "ActiveBuffAbility"
                      for b in r["fields"]["_buffs"] if b["templateKey"] == "blaze2_s_3[lose_hp]")
        loss_interval, = (b["value"] for b in loss_buff["blackboard"] if b["key"] == "interval")
    if slot in (2, 3):
        skill_period = single(single(single(panel["baseAttackTime"]) + single(bb["base_attack_time"]))
                              * 100 / panel["attackSpeed"])
        skill_wait = fixed_wait_frames(single(skill_period / dt))

    def alive(key):
        return receivers[key].hp > HP_EPSILON and key not in world.departed

    def invincible(key, frame):
        return world.wave.flags is not None and "INVINCIBLE" in world.wave.flags.at(key,frame)

    def attack_power(frame, active=False):
        modifier = _raw_f32(trait.get("atk", 0)) if frame < boost_until else 0
        return mul(atk, ONE + modifier + (_raw_f32(bb["atk"]) if active else 0))

    def arts(frame, key, power, kind, *, sourced=True, **details):
        if invincible(key,frame):
            return
        target = receivers[key]
        packet = target.receive_ordinary(frame,source=BLAZE if sourced else None,damage_type="arts",attack_q32=power,
                                         mitigation_q32=resistance[key]+target.res_add,
                                         output_scale_q32=trait_scale if sourced and target.burst else ONE)
        events.append(dict(kind=kind, frame=frame, target=key, damage_q32=packet["damage_q32"],
                           hp_after_q32=target.hp, **details))

    def before_burst(frame):
        nonlocal boost_until
        if not world.present:
            return
        active = slot in (2,3) and cast is not None and frame >= cast["spell_frame"]
        if trait.get("atk"):
            boost_until = frame + fixed_wait_frames(single(trait["duration"] / dt))
        world.hp = min(max_hp, world.hp + mul(max_hp, _raw_f32(talent["hp_ratio"])))
        if life.finish_if_full(frame,view):
            on_full(frame)
        if active and counter is not None and not counter.ready_to_reset:
            counter.recover(frame,int(bb["ammo_recover"]),skip_limit_check=True)
            events.append(counter.events[-1])

    def after_burst(key,frame):
        if not world.present:
            return
        target = receivers[key]
        active = slot in (2,3) and cast is not None and frame >= cast["spell_frame"]
        hit = target.apply_elemental_hp(frame, mul(attack_power(frame, active), _raw_f32(talent["ep_damage_scale"])),
                                        source="blaze_burst_talent", output_scale_q32=trait_scale)
        events.append(dict(kind="burn_burst", frame=frame, target=key, bonus_element=hit,
                           hp_after_q32=target.hp, source_hp_q32=world.hp))

    def fire(frame, key, buildup):
        receivers[key].apply_buildup(frame,"FIRE",buildup)

    for key, receiver in receivers.items():
        receiver.before_fire_break = before_burst
        receiver.after_fire_break = partial(after_burst,key)

    def candidates(frame, cells):
        return [r for r in world.snapshot(frame) if alive(r["id"])
                and circle_in_range(r["row"], r["col"], radii[r["id"]], cells, box_query=True)]

    def select(options, frame, limit=1):
        if len(options)<=1:
            return options
        rows = {row["id"]:row for row in options}
        order = world.wave.attack_order(list(rows),frame,observer_born=start)
        return [rows[key] for key in order[:limit]]

    def start_bolts(frame, targets, field=False):
        nonlocal face_sign, next_attack, attack_end
        first_target = targets[0]
        if first_target["col"] != place["col"]:
            face_sign = 1 if first_target["col"] > place["col"] else -1
        attack_period = skill_period if field else normal_period
        shot = ranged_shot_release(tables, evidence, attack if field else ordinary, attack_frame=frame,
                                    attack_interval=attack_period, facing=view, mode_path="Modes/S2" if field else "Modes/Default")
        for row in targets:
            origin, mount = stationary_cast_mounts(tables, BLAZE, shot["clock"], place, row, view,
                                                   caster_face_sign=face_sign)
            pending_bolts.append(dict(born=shot["release_frame"], target=row["id"], position=origin,
                        offset=(mount[0]-row["col"], mount[1]-row["row"], mount[2]-row["tile_z"]),
                        field=field,generation=start))
        events.append(dict(kind="field_attack_start" if field else "ordinary_attack_start", frame=frame,
                           target=first_target["id"], targets=[r["id"] for r in targets], release_frame=shot["release_frame"]))
        next_attack = attack_end = frame + (skill_wait if field else normal_wait)

    def cast_now(frame, automatic):
        nonlocal cast, restore, unblock
        phase = "before_coroutines" if automatic else "after_tick"
        cast = resolve_cast_start(tables, evidence, frame=frame, phase=phase, facing=view, direction=placed["direction"],
                                  trigger="AUTO" if automatic else "MANUAL",
                                  owner_state="attack" if frame < attack_end else "idle", sp=sp.points)
        events.extend(e for e in cast["events"] if e["frame"]==frame)
        pending_bolts.clear()
        if automatic:
            restore, unblock = frame + clock["end"], frame + clock["first_recovery_tick"]
        elif slot == 2:
            restore = cast["cast_frame"] + clock["end"]
            unblock = cast["cast_frame"] + clock["first_recovery_tick"]

    def attach_field(frame, key, cell):
        if key not in field_walks:
            field_walks[key] = world.start_walk(tables,key,frame,bb["move_speed"])
        timer = RecoveryTimer(_raw_f32(field_interval))
        timer.remaining = 0
        field_dots[key] = cell, timer
        events.append(dict(kind="burning_tile_enter", frame=frame, target=key, tile=cell, position=world.positions[frame][key]))

    def tile_pulse(frame, key):
        before = receivers[key].hp
        arts(frame, key, mul(attack_power(frame, True), _raw_f32(bb["atk_scale"])), "burning_tile_pulse",
             position=world.positions[frame][key])
        fire(frame, key, mul(abs(before-receivers[key].hp), _raw_f32(bb["element_damage_scale"])))

    def clear_mode(frame):
        nonlocal cast, counter, hp_loss, restore, unblock, boost_until, ring_timer, disarmed, disabled_until
        cast = counter = hp_loss = restore = unblock = ring_timer = None
        boost_until = -1
        disarmed, disabled_until = False, -1
        pending_bolts.clear()
        if fields:
            events.append(dict(kind="burning_tiles_cleared", frame=frame, tiles=sorted(fields)))
        fields.clear()
        field_dots.clear()

    def owner_damage(frame, source, amount, damage_type):
        previous = life.mode
        if previous=="down" and life.counter and source in receivers and alive(source):
            before = receivers[source].hp
            arts(frame,source,mul(attack_power(frame),_raw_f32(life.counter["damage_scale"])),"reborn_retaliation")
            fire(frame,source,mul(abs(before-receivers[source].hp),_raw_f32(life.counter["ep_damage_ratio"])))
        life.damage(frame, amount, damage_type, view)
        if previous=="normal" and life.mode=="falling":
            clear_mode(frame)

    def on_full(frame):
        if life.counter:
            before=sp.points
            sp.points=min(sp.maximum,sp.points+int(life.counter["sp"]))
            events.append(dict(kind="reborn_sp",frame=frame,sp=sp.points,gained=sp.points-before))
        for row in world.snapshot(frame):
            key = row["id"]
            attr = world.scene.enemies[world.births[key]["enemy_key"]]["attributes"]
            if not alive(key) or attr["stunImmune"]["m_value"] or not ignition_circle_overlap(place,row,life.radius,radii[key]):
                continue
            flags = world.wave.flags.at(key,frame) if world.wave.flags else ()
            duration = life.stun_seconds * (0.5 if "STATUS_RESISTANCE" in flags else 1)
            stuns[key] = frame + fixed_wait_frames(duration / dt)
            if key not in field_walks:
                field_walks[key] = world.start_walk(tables,key,frame,bb.get("move_speed",0))
            if world.incoming and key in world.incoming.enemies:
                world.incoming.stun(key,stuns[key])
            events.append(dict(kind="reborn_stun",frame=frame,target=key,until=stuns[key]))

    ring_center = dict(row=place["row"], col=place["col"])
    reason = "horizon"
    for frame in range(end+1):
        entry = cards.advance(frame,events)
        if entry:
            place, last_placement, start = entry, entry, frame
            view = "Back" if place["direction"]=="UP" else "Front"
            face_sign = -1 if place["direction"]=="LEFT" else 1
            area = range_cells(card["base_range"],place,scene["rows"],scene["cols"])
            skill_area = range_cells(card["skill_range"] or card["base_range"],place,scene["rows"],scene["cols"])
            gauge, _ = deploy_skill(tables,card,start)
            sp = gauge.state
            first = next_attack = start + _birth(tables,card,evidence,view)["born_exit_update"] + 1
            attack_end = -1
            ring_center = dict(row=place["row"],col=place["col"])
            life.reset()
            events.append(dict(kind="deployed",frame=frame,sp=sp.points))
        for key, walk in tuple(field_walks.items()):
            if alive(key):
                slow = key in field_dots and (restore is None or frame < restore)
                walk = world.step_walk(tables,walk,key,frame,bb.get("move_speed",0),slowed=slow,
                                       paused=frame<=stuns.get(key,-1))
            if not alive(key) or walk is None:
                field_walks.pop(key)
                field_dots.pop(key, None)
            else:
                field_walks[key] = walk
        if world.settle_exits(frame, alive) is not None:
            reason = "stage_defeat"
            break
        for key, receiver in receivers.items():
            if alive(key) and world.births[key]["frame"]<=frame:
                receiver.tick(frame)
        world.allied_impacts(frame,receivers,events)
        present = world.present
        world.receive(frame,receivers,events,owner_damage=owner_damage)
        if present and not world.present:
            clear_mode(frame)
            cards.leave(frame,BLAZE,manual=False)
        for hit in heals.get(frame,()) if world.present else ():
            old_hp = world.hp
            if life.mode=="normal":
                world.hp = min(max_hp,old_hp+_raw_f32(hit["amount"]))
            events.append(dict(kind="ally_heal",frame=frame,source=hit.get("source"),
                               heal_q32=world.hp-old_hp,source_hp_q32=world.hp))
        for effect in controls.get(frame,()) if world.present else ():
            kind = effect["kind"]
            if life.mode!="normal" and kind in ("stunned","frozen","doze","asleep","unable_to_act"):
                events.append(dict(kind="reborn_control_immune",frame=frame,effect=kind))
            elif kind in ("disarm_on","disarm_off"):
                disarmed = kind=="disarm_on"
                if disarmed:
                    pending_bolts.clear()
            elif kind in ("stunned","frozen","levitated","palsying","doze","asleep","unable_to_act","interrupt"):
                disabled_until = max(disabled_until,effect.get("end_frame",frame))
                pending_bolts.clear()
        if world.present and life.mode!="normal":
            phase = life.tick(frame,view)
            if phase=="full":
                on_full(frame)
            elif phase=="ready":
                next_attack=frame+1
        if ring_timer is not None and ring_timer.advance(step):
            for row in world.snapshot(frame):
                key = row["id"]
                if not alive(key) or not ignition_circle_overlap(ring_center, row, ring_radius, radii[key]):
                    continue
                before = receivers[key].hp
                arts(frame, key, mul(ring_atk, _raw_f32(bb["atk_scale"])), "ring_hit", sourced=False)
                fire(frame, key, mul(before - receivers[key].hp, _raw_f32(bb["element_multiplier"])))
        if frame == ring_expiry:
            ring_timer = None
        if cast:
            events.extend(e for e in cast["events"] if e["frame"]==frame)
        if cast and frame == cast["cast_frame"]:
            sp.points = cast["sp_after"]
        if cast and frame == cast["spell_frame"]:
            if slot == 1:
                ring_atk = attack_power(frame)
                ring_expiry = frame + fixed_wait_frames(single(bb["max_duration"] / dt))
                if ring_timer is None:
                    ring_timer = RecoveryTimer(_raw_f32(bb["interval"]))
                events.append(dict(kind="ring_attached", frame=frame, host=BLAZE, atk_snapshot_q32=ring_atk))
            elif slot == 2:
                next_attack = frame + 1
                events.append(dict(kind="skill_mode", frame=frame))
            else:
                counter = CasterAmmoCounter(evidence)
                hp_loss = RecoveryTimer(_raw_f32(loss_interval))
                next_attack = frame + 1
                events.append(dict(kind="skill_mode", frame=frame, ammunition=counter.remaining))
        if counter is not None and counter.owner_tick(frame):
            events.append(counter.events[-1])
            restore, unblock = frame + 1, frame + 2
        if frame == restore:
            cast = None
            pending_bolts.clear()
            next_attack = frame + 1
            events.append(dict(kind="restore_default_attack", frame=frame))
            if fields:
                events.append(dict(kind="burning_tiles_cleared", frame=frame, tiles=sorted(fields)))
                fields.clear()
                field_dots.clear()
        active = slot in (2, 3) and cast is not None and frame >= cast["spell_frame"]
        if world.present and frame > start and cast is None and (unblock is None or frame >= unblock):
            area_bonus = _raw_f32(trait.get("sp_recovery_per_sec", 0))
            if area_bonus and any(receivers[r["id"]].burst for r in candidates(frame, area)):
                sp.advance(step + mul(step, area_bonus))
            else:
                sp.advance(step)
        if world.present and life.mode=="normal" and slot == 1 and cast is None and sp.points >= sp.cost and frame >= first:
            cast_now(frame, True)
            sp.points = cast["sp_after"]

        if active and slot == 3 and frame > cast["spell_frame"] and hp_loss.advance(step):
            loss = mul(max_hp, _raw_f32(bb["lose_hp_scale"]))
            owner_damage(frame,BLAZE,loss,"HP_LOSS")
            active = life.mode=="normal"
            events.append(dict(kind="skill_hp_loss", frame=frame, source_hp_q32=world.hp))

        if active and slot == 2 and fields:
            for key in receivers:
                row = world.receiver_at(key, frame) if alive(key) and not world.stats[key].get("airborne") else None
                cell = fire_tile(row["row"], row["col"]) if row else None
                if key in field_dots:
                    previous, timer = field_dots[key]
                    if cell != previous:
                        del field_dots[key]
                        events.append(dict(kind="burning_tile_leave", frame=frame, target=key, tile=previous))
                    elif timer.advance(step):
                        tile_pulse(frame, key)
                if cell in fields and key not in field_dots:
                    attach_field(frame, key, cell)

        if (world.present and life.mode=="normal" and frame >= next_attack and frame>disabled_until and not disarmed
                and (cast is None or active) and not (active and counter and counter.ready_to_reset)):
            selected = select(candidates(frame, skill_area if active else area), frame,
                              int(bb["attack@max_target"]) if active and slot == 2 else 1)
            if not selected:
                next_attack = frame + 3
            elif active and slot == 3:
                chosen = selected[0]
                key = chosen["id"]
                center = dict(row=chosen["row"], col=chosen["col"])
                shells.append(dict(frame=frame+delay, born=frame, center=center, target=key, power=attack_power(frame, True),
                                   generation=start))
                counter.ability_event(frame, "spell_end")
                events.append(counter.events[-1])
                counter.ability_event(frame, "cast_end")
                events.append(dict(kind="skill_projectile_release", frame=frame, target=key, center=center,
                                   projectile=shell_key, ammunition=counter.remaining))
                next_attack = attack_end = frame + skill_wait
            else:
                field = active and slot == 2
                if field:
                    loss = mul(max_hp, _raw_f32(bb["attack@hp_ratio"]))
                    owner_damage(frame,BLAZE,loss,"HP_LOSS")
                    active = life.mode=="normal"
                    events.append(dict(kind="skill_hp_loss", frame=frame, source_hp_q32=world.hp))
                if life.mode=="normal":
                    start_bolts(frame, selected, field)
        for shot in bolts[:]:
            key = shot["target"]
            row = world.by_id(key, frame, projectile_born=shot["born"]) if alive(key) else None
            if row is None:
                bolts.remove(shot)
                continue
            x, y, z = shot["offset"]
            result = ordinary_trace_step(shot["position"], (row["col"]+x, row["row"]+y, row["tile_z"]+z),
                                         effective_dt=dt, round_position=True,
                                         speed=mover_s2["_speed"] if shot["field"] else mover["_speed"])
            shot["position"] = result.position
            lifetime = field_lifetime if shot["field"] else normal_lifetime
            if result.reached or frame>=shot["born"]+lifetime:
                if invincible(key,frame):
                    bolts.remove(shot)
                    continue
                current_source = world.present and shot["generation"]==start
                power = attack_power(frame, active) if current_source else shot["power"]
                arts(frame, key, power,
                     "field_projectile_hit" if shot["field"] else "ordinary_projectile_hit")
                if shot["field"] and active and current_source:
                    cell = fire_tile(row["row"], row["col"])
                    if cell not in fields:
                        fields.add(cell)
                        events.append(dict(kind="burning_tile_created", frame=frame, target=key, tile=cell))
                        for other in receivers:
                            current = world.receiver_at(other, frame) if alive(other) else None
                            if current and not current.get("airborne") and other not in field_dots and fire_tile(current["row"], current["col"]) == cell:
                                attach_field(frame, other, cell)
                bolts.remove(shot)
        for shell in shells[:]:
            if shell["frame"] != frame:
                continue
            shells.remove(shell)
            for row in world.snapshot(frame, projectile_born=shell["born"]):
                key = row["id"]
                center = shell["center"]
                if not alive(key) or (key != shell["target"] and not ignition_circle_overlap(
                        center, row, shell_radius, radii[key])):
                    continue
                if receivers[key].burst == "FIRE":
                    bonus_atk = attack_power(frame,active) if world.present and shell["generation"]==start else shell["power"]
                    bonus = receivers[key].apply_elemental_hp(
                        frame, mul(bonus_atk, _raw_f32(bb["attack@atk_scale"])),
                        source="blaze_s3", output_scale_q32=trait_scale)
                    events.append(dict(kind="skill_element_hit", frame=frame, target=key, packet=bonus,
                                       hp_after_q32=receivers[key].hp))
                if alive(key):
                    arts(frame, key, shell["power"], "skill_projectile_hit", center=center)
        for shot in pending_bolts[:]:
            if frame != shot["born"]:
                continue
            if alive(shot["target"]):
                shot["power"]=attack_power(frame,active)
                bolts.append(shot)
                events.append(dict(kind="field_projectile_release" if shot["field"] else "ordinary_projectile_release",
                                   frame=frame, target=shot["target"]))
            pending_bolts.remove(shot)
        if frame in presses:
            if not world.present or life.mode!="normal" or frame<=disabled_until:
                cause = "off_field" if not world.present else life.mode if life.mode!="normal" else "disabled"
                events.append(dict(kind="skill_rejected",frame=frame,reason=cause))
            elif active and slot == 3 and counter.remaining:
                counter.discard(frame)
                events.append(counter.events[-1])
            elif cast is None and sp.points >= sp.cost:
                cast_now(frame, False)
            else:
                events.append(dict(kind="skill_rejected", frame=frame, sp=sp.points))
        if any(a.get("kind")=="withdraw" for a in cards.orders.get(frame,())) and world.present:
            world.remove_owner(frame,events,"withdraw")
            cards.leave(frame,BLAZE,manual=True)
            clear_mode(frame)
        if world.incoming and world.present:
            world.incoming.units[BLAZE]["hp"]=world.hp
        if world.settled(frame,receivers,events):
            reason="stage_victory_projected"
            break
    result = dict(status=reason, stop=reason, through_frame=frame, stage=stage["code"], deployment=last_placement, skill=slot,
                  operator_id=BLAZE, module=card["module"], module_level=card["module_level"], source=tables.source,
                  events=events, remaining_hp_q32={k:r.hp for k,r in receivers.items()}, source_hp_q32=world.hp,
                  reborn_mode=life.mode if world.present else None, sp=sp.points if sp else None,
                  ammunition=counter.remaining if counter else None)
    return world.stage_result(result)
