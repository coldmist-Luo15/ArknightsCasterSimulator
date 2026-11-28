"""Deploy a caster, resolve its channel, and sample native-wave targets at each pulse."""
from collections import defaultdict
from dataclasses import dataclass

from .attack_range import circle_in_range
from .caster_cast import run_committed_cast
from .caster_catalog import resolve_caster_loadout
from .caster_opening import CasterOpening,deploy_to_first_cast
from .caster_prefabs import _prefab,caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .caster_start import resolve_cast_start
from .fixed import ONE
from .mostima_aura import MOSTIMA,apply_to_live_team
from .opening_replay import _instance_motion_events, _motion_profiles, _run_with_vent_removals, run_opening
from .opening_scene import OpeningScene, PinnedTables, load_opening_scene
from .skill_recovery import RecoveryTimer, deploy_skill
from .volley_damage import _position_samples


class UnsupportedChannelScene(ValueError):
    """The stage needs combat or route behaviour outside the current channel simulation."""


COCKTAIL_THROWER = "enemy_1028_mocock"


def require_ordinary_highland(scene,deployments):
    terrain={"tile_end","tile_floor","tile_flystart","tile_forbidden","tile_road","tile_start","tile_wall"}
    level=scene.level
    if (scene.predefines or any(scene.tile(a["row"],a["col"])["heightType"]!="HIGHLAND" for a in deployments)
            or level["mapData"]["effects"] or level["globalBuffs"] or level.get("branches")
            or any(t["tileKey"] not in terrain or t["blackboard"] or t["effects"] for t in level["mapData"]["tiles"])
            or any(r["difficultyMask"]!="FOUR_STAR" for r in level["runes"] or ())):
        raise UnsupportedChannelScene("该波次需要普通高台部署，额外地形或关卡机制尚未接入")


def ordinary_melee_enemy(tables,scene,enemy,*,allow_flying=False,allow_ranged=False,allow_thrower=False):
    unit=scene.enemies[enemy]
    prefab=_prefab(tables,"enemies",unit["prefabKey"]["m_value"] or enemy)
    ordinary={"Enemy","MeleeAttack","MoveController","UnitMode","FaceSwitcher","ShadowController",
              "SingleSpineAnimator","SkeletonAnimation","UberEffectEmitter"}
    components=[c for c in (prefab or {}).get("components",())
                if c["active"] and c["enabled"] and c["type"]=="MonoBehaviour"]
    followers=[c for c in components if c["script"]=="BoneFollower"]
    if len(followers)==1 and followers[0]["fields"]["boneName"]=="C_Weapon":
        path=followers[0]["path"]
        branch=[c for c in prefab["components"] if c["path"]==path or c["path"].startswith(path+"/")]
        transform=[c["id"] for c in branch if c["type"]=="Transform"]
        # V079's armed melee soldier binds this leaf to SpecialPoint0. Its root collider and hit mount are separate.
        if (len(branch)==2 and len(transform)==1 and any(c["script"]=="SingleSpineAnimator" and
                c["fields"].get("_specialPoint0",{}).get("m_PathID")==transform[0] and
                c["fields"]["_hitTransform"]["m_PathID"]!=transform[0] for c in components)):
            ordinary.add("BoneFollower")
    roots=[c["fields"] for c in components if c["script"]=="Enemy"]
    is_flying = allow_flying and unit["motion"]["m_value"]=="FLY"
    if is_flying and unit["applyWay"]["m_value"]=="NONE":
        ordinary.add("EmptyAnimatedAbility")
    is_ranged = (allow_ranged and enemy in ("enemy_1003_ncbow","enemy_1003_ncbow_2")
                 or allow_thrower and enemy==COCKTAIL_THROWER)
    if is_ranged:
        ordinary.update(("RangedAttack","AdvancedSelector","CircleRange","SelectorTrigger","BoneFollower"))
    if (len(roots)!=1 or len(roots[0]["_modes"])!=1 or roots[0]["_canNotExit"]
            or roots[0]["_useSpecificDeadAnim"] or roots[0]["_useSpecificReachExitAnim"]
            or any(c["script"] not in ordinary or c["fields"].get("_activeBuffs") or c["fields"].get("_passiveBuffs")
                   for c in components)
            or unit["skills"] or unit["talentBlackboard"] or unit["attributes"]["hpRecoveryPerSec"]["m_value"]
            or (unit["motion"]["m_value"]!="WALK" and not is_flying)
            or unit["applyWay"]["m_value"] not in (("MELEE","NONE") if is_flying else ("MELEE","RANGED") if is_ranged else ("MELEE",))
            or unit["notCountInTotal"]["m_value"] or not unit["lifePointReduce"]["m_defined"]
            or unit["lifePointReduce"]["m_value"]<0):
        raise UnsupportedChannelScene(f"{enemy} 的移动、攻击或独立能力需要加入这段回放")
    return unit


@dataclass
class ChannelStage:
    tables: PinnedTables
    request: dict
    card: dict
    config: dict
    deploy: dict
    button: dict | None
    opening: dict
    scene: OpeningScene
    placement_event: dict
    geometry: dict
    evidence: dict
    automatic_opening: CasterOpening | None = None

    def sp_at_button(self,periodic_grant=None):
        gauge,recovery=deploy_skill(self.tables,self.card,self.deploy["frame"])
        if gauge is None:
            raise ValueError(f"引导技能的部署技力尚未解析：{recovery['reason']}")
        if self.card["operator_id"]==MOSTIMA:
            apply_to_live_team(self.tables,self.card,{MOSTIMA:gauge},self.deploy["frame"],active=True)
        timer=RecoveryTimer(int(periodic_grant["period_seconds"]*ONE)) if periodic_grant else None
        for _ in range(self.button["frame"]-self.deploy["frame"]):
            gauge.state.advance(gauge.step)
            if timer and timer.advance(gauge.step):
                gauge.state.points=min(gauge.state.maximum,gauge.state.points+int(periodic_grant["sp_gain_attempt"]))
        return gauge.state.points

    def cast(self, *, periodic_grant=None,sp_before=None):
        """Resolve natural SP and an optional verified periodic SP grant before the button."""
        points=self.sp_at_button(periodic_grant) if sp_before is None else sp_before
        if points<self.card["skill_row"]["spData"]["spCost"]:
            raise ValueError("所选配置的技力来源在按键帧尚未保证技力充满")
        direction=self.deploy["direction"]
        facing="Back" if direction=="UP" else "Front"
        cast=resolve_cast_start(self.tables,self.evidence,frame=self.button["frame"],sp=points,
                                phase=self.button["phase"],direction=direction,facing=facing)
        if cast.get("cast_frame") is None:
            raise ValueError(f"技能输入尚未提交：{cast['status']}")
        timeline=run_committed_cast(self.tables,self.evidence,horizon=self.request["end_frame"]-cast["cast_frame"],
                                    direction=direction,facing=facing)
        return cast,timeline

    def sample_pulses(self,frames,end,*,enemy_key=None,track_escapes=False):
        """These channels reselect on spell in the coroutine phase, after enemy movement."""
        ability, = (a["fields"] for a in self.evidence["skill"]["components"] if a["script"]=="ChannelingMeleeAttack")
        if ability["_selectTargetTiming"]!=1 or ability["_alwaysIncludeTarget"] or ability["_selectTargetSource"]!=1:
            raise ValueError("引导脉冲的重新索敌时点或目标来源需要核对")
        births,radii,positions,active,escaped=channel_targets(self.tables,self.scene,self.opening,frames=set(frames),end=end,
            first_born_tick_delay=self.request["movement"]["first_born_tick_delay_frames"],enemy_key=enemy_key)
        if escaped and not track_escapes:
            raise UnsupportedChannelScene("所选引导技能的回放时窗内已有敌人抵达蓝门")
        contact=self.geometry.get("contact","circle")
        if contact not in ("circle","enemy_tile_coordinate"):
            raise ValueError("引导技能需要已核验的范围接触方式")
        return ChannelTargets(births,radii,positions,active,set(self.geometry["cells"]),contact,escaped)


def prepare_channel(tables,request,*,operator,skill,module=None,stage_id=None,direction=None,range_options=None,
                    supporting_deploys=(),phalanx_default_mode=False,contact_checked_during_replay=False,automatic=False,
                    allow_no_module=False,allow_idle=False,completing_waves=None,allow_partial_movement=False,live_deployment=False):
    supports={a["operator_id"] for a in supporting_deploys}
    primary=[p for p in request["squad"] if p["operator_id"] not in supports]
    pick,=primary
    module=module or pick.get("module")
    if not module and not allow_no_module:
        raise ValueError("关卡回放需要选择一个常规模组")
    card=resolve_caster_loadout(tables,operator,skill,module,module_level=pick.get("module_level"),
                               skill_level=pick.get("skill_level"),level=pick.get("level"))
    config={k:card[k] for k in ("operator_id","elite","level","potential","skill","skill_level","module","module_level")}
    if allow_partial_movement and "skin_id" in pick:
        config["skin_id"]=pick["skin_id"]
    if primary!=[config] or (stage_id and request["stage_id"]!=stage_id):
        raise ValueError("关卡、技能或模组与该回放的编队不符")
    deploy,*buttons=[a for a in request["actions"] if a["operator_id"] not in supports]
    if (automatic and (buttons or card["skill_row"]["skillType"]!="AUTO") or
            not automatic and (len(buttons)>1 or not buttons and not allow_idle)):
        raise ValueError("技能触发类型与场上的按键数不符")
    button=buttons[0] if buttons else None
    if (deploy["operator_id"]!=operator or (direction and deploy["direction"]!=direction) or button is not None and
            (button!=dict(kind="skill",operator_id=operator,phase="after_tick",frame=button["frame"]) or
             not deploy["frame"]<button["frame"]<=request["end_frame"])):
        raise ValueError("引导回放需要指定朝向的有效部署和冻结输入阶段的技能操作")
    base=dict(request,actions=[a for a in request["actions"] if a is deploy or a in supporting_deploys],track_attack_range=True)
    if live_deployment:
        base["actions"]=[a for a in base["actions"] if a["frame"]<=request["end_frame"]]
    base.pop("scene_model",None)
    base.pop("enemy_target_flags",None)
    base.pop("incoming_hits",None)
    if completing_waves is None:
        opening=run_opening(base,tables=tables)
    else:
        opening=_run_with_vent_removals(base,tables=tables,wave_releases=completing_waves,combat_clock=allow_partial_movement)
        pending=opening.get("wave_schedule",{}).get("pending") or {}
        if (pending and not allow_partial_movement and
                pending["frame"]<=base["end_frame"]):
            opening=_run_with_vent_removals(base,tables=tables,wave_releases=completing_waves,
                                           hold_wave=pending["wave_index"])
    scene=load_opening_scene(stage_id=request["stage_id"],tables=tables)
    placed={e["operator_id"]:e for e in opening["events"] if e["kind"]=="deployed"}
    if not live_deployment and any(placed.get(a["operator_id"],{}).get("frame")!=a["frame"] for a in supporting_deploys):
        raise UnsupportedChannelScene("协助干员部署失败，须按实际已部署阵容重算")
    placement=placed.get(operator)
    if "skin_id" in config:
        card["battle_skin"]=opening["initial"]["cards"][operator]["battle_skin"]
    evidence=caster_prefab_evidence(tables,operator,skill,skill_level=card["skill_level"],module_id=module,module_level=card["module_level"])
    auto_opening=None
    if automatic:
        auto_opening=deploy_to_first_cast(tables,card,evidence,deploy_frame=deploy["frame"],end_frame=request["end_frame"],
                facing="Back" if deploy["direction"]=="UP" else "Front",direction=deploy["direction"])
    until=request["end_frame"]
    if button:
        until=button["frame"]
    elif auto_opening is not None:
        until=auto_opening.through_frame-int(auto_opening.cast is not None)
    prior_contact=any(e["kind"]=="attack_range_enter" and e["operator_id"]==operator and e["frame"]<=until
                      for e in opening["events"])
    if prior_contact and phalanx_default_mode and placement is not None:
        from .caster_runtime import default_attack,phalanx_default
        profile=default_attack(tables,operator,0)
        prior_contact=phalanx_default(tables,card,profile,placement,until,[],auto_opening=auto_opening)["status"]!="phalanx_default_disarmed"
    if (not live_deployment and (placement is None or placement["frame"]!=deploy["frame"]) or
            (opening["movement"]["unresolved_profiles"] and not allow_partial_movement) or (scene.predefines and not allow_partial_movement) or
            (prior_contact and not contact_checked_during_replay)):
        raise UnsupportedChannelScene("本段需要开技前无普攻、无预置干员，且部署和敌人移动均可解析")
    grid=scene.level["mapData"]["map"]
    if live_deployment and card["skill_id"] == "skchr_necras_1" and range_options is None:
        range_options = {"area": "summon_tiles"}
    geometry=skill_range_cells(tables,card,dict(placement=deploy,rows=len(grid),cols=len(grid[0]),**(range_options or {})))
    return ChannelStage(tables=tables,request=request,card=card,config=config,deploy=deploy,button=button,
                        opening=opening,scene=scene,placement_event=placement,geometry=geometry,evidence=evidence,automatic_opening=auto_opening)


def object_position_frame(birth,frame,positions,active,identity,*,observer_born,observer_phase,same_phase_rule=None):
    """Enemy center visible to an ordinary object tick. Native wave births occur in COROUTINES."""
    if birth["phase"]!="COROUTINES":
        raise ValueError("敌人分配阶段不在普通出兵协程")
    if observer_phase not in ("INPUT","COROUTINES"):
        raise ValueError("需要观察者的实际分配阶段")
    if observer_phase=="INPUT" and observer_born==frame:
        return frame-1 if birth["frame"]==frame else frame
    if birth["frame"]!=observer_born:
        return frame if birth["frame"]<observer_born else frame-1
    if observer_phase=="INPUT":
        return frame-1
    if positions[frame].get(identity)==positions[frame-1].get(identity) and (
            (identity in active[frame])==(identity in active[frame-1])):
        return frame
    if same_phase_rule:
        return same_phase_rule(identity,frame)
    raise ValueError(f"F{observer_born} 同一协程阶段分配了观察者和敌人，需要实际实例顺序")


@dataclass
class ChannelTargets:
    births: dict
    radii: dict
    positions: dict
    active: dict
    cells: set
    contact: str
    escaped: dict

    def contains(self,identity,pos):
        if pos is None:
            return False
        if self.contact=="enemy_tile_coordinate":
            return (round(pos[0]),round(pos[1])) in self.cells
        return circle_in_range(*pos,self.radii[identity],self.cells,box_query=True)

    def at(self,frame,alive):
        for identity,pos in self.positions[frame].items():
            if not alive(identity):
                continue
            if self.contains(identity,pos):
                if identity not in self.active[frame]:
                    raise UnsupportedChannelScene("目标出生或显隐状态待核")
                yield identity,pos


def channel_targets(tables, scene, stage, *, frames, first_born_tick_delay, end, with_history=False,
                    enemy_key="enemy_1007_slime_3",native_first_walk=False,allow_later_waves=False,route_clocks=None,
                    allow_partial_movement=False):
    if not stage["movement"]["continued_move_position_rounding"]:
        raise ValueError("此连续回放需要启用原生移动坐标取整")
    stop=stage.get("stop")
    if stop and stop["frame"]<=end:
        raise UnsupportedChannelScene(f"F{stop['frame']} 的原生开局回放停止于 {stop['reason']}")
    births=[e for e in stage["events"] if e["kind"]=="spawn" and e["frame"]<=end]
    by_id={b["instance_id"]:b for b in births}
    if (any((not allow_later_waves and (b["wave_index"] or b["block_fragment"]))
             or (enemy_key and b["enemy_key"]!=enemy_key)
                          for b in births) or stage["clock"]["spawn_schedule"]!="native_wave"):
        raise UnsupportedChannelScene("该时窗需要不受死亡门槛影响的原生波次和可处理的敌人类型")
    if not births:
        empty=({}, {}, {f:{} for f in frames}, {f:set() for f in frames}, {})
        return (*empty,{}) if with_history else empty
    profiles=_motion_profiles(scene,tables,births,track_range=True,native_portals=True,round_position=True,
                             native_first_walk=native_first_walk,route_clocks=route_clocks,born_delay=first_born_tick_delay)
    if native_first_walk and profiles["unresolved_profiles"] and not allow_partial_movement:
        raise UnsupportedChannelScene("目标首段无法恢复原生移动速度与坐标")
    history=defaultdict(list)
    radii={}
    escaped={}
    navigation=None
    for identity,birth in by_id.items():
        p=profiles["profiles"][profiles["instance_profiles"][identity]]
        radii[identity]=p["circle_radius"]
        generated=_instance_motion_events(p,birth["frame"],None,first_born_tick_delay)
        if allow_partial_movement and p["projection"]["status"]=="unresolved":
            generated=[(birth["frame"],"range_motion_unresolved",dict(source=p["route"]["commands"][0]["source"],
                        reason=p["projection"]["reason"]))]
        for f,kind,fields in generated:
            if f>end:
                break
            if kind in ("range_motion_unresolved","birth_timer_unresolved"):
                if not allow_partial_movement:
                    raise UnsupportedChannelScene("此区间出现未解的敌人移动或出生状态")
                from .stage_route_cursor import extend_route,stage_navigation
                if navigation is None:
                    navigation=stage_navigation(scene)
                source=fields.get("source",p["route"]["commands"][0]["source"])
                notes=[dict(kind="rule_applied",frame=f,target=identity,rule="authored_route_cursor",
                            source=source,reason=fields.get("reason",kind))]
                exit_at=extend_route(history,identity,birth,p,f,end,source,
                    (route_clocks or {}).get((birth["wave_index"],birth["fragment_index"])),
                    tables.fixed_timestep(),navigation,notes,first_born_tick_delay)
                for note in notes:
                    history[note["frame"]].append(("route_rule",dict(note,instance_id=identity)))
                if exit_at is not None:
                    escaped[identity]=exit_at
                break
            if kind=="route_end_in_range":
                escaped[identity]=f
            history[f].append((kind,dict(instance_id=identity,**fields)))
    active={}
    snapshots=_position_samples(history,frames,active)
    result=by_id,radii,snapshots,active,escaped
    return (*result,history) if with_history else result
