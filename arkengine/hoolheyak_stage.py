"""Ho'olheyak's baked projectiles and at-impact airborne talent on an ordinary ground wave."""
from itertools import product
from math import ceil
from copy import copy
from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_cast import _after_spell_clock, run_committed_cast
from .caster_equipment_traits import _trait_values,module_branch_trait
from .caster_first_spell import _selected_clip
from .caster_opening import deploy_to_first_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_special_start import resolve_special_start
from .core_caster_skills import _hoho
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_projectiles import moving_ranged_shot,stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .levitate import WalkingLevitate
from .lin_output_aura import trigger_contact
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .phalanx_skills import _talent
from .ranged_stage import prepare_ranged_stage
from .skill_recovery import READY_EPSILON, _divide, deploy_skill
from .stage_channel import UnsupportedChannelScene,object_position_frame
from .target_priority import native_box_candidates
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


class _WalkingWave:
    """The route replay advances on walking updates; selection and projectile observers keep their own object order."""

    def __init__(self,world,states,cells,radii,step):
        self.world,self.states,self.cells,self.radii,self.step=world,states,cells,radii,step
        self.lifts={}

    def advance(self,frame,log):
        for key,lift in self.lifts.items():
            if self.states[key].hp>HP_EPSILON:
                lift.advance(frame)
                if frame==lift.expires:
                    log.append(dict(kind="landed",frame=frame,target=key,talent_applies=False))

    def apply(self,key,frame,duration):
        if key not in self.lifts:
            a=self.world.positions[frame-1].get(key)
            b=self.world.positions[frame].get(key)
            if not a or not b or a==b or any(abs(x-y)>.2 for x,y in zip(a,b)):
                return False
            self.lifts[key]=WalkingLevitate(frame)
        self.lifts[key].apply(frame,duration,self.step)
        return True

    def by_id(self,key,frame,*,projectile_born=None,after_objects=False):
        world=self.world
        observed=frame if after_objects else object_position_frame(world.births[key],frame,world.positions,world.active,key,
            observer_born=world.deployment_frame if projectile_born is None else projectile_born,
            observer_phase="INPUT" if projectile_born is None else "COROUTINES")
        lift=self.lifts.get(key)
        z=0.
        if lift:
            if observed>lift.frame:
                # S1 has one projectile per locked target per attack. Forecast only its
                # flight, without mutating the receiver's frame or pre-consuming effects.
                lift=copy(lift)
                lift.samples=dict(lift.samples)
                for tick in range(lift.frame+1,observed+1):
                    lift.advance(tick)
            observed,z=lift.samples.get(observed,(observed,0.))
        if key not in world.active[observed]:
            return None
        row,col=world.positions[observed][key]
        return dict(world.stats[key],row=row,col=col,height_offset=z,route_frame=observed,receiver_born=world.births[key]["frame"],
                    projectile_born=projectile_born)

    def admitted(self,frame,*,after_objects=False):
        return [e for key,state in self.states.items() if state.hp>HP_EPSILON and
                (e:=self.by_id(key,frame,after_objects=after_objects)) is not None and
                circle_in_range(e["row"],e["col"],self.radii[key],self.cells,box_query=True)]

    def elevated(self,key,frame):
        return key in self.lifts and self.lifts[key].airborne_after_objects(frame)


def _mass_aura(tables,card,evidence,wave,attached,*,mode=None):
    """Bind the source aura to the deployed mode's contact and receiver timers."""
    modes={1:"Default",2:"S2",3:"S3"}
    path=f"Modes/{mode or modes[card['skill']]}/Body/Talents/2"
    aura,=(p["fields"] for p in evidence["character"]["components"] if p["path"]==path and p["script"]=="AuraAbility")
    buff,=aura["_buffs"]
    templates=tables.read("battle/buff_template_data.json")
    actions=templates[buff["templateKey"]]["eventToActions"]
    start,=actions["ON_BUFF_START"]
    if (not aura["_removeBuffWhenTargetLeave"] or not aura["_removeBuffWhenAbilityDetached"]
            or start["_conditionNode"]["_condType"]!="GT" or start["_conditionNode"]["_useSourceHpRatio"]
            or buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"]!=-1
            or actions["ON_BUFF_TRIGGER"]!=[start]):
        raise ValueError("曾有羽翼的进入、离开或血线刷新配置发生变化")
    child=start["_succeedNodes"][-1]
    modifier,=tables.read("buff_table.json")[child["_buff"]["buffKey"]]["attributes"]["attributeModifiers"]
    if (not child["_finishDerivedBuffIfParentFinish"] or not child["_buff"]["loadFromDB"]
            or (modifier["attributeType"],modifier["formulaItem"],modifier["value"])!=("MASS_LEVEL","ADDITION",-1)):
        raise ValueError("失重的来源或重量变化配置发生变化")
    threshold=_raw_f32(_talent(card,1,"2")["hp_ratio"])
    return _HealthAura(wave,attached,threshold,_raw_f32(buff["triggerInterval"]),at_contact=True,
                       targets=[key for key,enemy in wave.world.stats.items()
                                if getattr(wave,"exact_contacts",False) or enemy["mass_level"]==4 and not enemy["levitate_immune"]])


def _magic_aura(tables,evidence,wave,attached,trait):
    if "damage_scale" not in trait:
        return None
    candidates=[(c["fields"],v["components"]) for v in evidence["equipment"].values() for c in v["components"]
                if c["script"]=="AuraAbility" and any(b["buffKey"]=="heyak_e_003[check]" for b in c["fields"]["_buffs"])]
    (fields,components),=candidates
    buff,=fields["_buffs"]
    opts,=[c["fields"]["_targetOptions"] for c in components if c["id"]==fields["_targetValidator"]["m_PathID"]]
    templates=tables.read("battle/buff_template_data.json")
    actions=templates[buff["templateKey"]]["eventToActions"]
    check,=actions["ON_BUFF_TRIGGER"]
    condition=check["_conditionNode"]
    create,_=check["_succeedNodes"][0]["_failNodes"]
    remove,_=check["_failNodes"]
    if ((opts["targetSide"],opts["targetMotion"],opts["targetCategory"])!=(2,3,1)
            or not fields["_removeBuffWhenTargetLeave"] or not fields["_removeBuffWhenAbilityDetached"]
            or fields["_onlyAddBuffOnceForEachTarget"] or set(actions)!={"ON_BUFF_TRIGGER"}
            or (condition["_condType"],condition["_targetType"],condition["_useSourceHpRatio"])!=("LT","BUFF_OWNER",False)
            or buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"]!=-1
            or create["_buffKey"]!="weak[magic][inf]" or not create["_finishDerivedBuffIfParentFinish"]
            or remove["_buffKey"]!="weak[magic]" or not remove["_updateOverrideMap"]):
        raise ValueError("霍尔海雅的法脆光环筛选、时钟或派生动作变化")
    return _HealthAura(wave,attached,_raw_f32(trait["hp_ratio"]),_raw_f32(buff["triggerInterval"]),
                       at_contact=False,targets=wave.states)


class _HealthAura:
    """Track the receiver clock for each possible physical contact frame."""
    def __init__(self,wave,attached,threshold,period,*,at_contact,targets):
        self.wave,self.threshold,self.period=wave,threshold,period
        self.at_contact=at_contact
        if wave.step>=period:
            raise ValueError("目标生命光环的轮询周期需要大于一次对象更新")
        self.members={key:{(None,False)} for key in targets}
        self.contacts(attached,initial=True)
        self.before_update=self.members.copy()

    def checks(self,key,min_hp):
        upper=self.wave.states[key].hp
        lower=min_hp.get(key,upper)
        maximum=_raw_f32(self.wave.world.stats[key]["hp"])
        ratios=(_divide(hp,maximum) for hp in (lower,upper))
        if self.at_contact:
            return {ratio>self.threshold+READY_EPSILON for ratio in ratios}
        return {ratio<self.threshold-READY_EPSILON for ratio in ratios}

    def advance(self,min_hp=None,*,save_before_update=False):
        min_hp={} if min_hp is None else min_hp
        if save_before_update:
            self.before_update=self.members.copy()
        for key,branches in self.members.items():
            sampled=None
            updated=set()
            for remaining,reduced in branches:
                if remaining is None:
                    updated.add((remaining,reduced))
                    continue
                remaining-=self.wave.step
                if remaining<READY_EPSILON:
                    if sampled is None:
                        sampled=self.checks(key,min_hp)
                    updated.update((remaining+self.period,passed) for passed in sampled)
                else:
                    updated.add((remaining,reduced))
            self.members[key]=updated

    def contacts(self,frame,min_hp=None,*,initial=False):
        min_hp={} if min_hp is None else min_hp
        for key,branches in self.members.items():
            row=self.wave.by_id(key,frame,after_objects=True) if self.wave.states[key].hp>HP_EPSILON else None
            contact=trigger_contact((row["row"],row["col"]),self.wave.radii[key],self.wave.cells) if row else False
            if getattr(self.wave,"exact_contacts",False):
                contact=bool(row and circle_in_range(row["row"],row["col"],self.wave.radii[key],self.wave.cells,box_query=True))
                initial=False
            updated=set()
            if contact is not True or initial:
                updated.add((None,False))
            if contact is not False:
                updated.update((remaining,reduced) for remaining,reduced in branches if remaining is not None)
                if (None,False) in branches:
                    initial_states=self.checks(key,min_hp) if self.at_contact else (False,)
                    updated.update((0,passed) for passed in initial_states)
            self.members[key]=updated

    def weight(self,enemy):
        mass=enemy["mass_level"]
        states=self.applied(enemy["id"])
        return mass-int(next(iter(states))) if len(states)==1 else None

    def applied(self,key,*,before_update=False):
        branches=(self.before_update if before_update else self.members).get(key,((None,False),))
        return {active for _,active in branches}


def _vulnerability(magic_aura,key,trait,*,before_update=False):
    if magic_aura is None:
        return ONE,ONE
    factor=_raw_f32(trait["damage_scale"])
    possible={factor if applied else ONE for applied in magic_aura.applied(key,before_update=before_update)}
    return min(possible),max(possible)


def _first_positions(origins,mount,speed,dt):
    # This loop moves its muzzle only on X. Over a source interval that does
    # not cross target X each coordinate of MoveTowards is monotone. The
    # clamp to the target preserves that property. Enclose each tiny source
    # interval and keep the actual four-decimal lattice for subsequent ticks.
    lo,hi=min(origins)[0]-.00001,max(origins)[0]+.00001
    y,z=next(iter(origins))[1:]
    pieces=max(1,ceil((hi-lo)/.0001))
    xs={lo+i*(hi-lo)/pieces for i in range(pieces+1)}
    if lo<mount[0]<hi:
        xs.add(mount[0])
    positions=set()
    previous=None
    for x in sorted(xs):
        point=ordinary_trace_step((x,y,z),mount,effective_dt=dt,round_position=False,speed=speed).position
        if previous is not None:
            bounds=[range(round((min(a,b)-.00001)*10000),round((max(a,b)+.00001)*10000)+1)
                    for a,b in zip(previous,point)]
            positions.update(tuple(single(v/10000) for v in xyz) for xyz in product(*bounds))
        previous=point
    return positions


def _hit(tables,card,bb,enemy,state,frame,pen,*,count=0,stream=None,levitating=False,scale=None,vulnerability=ONE,
         effective_mass):
    effect=_hoho(tables,card,dict(kind="hit",issued_projectiles=count,target_motion=dict(
        raw_airborne=enemy.get("airborne",False),
        mass_level=enemy["mass_level"] if effective_mass is None else effective_mass,
        levitate_immune=enemy["levitate_immune"],levitate_sequence=int(levitating))),bb,stream)
    if effect["levitate_applied"] and effective_mass is None:
        raise UnsupportedChannelScene("此时需要先计算目标减重光环，再判定是否减半浮空时长")
    if scale is not None:
        effect["skill_scale"]=scale
    atk=mul(_raw_f32(card["panel_attributes"]["atk"]),_raw_f32(effect["skill_scale"]))
    attack=mul(atk,_raw_f32(effect["talent_atk_factor"]))
    hp=state.receive_ordinary(frame,source="hoolheyak",damage_type="arts",attack_q32=attack,
                              mitigation_q32=max(0,_raw_f32(enemy["res"])-pen),output_scale_q32=vulnerability)
    accepted=effect["silence_seconds"]>0 and not enemy["silence_immune"]
    result=dict(kind="impact",target=enemy["id"],frame=frame,**effect,attack_q32=attack,damage_q32=hp["damage_q32"],hp_after_q32=state.hp,
                silence_accepted=accepted)
    if accepted and state.hp>HP_EPSILON:
        result["silence_expiry_receiver_tick"]=state.silence.attach(tables,frame,effect["silence_seconds"],
                                   receiver_born=enemy["receiver_born"],projectile_born=enemy["projectile_born"])
    return result


def _first_skill(tables,card,event,world,evidence,bb,states,cells,radii,pen):
    start,end,place,face=event["frame"],event["end_frame"],event["placement"],event["view"]
    parts=evidence["skill"]["components"]
    special,=(p["fields"] for p in parts if p["script"]=="RangedAttack")
    normal,=(p["fields"] for p in evidence["character"]["components"]
             if p["path"]=="Modes/Default/Body/Attack" and p["script"]=="RangedAttack")
    select,=(p["fields"] for p in parts if p["script"]=="AdvancedSelector")
    mark,=(p["fields"] for p in parts if p["script"]=="BuffToCastTargets")
    if (select["_postFilter"]!=4 or select["_maxNum"]!=2 or mark["_extraCondition"]!=1 or
            special["_selectTargetTiming"]!=0 or not special["_alwaysIncludeTarget"]):
        raise ValueError("但为求索的双目标选择或单弹标记配置发生变化")
    panel=card["panel_attributes"]
    interval=single(single(panel["baseAttackTime"])*100/single(panel["attackSpeed"]))
    period=fixed_wait_frames(single(interval/tables.fixed_timestep()))
    gauge,_=deploy_skill(tables,card,start)
    shot_clock=run_committed_cast(tables,evidence,horizon=period+3,facing=face,
                                  effective_attack_interval=interval)["replacement"]
    if shot_clock["wait_for_projectile_invalid"]:
        raise ValueError("求索改为等待弹体失效才结算技力阻回")
    recovery_offset=_after_spell_clock(shot_clock["release"],shot_clock["cached_duration_seconds"],
                                       gauge.step,same_frame_resume=True)["first_recovery_tick"]
    recovery=start+1
    wave=_WalkingWave(world,states,cells,radii,gauge.step)
    mass_aura=_mass_aura(tables,card,evidence,wave,start)
    trait=_trait_values(card)
    magic_aura=_magic_aura(tables,evidence,wave,start,trait)
    search=start+_birth(tables,card,evidence,face)["born_exit_update"]+1
    releases,hits={},{}
    log=[dict(kind="deploy",frame=start,sp=gauge.state.points)]
    stop="horizon"
    frame=start
    for frame in range(start+1,end+1):
        wave.advance(frame,log)
        mass_aura.advance()
        if magic_aura is not None:
            magic_aura.advance()
        gauge.state.advance(gauge.step,stopped=frame<recovery)
        if frame==search:
            rows=wave.admitted(frame)
            if rows:
                charged=gauge.state.points>=gauge.state.cost
                attack=special if charged else normal
                chosen=[]
                route_frames={e["id"]:e["route_frame"] for e in rows}
                while rows and len(chosen)<(select["_maxNum"] if charged else 1):
                    enemy=world.select(rows,frame,frame_by_target=route_frames) if len(rows)>1 else rows[0]
                    chosen.append(enemy)
                    rows.remove(enemy)
                if len({e["col"]>place["col"] for e in chosen})>1:
                    return dict(stop="two_receivers_on_opposite_sides",through_frame=frame,events=log)
                if charged:
                    registration=resolve_special_start(tables,evidence,frame=frame,phase="before_coroutines",sp=gauge.state.points,
                        source="before_attack",direction=place["direction"],facing=face)
                    if registration["status"]!="replacement_registered":
                        raise ValueError("一技能没有在普攻前注册替换")
                    gauge.state.points=registration["sp_after"]
                    recovery=frame+recovery_offset
                log.append(dict(kind="replacement" if charged else "ordinary",frame=frame,
                                targets=[e["id"] for e in chosen],sp=gauge.state.points))
                for enemy in chosen:
                    shot=moving_ranged_shot(tables,evidence,attack,event,wave,enemy,frame=frame,interval=interval,
                                            mode_path=None,through=end)
                    if shot["reach_frame"] is not None and shot["reach_frame"]>=search+period:
                        return dict(stop="concurrent_shots_may_change_receiver_motion",through_frame=frame,events=log)
                    release=shot["release_frame"]
                    releases.setdefault(release,dict(projectile=attack["_projectileKey"],targets=[]))["targets"].append(enemy["id"])
                    if shot["reach_frame"] is not None:
                        hits.setdefault(shot["reach_frame"],[]).append((enemy["id"],release,charged))
                search+=period
            else:
                search+=3
        if frame in releases:
            log.append(dict(kind="release",frame=frame,**releases[frame]))
        for key,release,charged in hits.pop(frame,()):
            if states[key].hp<=HP_EPSILON:
                continue
            receiver=wave.by_id(key,frame,projectile_born=release)
            minimum,maximum=_vulnerability(magic_aura,key,trait)
            if minimum!=maximum:
                return dict(stop="magic_fragility_aura_refresh_phase",through_frame=frame,events=log)
            impact=_hit(tables,card,bb,receiver,states[key],frame,pen,count=len(releases[release]["targets"]) if charged else 0,
                        scale=None if charged else 1,levitating=wave.elevated(key,frame),
                        vulnerability=minimum,
                        effective_mass=mass_aura.weight(receiver))
            if "sp" in trait:
                # The owner updates the replacement modifier one tick before natural recovery resumes.
                branch=module_branch_trait(tables,card,dict(target_level=receiver["level"],normal_attack_hit=True,
                                                            sp_blocked=frame<recovery-1))
                before=gauge.state.points
                gauge.state.points=min(gauge.state.maximum,before+int(branch["uncapped_sp_credit"]))
                impact.update(module_sp_credit=gauge.state.points-before,sp_after=gauge.state.points)
            log.append(impact)
            if impact["levitate_applied"] and states[key].hp>HP_EPSILON:
                if not wave.apply(key,frame,impact["initial_levitate_lifetime_q32"]):
                    return dict(stop="levitate_during_nonwalking_route_command",through_frame=frame,events=log)
        mass_aura.contacts(frame)
        if magic_aura is not None:
            magic_aura.contacts(frame)
    return dict(stop=stop,through_frame=frame,events=log)


def run_hoolheyak_stage(tables,request):
    slot=request["squad"][0]["skill"]
    card,stage,deploy,event,world=prepare_ranged_stage(tables,request,operator="char_4027_heyak",manual=slot==2,
                                                      portable=True,allow_flying=True)
    evidence=caster_prefab_evidence(tables,card["operator_id"],slot,skill_level=card["skill_level"],
                                    module_id=card["module"],module_level=card["module_level"])
    bb={r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    states={k:EnemyElements.from_scene_enemy(tables,dict(e,hp_q32=_raw_f32(e["hp"]))) for k,e in world.stats.items()}
    min_hp={}
    cells=range_cells(card["skill_range"] or card["base_range"],deploy,event["rows"],event["cols"])
    radii={k:tables.enemy_circle(e["prefab"]) for k,e in world.stats.items()}
    pen=_raw_f32(_trait_values(card).get("magic_resist_penetrate_fixed",0))
    if slot==1:
        result=_first_skill(tables,card,event,world,evidence,bb,states,cells,radii,pen)
    elif slot==2:
        stream=tables.combat_random(request["battle_random_seed"])
        min_hp={k:s.hp for k,s in states.items()}
        result=_barrage(tables,card,event,world,evidence,bb,states,cells,radii,pen,stream,min_hp)
        result["rng"]=dict(type="Torappu.LegacyRandom",seed=request["battle_random_seed"],draws=stream.draws)
    else:
        raise ValueError("该普通波次目前接入霍尔海雅一、二技能")
    hp_bounds={k:[lo,states[k].hp] for k,lo in min_hp.items() if lo!=states[k].hp}
    if hp_bounds:
        result["remaining_hp_bounds_q32"]=hp_bounds
    return dict(stage=stage["code"],deployment=deploy,remaining_hp_q32={k:None if k in hp_bounds else s.hp for k,s in states.items()},
                **result,source=tables.source)


def _barrage(tables,card,event,world,evidence,bb,states,cells,radii,pen,stream,min_hp):
    start,end,place,face=event["frame"],event["end_frame"],event["placement"],event["view"]
    opening=deploy_to_first_cast(tables,card,evidence,deploy_frame=start,direction=place["direction"],facing=face,
        end_frame=end,manual_phase=event["press_phase"],next_manual_input=lambda earliest,born:event["press_frame"])
    cast=opening.cast
    if not cast or cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("未在窗口内进入九连发的技能模式")
    mode=cast["spell_frame"]
    atk,=(p["fields"] for p in evidence["character"]["components"]
          if p["path"]=="Modes/S2/Body/Attack" and p["script"]=="MultiRangedAttack")
    if (atk["_selectTargetTiming"]!=1 or atk["_selectTargetSource"]!=1 or atk["_animKey"] or atk["_alwaysIncludeTarget"]
            or atk["_waitForAttackEvent"] or atk["_waitAttackEventForAllAttacks"] or atk["_useMountGroup"]
            or atk["_limitToOneTargetAfterFirstRound"] or atk["_onlyFeedActiveBuffToFirstOne"] or atk["_onlyFeedActiveBuffToLastOne"]
            or atk["_castToFirstRoundTargetLocationsAtProjectileBirth"]):
        raise ValueError("九连发的逐发索敌、蓄力及浮空弹配置发生变化")
    dt=tables.fixed_timestep()
    step=int(dt*ONE)
    period=fixed_wait_frames(single(single(card["panel_attributes"]["baseAttackTime"])/dt))
    pre=fixed_wait_frames(single(atk["_preDelay"]/dt))
    gap=fixed_wait_frames(single(atk["_triggerDelta"]/dt))
    count=1+atk["_additionalTimes"]
    parts=evidence["projectiles"][atk["_projectileKey"]]["logic"]["components"]
    movement,=(p["fields"] for p in parts if p["script"]=="AdvancedMovement")
    if movement["_delayAfterReached"] or any(p["script"]=="HitBehaviour" for p in parts):
        raise ValueError("九连发的命中回调和移动组件发生变化")
    channel,=(p["fields"]["_default"] for p in evidence["skill"]["components"] if p["script"]=="ThreePartChannelingAnimation")
    begin,_=_selected_clip(tables,evidence,channel["beginAnim"],face)
    finish,_=_selected_clip(tables,evidence,channel["endAnim"],face)
    track=tables.read("bakemuzzledata/character/"+card["operator_id"]+"_defaultskin.json")["bakedAnimDatas"][face][
        "bakedMPDatas"][channel["loopAnim"]][str(atk["_mountPointType"])]
    first_loop=mode+fixed_wait_frames(single(begin["duration"]/dt))+1
    before_end=mode+fixed_wait_frames(single(single(card["skill_row"]["duration"])/dt))-fixed_wait_frames(single(finish["duration"]/dt))-1
    end=min(end,before_end)
    parts=evidence["character"]["components"]
    mode_owner,=(p["fields"] for p in parts if p["path"]=="Modes/S2" and p["script"]=="UnitMode")
    ability,=(p for p in parts if p["id"]==mode_owner["_attack"]["m_PathID"])
    host,=(p for p in parts if p["id"]==mode_owner["_attackTrigger"]["m_PathID"])
    selector,=(p for p in parts if p["id"]==atk["_selector"]["m_PathID"])
    if (ability["fields"]!=atk or host["script"]!="SelectorTrigger" or host["path"]!=selector["path"] or
            selector["script"]!="RandomSelector" or selector["fields"]["_selectNum"]!=1 or
            host["fields"]["_keepTarget"] or host["fields"]["_minTargetNum"]!=1):
        raise ValueError("九连发的宿主和逐发选择器引用或保留目标设置改变")
    logs=[dict(kind="deploy",frame=start,sp=opening.initial_sp),dict(kind="first_ready",frame=opening.first_ready_frame),*cast["events"]]
    rounds={}
    pending=[]
    wave=_WalkingWave(world,states,cells,radii,step)
    mass_aura=_mass_aura(tables,card,evidence,wave,mode)
    origin_cache={}
    next_attack=mode+1
    status="horizon" if end==event["end_frame"] else "skill_end_animation_boundary"
    trait=_trait_values(card)
    magic_aura=_magic_aura(tables,evidence,wave,mode,trait)

    def pick(rows):
        keyed={r["id"]:r for r in rows}
        key,=stream.select_targets(native_box_candidates(world,keyed),1)
        return keyed[key]

    for frame in range(mode+1,end+1):
        wave.advance(frame,logs)
        mass_aura.advance(min_hp)
        if magic_aura is not None:
            magic_aura.advance(min_hp)
        if frame==next_attack:
            rows=wave.admitted(frame,after_objects=False)
            if rows:
                before=stream.draws
                target=pick(rows)
                sign=1 if target["col"]>place["col"] else -1 if target["col"]<place["col"] else 0
                if not sign:
                    return dict(stop="barrage_requires_facing_state",through_frame=frame,events=logs)
                logs.append(dict(kind="barrage_start",frame=frame,target=target["id"],candidates=len(rows),draws=[before,stream.draws]))
                for index in range(count):
                    rounds[frame+pre-1+index*gap]=(index+1,sign)
                next_attack+=period
            else:
                next_attack+=3
        # Existing projectiles tick in creation order before the coroutine releases another round.
        for shot in pending[:]:
            key=shot["target"]
            if states[key].hp<=HP_EPSILON:
                logs.append(dict(kind="target_already_defeated",frame=frame,round=shot["index"],target=key))
                pending.remove(shot)
                continue
            enemy=wave.by_id(key,frame,projectile_born=shot["release"])
            if not enemy:
                return dict(stop="in_flight_target_left_stage",through_frame=frame,events=logs)
            mount=tuple(v+d for v,d in zip((enemy["col"],enemy["row"],enemy.get("height_offset",0)),shot["offset"]))
            if frame==shot["release"]+1:
                # After the first native position write the set is discrete.
                positions=_first_positions(shot["positions"],mount,movement["_speed"],dt)
                residuals=[tuple(single(single(v)-p) for v,p in zip(mount,pos)) for pos in positions]
                reached={single(single(single(x*x)+single(y*y))+single(z*z))<=single(single(.1)**2)
                         for x,y,z in residuals}
            else:
                moved={ordinary_trace_step(p,mount,effective_dt=dt,round_position=True,speed=movement["_speed"]) for p in shot["positions"]}
                positions={m.position for m in moved}
                reached={m.reached for m in moved}
            if len(reached)!=1:
                return dict(stop="loop_muzzle_phase_changes_reach",through_frame=frame,events=logs)
            if not reached.pop():
                shot["positions"]=positions
                continue
            pending.remove(shot)
            min_vuln,max_vuln=_vulnerability(magic_aura,key,trait)
            before=stream.draws
            elevated=wave.elevated(key,frame)
            impact=_hit(tables,card,bb,enemy,states[key],frame,pen,stream=stream,levitating=elevated,vulnerability=min_vuln,
                        effective_mass=mass_aura.weight(enemy))
            upper=states[key].hp
            lower=ordinary_arts_hit(attack_raw=impact["attack_q32"],resistance_raw=max(0,_raw_f32(enemy["res"])-pen),
                                    hp_raw=min_hp[key],output_scale_raw=max_vuln)
            min_hp[key]=lower.hp_after_raw
            if min_vuln!=max_vuln:
                impact.update(damage_bounds_q32=[impact["damage_q32"],lower.damage_raw],damage_q32=None,
                              damage_uncertainty="magic_fragility_aura_refresh_phase")
            if min_hp[key]!=upper:
                impact.update(hp_bounds_q32=[min_hp[key],upper],hp_after_q32=None)
            impact.update(round=shot["index"],release_frame=shot["release"],draws=[before,stream.draws],position=[enemy["row"],enemy["col"],mount[2]])
            logs.append(impact)
            if (upper>HP_EPSILON)!=(min_hp[key]>HP_EPSILON):
                return dict(stop="magic_fragility_changes_kill",through_frame=frame,events=logs)
            if impact["levitate_applied"] and states[key].hp>HP_EPSILON:
                if not wave.apply(key,frame,impact["initial_levitate_lifetime_q32"]):
                    return dict(stop="levitate_during_nonwalking_route_command",through_frame=frame,events=logs)
        mass_aura.contacts(frame,min_hp)
        if magic_aura is not None:
            magic_aura.contacts(frame,min_hp)
        if frame not in rounds:
            continue
        index,sign=rounds[frame]
        rows=wave.admitted(frame,after_objects=True)
        if not rows:
            # UpdateTargets returns false; AbilityStandard._DoCast ends this
            # coroutine. Already-created projectiles keep ticking independently.
            for remaining in range(1,count-index+1):
                rounds.pop(frame+remaining*gap,None)
            logs.append(dict(kind="no_target_at_spell",frame=frame,round=index,cancelled_later_rounds=count-index))
            continue
        before=stream.draws
        enemy=pick(rows)
        key=enemy["id"]
        if frame<first_loop:
            return dict(stop="release_before_loop_start",through_frame=frame,events=logs)
        cache=enemy["prefab"],sign
        if cache not in origin_cache:
            # Consider every native baked loop position. When all origins reach on the same tick
            # the result is exact even if the continuous loop's initial visual phase differs.
            posed=dict(enemy,col=place["col"]+sign,row=place["row"],tile_z=0)
            origins=[]
            for i in range(track["frameCount"]):
                origin,receiver=stationary_cast_mounts(tables,card["operator_id"],dict(action=channel["loopAnim"]),
                    place,posed,face,sample_seconds=i*dt/track["bakedStepInterval"])
                origins.append(origin)
            if any(len({p[i] for p in origins})!=1 for i in (1,2)):
                return dict(stop="loop_muzzle_requires_multiaxis_samples",through_frame=frame,events=logs)
            offset=(receiver[0]-posed["col"],receiver[1]-posed["row"],receiver[2])
            origin_cache[cache]=set(origins),offset
        origins,offset=origin_cache[cache]
        pending.append(dict(release=frame,index=index,target=key,positions=origins,offset=offset))
        logs.append(dict(kind="release",frame=frame,round=index,target=key,projectile=atk["_projectileKey"],
                         candidates=len(rows),draws=[before,stream.draws]))
    return dict(stop=status,through_frame=end,events=logs)
