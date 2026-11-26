"""Ifrit S3 on ground receivers, with the selected module and observed channel horizon."""
from math import sqrt

from .area_caster_skills import IFRIT,execute_area_skill
from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_equipment_traits import blast_distance_factor
from .damage import HP_EPSILON
from .fixed import ONE
from .enemy_elements import EnemyElements
from .eyja_aura import EYJA
from .ifrit_delta import ifrit_delta_hit,ifrit_delta_rules,ifrit_x_pulse,scorched_earth_debuff
from .ifrit_replay import (ifrit_flame_active,ifrit_flame_support,ifrit_line_attack_clock,
                           rhine_circuit_definition,validate_ifrit_flame_support)
from .lin_output_aura import LinAuraGeometry
from .marcil_deploy import _birth
from .module_effects import trait_board
from .skill_recovery import RecoveryTimer,deploy_skill
from .stage_channel import (ChannelTargets, UnsupportedChannelScene, channel_targets, object_position_frame,
                            ordinary_melee_enemy, prepare_channel, require_ordinary_highland)
from .volley_damage import _raw_f32


def ifrit_distance_factor(placement,trait,pos):
    dr=single(single(pos[0])-placement["row"])
    dc=single(single(pos[1])-placement["col"])
    distance=single(sqrt(single(single(dr*dr)+single(dc*dc))))
    return blast_distance_factor(trait,distance)


def run_ifrit_channel(tables,request):
    base={key:value for key,value in request.items() if key not in ("battle_random_seed","owner_hp_q32_at_cast")}
    deploy=next(action for action in request["actions"] if action["operator_id"]==IFRIT)
    support,supports,support_withdrawal=ifrit_flame_support(tables,request,deploy["frame"])
    flame=support[1] if support else None
    if support and not supports:
        base["squad"]=[row for row in base["squad"] if row["operator_id"]!=EYJA]
    battle=prepare_channel(tables,base,operator=IFRIT,skill=3,contact_checked_during_replay=True,
                           supporting_deploys=supports)
    validate_ifrit_flame_support(tables,support,supports,support_withdrawal,battle.opening,request["end_frame"])
    card,spec,scene=battle.card,battle.evidence,battle.scene
    require_ordinary_highland(scene, [battle.deploy])
    circuit=rhine_circuit_definition(card,spec)
    def apply(kind,frame,**state):
        return execute_area_skill(tables,card,dict(kind=kind,frame=frame,**state))["result"]
    grant=dict(sp_gain_attempt=circuit["sp"],period_seconds=circuit["interval"])
    points=battle.sp_at_button(grant)
    cost=card["skill_row"]["spData"]["spCost"]
    bonus="ifrit_e_002[dice_sp]"
    recovery=None
    if bonus+".prob" in circuit:
        if "battle_random_seed" in request:
            if circuit[bonus+".interval"]!=circuit["interval"]:
                raise ValueError("莱茵回路两次回技周期不同")
            stream=tables.combat_random(request["battle_random_seed"])
            gauge,_=deploy_skill(tables,card,battle.deploy["frame"])
            timer=RecoveryTimer(_raw_f32(circuit["interval"]))
            credits=[]
            for frame in range(battle.deploy["frame"]+1,battle.button["frame"]+1):
                gauge.state.advance(gauge.step)
                if timer.advance(gauge.step):
                    extra=int(circuit[bonus+".sp"]) if stream.dice(circuit[bonus+".prob"]) else 0
                    before=gauge.state.points
                    gauge.state.points=min(gauge.state.maximum,before+int(circuit["sp"])+extra)
                    credits.append(dict(frame=frame,extra=extra,credited=gauge.state.points-before,sp=gauge.state.points))
            points=gauge.state.points
            recovery=dict(credits=credits,random_draws=stream.draws,sp_at_button=points)
            if points<cost:
                return dict(status="sp_not_ready",frame=battle.button["frame"],skill_recovery=recovery)
        elif points<cost:
            upper=dict(grant,sp_gain_attempt=circuit["sp"]+circuit[bonus+".sp"])
            maximum=battle.sp_at_button(upper)
            if maximum>=cost:
                return dict(status="unresolved",reason="rhine_circuit_random_sp_changes_cast_eligibility",
                            frame=battle.button["frame"],sp_min=points,sp_max=maximum)
    step=int(tables.fixed_timestep()*ONE)
    start,cast=battle.cast(sp_before=points)
    commit=start["cast_frame"]
    deployed=battle.deploy["frame"]
    finish=commit+cast["end_frame"]
    end = request["end_frame"]
    pulses = [commit + f for f in cast["channel_pulse_frames"] if commit + f <= end]
    by_id, radii, positions, active, escaped, history = channel_targets(tables, scene, battle.opening,
        frames=range(deployed - 1, end + 1), end=end, with_history=True, enemy_key=None,
        first_born_tick_delay=request["movement"]["first_born_tick_delay_frames"])
    cells = set(battle.geometry["cells"])
    samples = ChannelTargets(by_id, radii, positions, active, cells, "circle", escaped)
    units = {k: ordinary_melee_enemy(tables, scene, k)["attributes"] for k in {e["enemy_key"] for e in by_id.values()}}
    receivers, resistance = {}, {}
    for key, birth in by_id.items():
        enemy = birth["enemy_key"]
        attrs = units[enemy]
        resistance[key] = _raw_f32(attrs["magicResistance"]["m_value"])
        receivers[key] = EnemyElements(tables, enemy_level=scene.enemy_level(enemy=enemy),
            hp_q32=_raw_f32(attrs["maxHp"]["m_value"]),
            buildup_resistance_q32=_raw_f32(attrs["epResistance"]["m_value"]),
            elemental_resistance_q32=_raw_f32(attrs["epDamageResistance"]["m_value"]))
    icon=card["module_info"]["typeIcon"]
    if icon not in ("bla-x","bla-d"):
        raise ValueError("所选伊芙利特模组的伤害机制尚未核实")
    module=ifrit_delta_rules(card,spec) if icon=="bla-d" else None
    debuff=scorched_earth_debuff(spec)
    trait=trait_board({"trait":{"candidates":[]}},card["module_phase"],card)
    aura, = (r["fields"] for r in spec["character"]["components"] if r["script"] == "AuraAbility")
    attr, = aura["_buffs"][0]["attributes"]["attributeModifiers"]
    if (attr["attributeType"], attr["formulaItem"]) != (3, 3):
        raise ValueError("精神融解的法抗修正位置发生变化")
    ratio = ONE + _raw_f32(next(b["value"] for talent in card["base_talents"] if talent["prefabKey"] == "1"
                                for b in talent["blackboard"] if b["key"] == "magic_resistance"))
    grid = scene.level["mapData"]["map"]
    ordinary_cells = range_cells(card["base_range"], battle.deploy, len(grid), len(grid[0]))
    geometry = LinAuraGeometry(ordinary_cells, radii,
                               {k: dict(spawn_frame=b["frame"]) for k, b in by_id.items()}, history)
    pulse=apply("channel_hit",commit,targets=[],actual_attack=card["panel_attributes"]["atk"])
    base_attack=_raw_f32(card["panel_attributes"]["atk"])
    component,=(c for c in spec["skill"]["components"] if c["script"]=="BuffDuringCasting")
    buff,=component["fields"]["_buffs"]
    action,=spec["templates"][buff["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    if (buff["templateKey"]!="periodic_damage_by_hp_ratio[skip_modifier_fix]" or not buff["waitFirstTriggerInterval"]
            or buff["firstTriggerInterval"]!=-1 or buff["lifeTimeType"]!=2 or action["_damageType"]!="PURE"
            or action["_isUndeadable"] or not action["_skipModifierEvent"]):
        raise ValueError("施法自损的触发、致死条件或生命周期改变")
    timer=RecoveryTimer(_raw_f32(buff["triggerInterval"]))
    owner_max=card["panel_attributes"]["maxHp"]
    owner_hp=request.get("owner_hp_q32_at_cast",_raw_f32(owner_max))
    if type(owner_hp) is not int or not 0<owner_hp<=_raw_f32(owner_max):
        raise ValueError("开技生命须为不超过最大生命的正 Q32.32 整数")
    self_ticks=[]
    hits=[]
    ordinary_hits, attack_starts = [], []
    self_defeat=False
    life = scene.level["options"]["maxLifePoint"]
    pending = None
    panel = card["panel_attributes"]
    interval = single(single(panel["baseAttackTime"]) * 100 / single(panel["attackSpeed"]))
    delay, period, _ = ifrit_line_attack_clock(tables, spec, interval)
    face = "Back" if battle.deploy["direction"] == "UP" else "Front"
    search,release=deployed+_birth(tables,card,spec,face)["born_exit_update"]+1,None
    interrupted=[]
    for f in range(deployed+1,end+1):
        for key in active[f]:
            if receivers[key].hp > HP_EPSILON:
                receivers[key].tick(f)
        for key, frame in escaped.items():
            if frame == f and receivers[key].hp > HP_EPSILON:
                life -= scene.enemies[by_id[key]["enemy_key"]]["lifePointReduce"]["m_value"]
        if life <= 0:
            end = f
            break
        if commit < f <= finish and timer.advance(step):
            effect=apply("self_hp_tick",f,current_hp_q32=owner_hp,max_hp=owner_max)
            owner_hp=effect["hp_after_q32"]
            self_ticks.append(dict(frame=f,**effect))
            if not owner_hp:
                end=f
                pulses=[frame for frame in pulses if frame<f]
                self_defeat=True
                break
        if f == search:
            for key in by_id:
                tick = object_position_frame(by_id[key], f, positions, active, key,
                                             observer_born=battle.deploy["frame"], observer_phase="INPUT")
                if (key in active[tick] and receivers[key].hp > HP_EPSILON and
                        circle_in_range(*positions[tick][key], radii[key], ordinary_cells, box_query=True)):
                    release = f + delay
                    attack_starts.append(dict(frame=f, release_frame=release))
                    search += period
                    break
            else:
                search += 3
        if f==commit:
            # The button follows this tick. A key which already fired survives;
            # EnterSkill prevents the next key in an unfinished normal attack.
            if release is not None and release>f:
                interrupted.append(dict(frame=f,scheduled_release=release))
                release=None
            search=finish+1
        if f not in pulses and f != release:
            continue
        channel = f in pulses
        if channel:
            targets = list(samples.at(f, lambda k: receivers[k].hp > HP_EPSILON))
        else:
            targets = [(k, pos) for k, pos in positions[f].items() if k in active[f] and
                       receivers[k].hp > HP_EPSILON and circle_in_range(*pos, radii[k], ordinary_cells, box_query=True)]
        contacts = geometry.at(f, phase="COROUTINES", entered_mode=battle.deploy["frame"], devices=(), finished={})
        scales = {}
        for key, _ in targets:
            state = receivers[key]
            flat = debuff[0] if channel else state.scorched_earth.get(IFRIT, (0, 0))[0]
            if resistance[key] + state.res_add + flat <= 0:
                scales[key] = ONE
            elif key in contacts["stable_members"]:
                scales[key] = ratio
            elif key in contacts["possible_members"]:
                pending = dict(frame=f, target=key, reason="aura_resistance_boundary")
                break
            else:
                scales[key] = ONE
        if pending:
            end = f - 1
            break
        attack=base_attack if flame is None else flame.source_attack(tables,card,owner_on_field=
            ifrit_flame_active(f,supports,support_withdrawal))
        for identity,pos in targets:
            receiver=receivers[identity]
            hp_before=receiver.hp
            res = resistance[identity]
            scale = scales[identity]
            if module:
                source = "channel_hit" if channel else "ordinary_attack"
                packet=ifrit_delta_hit(receiver,card,module,f,source,attack,res,resistance_scale_q32=scale)
                damage=packet.damage_raw if packet else 0
            elif channel:
                packet=ifrit_x_pulse(receiver,card,debuff,f,IFRIT,attack,res,
                                     ifrit_distance_factor(battle.deploy,trait,pos),scale)
                damage=packet["damage_q32"] if packet else 0
            else:
                packet = receiver.receive_ordinary(f, source=IFRIT, damage_type="arts", source_card=card,
                    attack_q32=attack, mitigation_q32=res+receiver.res_add, resistance_scale_q32=scale,
                    output_scale_q32=ifrit_distance_factor(battle.deploy, trait, pos))
                damage = packet["damage_q32"]
            (hits if channel else ordinary_hits).append(dict(frame=f,id=identity,damage_q32=damage,
                             hp_before_q32=hp_before,hp_after_q32=receiver.hp,
                             fire_ep_remaining_q32=receiver.gauges["FIRE"],
                             resistance_delta=pulse["resistance_delta"] if channel else 0,
                             **({"source_attack_q32":attack} if flame else {})))
        if f==release:
            release=None
    pulses = [f for f in pulses if f <= end]
    result=dict(stage=battle.opening["code"],squad=battle.config,deployment=battle.placement_event,cast=start,range_cells=battle.geometry["cells"],pulse_frames=pulses,
                hits=hits,self_hp_ticks=self_ticks,through_frame=end,owner_hp_q32=owner_hp,
                ordinary_attack_starts=attack_starts, ordinary_hits=ordinary_hits, interrupted_attacks=interrupted,remaining_life=life,
                receiver_events={k:r.events for k,r in receivers.items() if r.events},
                conditions=["only_reviewed_flame_breath_support" if flame else "no_enemy_or_allied_modifiers",
                            "unblocked_native_wave"],
                source=battle.opening["source"])
    if self_defeat:
        result.update(status="channel_interrupted",reason="caster_defeated_by_self_damage")
    elif pending:
        result.update(status="unresolved", reason=pending["reason"], pending=pending)
    elif life <= 0:
        result.update(status="stage_defeat")
    elif end > finish:
        result.update(status="through_horizon")
    if recovery is not None:
        result["skill_recovery"]=recovery
    return result
