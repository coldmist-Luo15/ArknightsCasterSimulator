"""Replay the first activated skill of a deployed caster against a stationary target schedule."""
from .attack_range import circle_in_range, range_cells
from .caster_cast import run_committed_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .caster_special_start import resolve_special_start
from .caster_start import resolve_cast_start
from .five_star_skills import _integer, _number, _targets
from .fixed import ONE
from .module_effects import active_board, talent_rows
from .mostima_aura import set_period
from .passenger_isolation import isolated_in
from .skill_recovery import RecoveryTimer, _divide, deploy_skill
from .volley_damage import _raw_f32


def first_branch_skill(tables, card, event):
    """The scene stops after the first delivered skill, without projecting unobserved subsequent damage."""
    uid, slot = card["operator_id"], card["skill"]
    start, end = _integer(event,"frame"), _integer(event,"end_frame")
    if not start < end <= start+18000:
        raise ValueError("需要最多十分钟的部署帧区间")
    rows, cols, place = _integer(event,"rows"), _integer(event,"cols"), event["placement"]
    active_range = skill_range_cells(tables,card,event)["cells"]
    basic_range = range_cells(card["base_range"],place,rows,cols)
    facing = place["spine_view"]
    if facing not in ("Front","Back"):
        raise ValueError("需要默认外观的正面或背面视图")
    presses = event.get("press_frames",[])
    if not isinstance(presses,list) or any(type(f) is not int or not start <= f <= end for f in presses) or len(set(presses))!=len(presses):
        raise ValueError("按键应是区间内无重复的逻辑帧")
    targets = []
    for row in _targets(event):
        r, c = _integer(row,"row"), _integer(row,"col")
        if r >= rows or c >= cols or not _number(row,"hp"):
            raise ValueError("靶标需要有效的格心和正生命")
        radius = _number(row,"radius")
        targets.append(dict(id=row["id"], row=r, col=c, radius=radius,enemy_unit=row.get("enemy_unit",True),hp=row["hp"],spawn=_integer(row,"spawn"),
                            rank=_number(row,"route_remaining"),
                            basic=circle_in_range(r,c,radius,basic_range,box_query=True),
                            skill=circle_in_range(r,c,radius,active_range,box_query=True)))
    replay, _ = deploy_skill(tables,card,start)
    g = replay.state
    flags = card["elite"],card["level"],card["potential"]
    unit = tables.read("excel/character_table.json")[uid]
    ev = caster_prefab_evidence(tables,uid,slot,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    control, = [x for x in ev["skill"]["components"] if not x["path"] and "_allowSpRecoveryWhenAffecting" in x["fields"]]
    extra_sp = 0
    if uid=="char_1038_whitw2" and unit["nationId"]=="siracusa":
        extra_sp = int(active_board(talent_rows(card["module_phase"],1,"2",unit["talents"][1]["candidates"]),*flags)["sp"])
        g.points = min(g.maximum,g.points+extra_sp)
    isolation = None
    if uid=="char_472_pasngr":
        extra = active_board(talent_rows(card["module_phase"],1,"10",[]),*flags)
        if extra.get("sp_recovery_per_sec"):
            part,=[r for p in ev["equipment"].values() for r in p["components"] if r["script"]=="PassiveBuffAbility"]
            buff,=part["fields"]["_buffs"]
            if (buff["templateKey"]!="pasngr_e_t_10" or not buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"]!=-1):
                raise ValueError("异客孤卒回复光环的首个刷新配置已变化")
            shape,=[b["valueStr"] for b in buff["blackboard"] if b["key"]=="range_id"]
            check_cells=set(range_cells(tables.read("excel/range_table.json")[shape],place,rows,cols))
            period=_divide(ONE,ONE+_raw_f32(extra["sp_recovery_per_sec"]))
            isolation=(RecoveryTimer(_raw_f32(buff["triggerInterval"])),check_cells,period)
    automatic = card["skill_row"]["skillType"]=="AUTO"
    birth_done = start+(ONE+replay.step-1)//replay.step+1
    next_search = birth_done
    log = [dict(frame=start,kind="deploy",sp=g.points,own_team_initial_sp=extra_sp)]
    ready = start if g.points>=g.cost else None
    target = None
    pending_damage = False
    status = "held_manual_skill" if ready is not None and not automatic else "horizon"
    delivery = None
    for f in range(start,end+1):
        prior = g.points
        live = [r for r in targets if r["spawn"]<=f]
        if f>start:
            if isolation:
                timer,check_cells,accelerated=isolation
                if timer.advance(replay.step):
                    isolated=isolated_in(check_cells,live)
                    new_period=accelerated if isolated else ONE
                    if g.timer.period!=new_period:
                        # Entity.OnTick updates the buff container before the SP controller.
                        set_period(g,new_period,stopped=False)
                        log.append(dict(frame=f,kind="isolation_sp_rate_change",isolated=isolated,period_q32=new_period))
            g.advance(replay.step)
        if g.points//g.cost > prior//g.cost or (f==start and g.points>=g.cost):
            if ready is None:
                ready = f
            log.append(dict(frame=f,kind="charge_available",sp=g.points,charges=g.points//g.cost))
            status = "waiting_for_ordinary_attack" if automatic else "held_manual_skill"
        ordinary = [r for r in live if r["basic"]]
        source = None
        if f==next_search:
            if ordinary:
                if uid=="char_472_pasngr" and slot==1 and g.points>=g.cost:
                    best = min(r["rank"] for r in ordinary)
                    selected = [r for r in ordinary if r["rank"]==best]
                    if len(selected)!=1:
                        status="unresolved_ordinary_target_priority_tie"
                        break
                    target,source=selected[0]["id"],"before_attack"
                else:
                    pending_damage = True
                # This first-skill window only projects idle searches; a running
                # ordinary attack would require its own damage, cooldown and state.
                next_search=None
            else:
                next_search += 3
        if automatic and f in presses:
            log.append(dict(frame=f,kind="rejected_manual_request",reason="automatic_skill"))
        if automatic and uid!="char_472_pasngr" and g.points>=g.cost and f>=birth_done:
            source = "auto_tick"
        if not automatic and f in presses:
            if g.points<g.cost:
                log.append(dict(frame=f,kind="rejected_manual_request",reason="insufficient_sp"))
                continue
            source = "manual"
        if pending_damage:
            status = "requires_ordinary_attack_continuation"
            break
        if source is None:
            continue
        cls = control["script"]
        if cls=="CastSkill" and control["fields"]["_trigger"]["m_PathID"]:
            if uid!="char_472_pasngr" or slot!=3 or not control["fields"]["_useTriggerInManualMode"]:
                raise ValueError("场景未覆盖此技能的启动触发器")
            pool = [r for r in live if r["skill"]]
            if not pool:
                log.append(dict(frame=f,kind="rejected_manual_request",reason="selector_found_no_target",sp=g.points))
                continue
            best = max(r["hp"] for r in pool)
            selected = [r for r in pool if r["hp"]==best]
            target=min(selected,key=lambda r:(r["rank"],r["spawn"]))["id"]
        phase = "after_tick" if source=="manual" else "before_coroutines"
        options = dict(frame=f,phase=phase,facing=facing,direction=place["direction"],sp=g.points)
        if cls in ("ToggleSkill","ReplacementSkill"):
            delivery = resolve_special_start(tables,ev,**options,source=source,toggled=False)
        else:
            delivery = resolve_cast_start(tables,ev,**options,owner_state="born" if f<birth_done else "idle")
            if uid=="char_4226_veen" and f>=birth_done:
                # Mystic Casters can be charging on an empty field. Both live
                # states take the same accepted state switch in this fixture.
                attack=resolve_cast_start(tables,ev,**options,owner_state="attack")
                if any(attack[k]!=delivery[k] for k in ("status","cast_frame","spell_frame","sp_after")):
                    status="requires_current_storage_action_for_skill_start"
                    break
                for e in delivery["events"]:
                    if e["kind"]=="enter_skill_state":
                        e["old_state"]="idle_or_storage_attack"
                        e.pop("old_attack_exit_calls_interrupt_if_not",None)
        if delivery["cast_frame"] is None:
            log.append(dict(frame=f,kind="request_without_commit",reason=delivery["status"]))
            delivery=None
            continue
        cast = delivery["cast_frame"]
        log.append(dict(frame=f,kind="skill_delivery",source=source,target=target))
        log.extend(e for e in delivery["events"] if e["frame"]<=end and e["kind"]!="trigger_delivery")
        if cls=="ReplacementSkill":
            stats = card["panel_attributes"]
            shot = run_committed_cast(tables,ev,horizon=end-cast,facing=facing,
                                      effective_attack_interval=stats["baseAttackTime"]*100/stats["attackSpeed"])
            log.extend(dict(e,frame=cast+e["frame"],target=target) for e in shot["events"] if e["kind"] in ("baked_OnAttack","projectile_release"))
        if cast<=end:
            g.points=delivery["sp_after"]
        status="first_cast" if cast<=end else "in_start_animation"
        f=max(e["frame"] for e in log)
        break
    return dict(status=status, through_frame=f, first_ready_frame=ready,sp=g.points,target=target, events=log,
                conditions=["默认外观、单人部署、静止且可选的敌方靶标、无外部技力或速度调整",
                            "输入按键在本帧固定更新之后交付；自动检查属于干员时钟；记录至首个技能的已知动作",
                            "首次普通攻击会改变靶标的场景须交给持续攻击引擎"])
