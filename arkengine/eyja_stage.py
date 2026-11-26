"""Eyjafjalla in an ordinary native ground wave, with her selected skill and equipment."""
from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_catalog import stage_loadout
from .caster_equipment_traits import _trait_values
from .caster_model import _assets
from .caster_runtime import default_attack
from .caster_special_start import resolve_special_start
from .core_caster_skills import execute_core_skill,eyja_deployment
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_projectiles import moving_ranged_shot
from .eyja_ignition import run_ignition
from .fixed import ONE,mul
from .marcil_deploy import _birth
from .mode_replay import _AttackCooldown
from .mystic_timing import fixed_wait_frames
from .ranged_stage import prepare_ranged_stage
from .skill_recovery import READY_EPSILON, deploy_skill
from .volley_damage import _raw_f32


def _manual_skill(tables,card,evidence,event,world,buttons,stream):
    place=event["placement"]
    start,finish=event["frame"],event["end_frame"]
    def cells(shape):
        return range_cells(shape,place,event["rows"],event["cols"])
    base=cells(card["base_range"])
    radii={k:tables.enemy_circle(v["prefab"]) for k,v in world.stats.items()}
    def targets(f,area,*,after_objects=False):
        return [v for v in world.snapshot(f,projectile_born=f if after_objects else None) if circle_in_range(v["row"],v["col"],radii[v["id"]],area,box_query=True)]
    talent=eyja_deployment(tables,card,dict(seed=event["seed"],elite_or_boss_in_range=any(
        v["level"]!="NORMAL" for v in targets(start,base))),stream)
    gauge,_=deploy_skill(tables,card,start)
    sp=gauge.state
    sp.points=min(sp.maximum,sp.points+talent["sp"])
    log=[dict(kind="deployment",frame=start,sp=sp.points,talent=talent)]
    hp={k:_raw_f32(v["hp"]) for k,v in world.stats.items()}
    def stop(reason,f):
        return dict(events=[r for r in log if r["frame"]<=f],stop=reason,through_frame=f,sp=sp.points,hp_q32=hp)
    presses = {}
    previous = start
    for button in buttons:
        press = button["frame"]
        if (button != dict(kind="skill", operator_id=card["operator_id"], frame=press, phase="after_tick")
                or not previous < press <= finish):
            raise ValueError("需要依次送达的 after_tick 按键")
        presses[press] = button
        previous = press
    born_end=start+_birth(tables,card,evidence,event["view"])["born_exit_update"]
    if card["skill"] == 3:
        from .eyja_volcano import run_volcano
        button, = buttons
        return run_volcano(tables, card, evidence, event, world, button, sp, talent, log, targets, born_end, stream)

    panel = card["panel_attributes"]
    trait = _trait_values(card)
    penetration = _raw_f32(trait.get("magic_resist_penetrate_fixed", 0))
    attack = default_attack(tables, card["operator_id"])["fields"]
    if attack["_useCachedAtkOnly"]:
        raise ValueError("二重咏唱的普攻改为只读缓存攻击力")
    search = born_end + 1
    cooldown = None
    releases, impacts = {}, {}
    use = 0
    buff = detach = None

    def interval():
        speed = panel["attackSpeed"] + talent["attack_speed"] + (buff["attack_speed"] if buff else 0)
        return single(single(panel["baseAttackTime"]) * 100 / single(speed))

    def adjust_cooldown(frame):
        nonlocal search
        if cooldown and search > frame and cooldown.remaining_at(frame, gauge.step):
            cooldown.change_period(frame, gauge.step, _raw_f32(interval()))
            ticks = (cooldown.remaining - READY_EPSILON + gauge.step) // gauge.step
            search = frame + max(1, ticks)

    for f in range(start+1, finish+1):
        defeat = world.settle_exits(f-1, lambda k: hp[k] > HP_EPSILON)
        if defeat is not None or world.all_processed(hp):
            return stop("stage_defeat" if defeat is not None else "all_enemies_processed",
                        f-1 if defeat is None else defeat)
        sp.advance(gauge.step, stopped=buff is not None)
        if f == search:
            rows = [row for row in targets(f, base) if hp[row["id"]] > HP_EPSILON]
            if not rows:
                search = f + 3
            else:
                enemy = world.select(rows, f) if len(rows) > 1 else rows[0]
                if enemy["col"] == place["col"]:
                    return stop("requires_duetto_muzzle_facing", f-1)
                seconds = interval()
                shot = moving_ranged_shot(tables, evidence, attack, event, world, enemy, frame=f,
                                          interval=seconds, mode_path=None, through=finish, allow_target_exit=True)
                releases[shot["release_frame"]] = (enemy, shot)
                log.append(dict(kind="duetto_attack" if buff else "ordinary_attack", frame=f,
                                target=enemy["id"], interval_seconds=seconds))
                search = f + fixed_wait_frames(single(seconds/tables.fixed_timestep()))
                cooldown = _AttackCooldown(f, _raw_f32(seconds))
        if f == detach:
            buff = detach = None
            adjust_cooldown(f)
            log.append(dict(kind="duetto_detached", frame=f))
        for key in impacts.pop(f, ()):
            if hp[key] <= HP_EPSILON:
                continue
            ratio = ONE + _raw_f32(talent["caster_attack_bonus"]) + _raw_f32(buff["atk_add_ratio"] if buff else 0)
            enemy = world.stats[key]
            packet = ordinary_arts_hit(attack_raw=mul(_raw_f32(panel["atk"]), ratio),
                                       resistance_raw=_raw_f32(enemy["res"])-penetration, hp_raw=hp[key])
            hp[key] = packet.hp_after_raw
            if not buff and enemy["level"] in ("ELITE", "BOSS"):
                sp.points = min(sp.maximum, sp.points + int(trait.get("sp", 0)))
            log.append(dict(kind="duetto_hit" if buff else "ordinary_hit", frame=f, receiver=key,
                            damage_q32=packet.damage_raw, hp_q32=hp[key]))
        if f in releases:
            enemy, shot = releases.pop(f)
            key = enemy["id"]
            if hp[key] > HP_EPSILON and world.by_id(key, f, projectile_born=f) is not None:
                log.append(dict(kind="projectile_release", frame=f, target=key, projectile=attack["_projectileKey"]))
                if shot["reach_frame"] is not None:
                    impacts.setdefault(shot["reach_frame"], []).append(key)
            else:
                return stop("duetto_target_invalid_before_release", f)
        if f in presses:
            cast = resolve_special_start(tables, evidence, frame=f, source="manual", sp=sp.points,
                                         previous_uses_this_deployment=use, attached=buff is not None)
            if cast["status"] != "ability_attached":
                log.append(dict(kind="skill_button_rejected", frame=f, reason=cast["status"], sp=sp.points))
                continue
            buff = execute_core_skill(tables, card, dict(kind="skill_buff", frame=f, previous_uses_this_deployment=use))
            sp.points, detach = cast["sp_after"], cast["detach_frame"]
            use += 1
            adjust_cooldown(f)
            log.append(dict(kind="duetto_attached", frame=f, buff=cast["attached_buff"], sp=sp.points,
                            attack_speed=buff["attack_speed"], attack_bonus=buff["atk_add_ratio"]))
    return stop("horizon", finish)


def run_eyja_stage(tables,request):
    if len(request["squad"])>1:
        from .eyja_live import run_live_eyja
        return run_live_eyja(tables,request)
    pick,=request["squad"]
    req=dict(request,squad=[stage_loadout(tables,pick)])
    if req["squad"][0]["skill"] in (2,3):
        from .eyja_live import run_live_eyja
        return run_live_eyja(tables,req)
    actions=req["actions"]
    buttons=[b for b in actions if b.get("kind")=="skill"]
    if len(buttons)>1:
        if req["squad"][0]["skill"]!=1 or len(actions)!=len(buttons)+1:
            raise ValueError("只有二重咏唱支持此连续输入")
        req=dict(req,actions=[actions[0],buttons[-1]])
    card,stage,deploy,event,world=prepare_ranged_stage(tables,req,operator="char_180_amgoat",manual=bool(buttons),
                                                      portable=True,track_exits=True,
                                                      simulate_before_button=True,allow_flying=True)
    if (card["skill"]==2)==bool(buttons):
        raise ValueError("所选艾雅法拉技能的手动按键数量不符")
    seed=request.get("battle_random_seed",request.get("eyja_deployment_seed"))
    if "eyja_deployment_seed" in request and request["eyja_deployment_seed"]!=seed:
        raise ValueError("艾雅法拉部署与后续技能必须使用同一条战斗随机流")
    battle_seed="battle_random_seed" in request
    stream=tables.combat_random(seed) if seed is not None else None
    if battle_seed and stream:
        stream.advance_spawn_offsets(sum(birth["frame"]<event["frame"] for birth in world.births.values()))
    event.update(enemies=list(world.stats.values()),seed=seed,native_spawn_random=battle_seed)
    spec=_assets(tables,card)
    loss_before_deploy=world.settle_exits(deploy["frame"]-1,lambda _:True)
    if loss_before_deploy is None:
        result=(_manual_skill(tables,card,spec,event,world,buttons,stream) if buttons else
                run_ignition(tables,card,spec,event,stream=stream,world=world))
    else:
        deploy=None
        result=dict(status="eyja_ignition_scene",through_frame=loss_before_deploy,stop="stage_defeat",events=[],
                    sp=None,hp_q32={k:_raw_f32(e["hp"]) for k,e in world.stats.items()})
    world.stage_result(result,fixed_timestep=tables.fixed_timestep(),target_field="receiver",hp_field="hp_q32",
                       projection_horizon=request["end_frame"])
    return dict(stage=stage["code"],deployment=deploy,**result,source=tables.source)
