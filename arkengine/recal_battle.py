"""Shared V079 battle state for H5-1. All operators, enemies and DP use one frame clock."""
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from math import ceil, floor, hypot

from .attack_range import circle_in_range
from .battle_outcome import ordinary_battle_outcome
from .caster_catalog import resolve_caster_loadout
from .damage import ordinary_arts_hit, ordinary_physical_hit
from .deployment import ManualDeploymentProtection
from .enemy_speed import movement_cap, scaled_move_attribute
from .enemy_silence import ReceiverSilence
from .eyja_aura import EYJA, FlameBreath
from .fixed import ONE, mul
from .forced_motion import enemy_push_profile, start_push, step_push
from .health_bounds import ordinary_life_points
from .natural_cost import NaturalCostClock
from .opening_spawns import native_wave_schedule
from .projectile_scene import stage_tile_positions
from .recal_aphris import AphrisCombat
from .recal_eyja import EyjaCombat
from .recal_scene import DEVICE, describe_selection, load_recal_scene
from .route_timing import birth_timer, route_clock_snapshots
from .routes import compile_route
from .stage_route_cursor import StageRouteCursor, stage_navigation
from .volley_damage import _raw_f32


@dataclass
class Operator:
    uid: str
    card: dict
    place: dict
    hp: int
    paid: int
    key: str
    alive: bool = True
    handler: object = None
    deployment_protection: ManualDeploymentProtection | None = None

    row = property(lambda self: self.place["row"])
    col = property(lambda self: self.place["col"])
    direction = property(lambda self: self.place["direction"])
    deployed_frame = property(lambda self: self.place["frame"])

    def enemy_selectable(self, frame):
        if self.deployment_protection is None:
            return self.alive
        return self.deployment_protection.enemy_selectable(frame, otherwise_selectable=self.alive)


class Enemy:
    def __init__(self,battle,spawn,order):
        self.spawn,self.order=spawn,order
        self.key,self.id,self.birth=spawn["enemy_key"],spawn["instance_id"],spawn["frame"]
        self.unit=battle.scene.enemies[self.key]
        self.prefab=self.unit["prefabKey"]["m_value"] or self.key
        self.level=battle.scene.enemy_level(self.key)
        self.airborne=self.unit["motion"]["m_value"]=="FLY"
        self.hp,self.res=_raw_f32(self.attr("maxHp")),self.attr("magicResistance")
        self.radius=battle.tables.enemy_circle(self.prefab)
        self.alive=True
        self.slow_until=self.slow_ratio=0
        self.forced=None
        self.silence=ReceiverSilence()
        self.stun_until=0
        route=compile_route(battle.scene,spawn["route_index"],self.key)
        if any(route["spawn_random_range"].values()) or any(c.get("randomize_reach_offset") for c in route["commands"]):
            raise ValueError("H5-1 出生或检查点偏移需要抽样")
        pos=tuple(route["start"][a]+route["spawn_offset"][b] for a,b in (("row","y"),("col","x")))
        self.previous=pos
        self.cursor=StageRouteCursor(route,route["commands"][0]["source"],pos,(0.,0.),
            battle.tables.enemy_steering(self.prefab),battle.dt,battle.clocks[spawn["wave_index"],spawn["fragment_index"]],
            battle.events,battle.navigation)
        delay=battle.born_delay(self.key,spawn["route_index"])
        self.attack_ready_from=self.birth+1+birth_timer({"_delayToBorn":delay,"_onlyDelayToBornOnTileStart":0})["first_default_request_update"]
        self.legs=self.route_costs()

    row=property(lambda self:self.cursor.position[0])
    col=property(lambda self:self.cursor.position[1])

    def attr(self,name,default=0):
        item=self.unit["attributes"].get(name)
        return item["m_value"] if item and item["m_defined"] else default

    def leg_cost(self,pos,dest):
        nav=self.cursor.navigation
        if nav:
            cell=tuple(floor(x+.5) for x in pos)
            goal=tuple(floor(x+.5) for x in dest)
            distances,following=nav.get_map(goal,self.cursor.route["flags"]["allowDiagonalMove"])
            if cell in distances:
                if cell==goal:
                    return 0.
                next_cell=following[cell]
                return distances[next_cell]+hypot(pos[0]-next_cell[0],pos[1]-next_cell[1])
        return hypot(pos[0]-dest[0],pos[1]-dest[1])

    def route_costs(self):
        cmds=self.cursor.route["commands"]
        legs=[None]*len(cmds)
        following=None
        after=0.
        for n in range(len(cmds)-1,-1,-1):
            c=cmds[n]
            if c["kind"] in ("MOVE","END","APPEAR_AT_POS"):
                point=(c["target"]["row"]+c.get("reach_offset",{}).get("y",0),
                       c["target"]["col"]+c.get("reach_offset",{}).get("x",0))
                if following is not None:
                    after+=self.leg_cost(point,following)
                following=point if c["kind"]!="APPEAR_AT_POS" else None
            legs[n]=(following,after)
        return legs

    def priority(self):
        dest,after=self.legs[self.cursor.index]
        distance=after+(self.leg_cost(self.cursor.position,dest) if dest else 0.)
        return (-self.attr("tauntLevel"),floor(distance*10),self.birth,self.order)


class RecalBattle:
    def __init__(self,tables,request):
        from .recal_device import RecalFields
        from .recal_enemy_attacks import EnemyAttacks
        self.request=request
        self.seed=request.get("battle_random_seed",0)
        self.tables=tables.for_combat(self.seed)
        self.scene=load_recal_scene(self.tables,request["stage_id"],request.get("recal_rune_ids"))
        self.dt=self.tables.fixed_timestep()
        self.frame=0
        level=self.scene.level
        opts=level["options"]
        grid=level["mapData"]["map"]
        self.rows,self.cols=len(grid),len(grid[0])
        self.navigation=stage_navigation(self.scene)
        self.tiles=stage_tile_positions(self.tables,replace(self.scene,level=dict(level,mapId="obt/hard/level_hard_05-01")))
        admitted,_=self.tables.admit_combat_actions(level)
        self.schedule=native_wave_schedule(level,self.born_delay,self.dt,admitted=admitted)
        if self.schedule["pending"]:
            raise ValueError(f"全息刷怪时钟存在待定事件 {self.schedule['pending']}")
        self.clocks=route_clock_snapshots(level,self.schedule,self.dt)
        self.rng=self.tables.combat_random(self.seed)
        self.events=[]
        self.cards={}
        self.operators={}
        self.handlers=[]
        self.enemies={}
        self.live={}
        self.spawns=defaultdict(list)
        self.actions=defaultdict(list)
        self.paid_count=Counter()
        self.ready_at=Counter()
        self.inventory=Counter({uid:item["initial_count"] for uid,item in self.scene.recal.token_cards.items()})
        self.clock=NaturalCostClock(opts["costIncreaseTime"],self.dt,request.get("clock",{}).get("cost_model","native_q32"))
        self.dp,self.cap,self.life=opts["initialCost"],opts["maxCost"],opts["maxLifePoint"]
        self.fields=RecalFields(self)
        self.hostile=EnemyAttacks(self)
        self.snow=None
        self.logos=None
        self.mantra=None
        self.cast_starts=defaultdict(list)
        for spawn in self.schedule["events"]:
            if spawn["kind"]=="spawn":
                self.spawns[spawn["frame"]].append(spawn)
        self.count=sum(map(len,self.spawns.values()))
        self.finishes=[]
        self.exits=[]
        for pick in request["squad"]:
            uid=pick.get("operator",pick.get("operator_id"))
            card=resolve_caster_loadout(self.tables,uid,**{k:v for k,v in pick.items()
                if k in ("skill","module","level","skill_level","module_level")})
            if any(k in pick and pick[k]!=card[k] for k in ("potential","elite")):
                raise ValueError("整局入口需要可解析的精英阶段和潜能")
            uid=card["operator_id"]
            if uid in self.cards:
                raise ValueError("全息编队干员重复")
            card["panel_attributes"]=self.scene.recal.prepare_panel(uid,"CASTER",card["panel_attributes"])
            self.cards[uid]=card
        if not 1<=len(self.cards)<=min(13,self.scene.recal.squad_limit):
            raise ValueError("编队超出所选词条或游戏的编队上限")
        self.flame=FlameBreath.from_card(self.tables,self.cards[EYJA]) if EYJA in self.cards else None
        for a in request["actions"]:
            if type(a.get("frame")) is not int or a["frame"]<0 or a.get("phase","after_tick")!="after_tick":
                raise ValueError("H5-1 输入需指定非负整数帧，并在 after_tick 执行")
            self.actions[a["frame"]].append(a)

    def born_delay(self,key,index):
        unit=self.scene.enemies[key]
        prefab=self.tables.enemy_birth(unit["prefabKey"]["m_value"] or key)
        start=self.scene.level["routes"][index]["startPosition"]
        if prefab["_onlyDelayToBornOnTileStart"] and self.scene.tile(**start)["tileKey"] not in ("tile_start","tile_flystart"):
            return 0
        return prefab["_delayToBorn"]

    def log(self,kind,**fields):
        self.events.append(dict(frame=self.frame,kind=kind,**fields))

    def skill_started(self, actor, frame):
        if self.mantra is None:
            return
        if frame == self.frame:
            self.mantra.grant(actor)
        else:
            self.cast_starts[frame].append(actor)

    def occupied(self):
        return {(a.row,a.col) for a in self.operators.values()}

    def targets(self,actor,cells,tile_only=False):
        candidates=(e for e in self.live.values() if not e.cursor.hidden and
                    ((round(e.row),round(e.col)) in cells if tile_only else circle_in_range(e.row,e.col,e.radius,cells,box_query=True)))
        return sorted(candidates,key=Enemy.priority)

    def attack(self,actor,bonus=0):
        flame=self.flame.bonus("CASTER",owner_on_field=EYJA in self.operators) if self.flame and actor.uid!=DEVICE else 0
        return mul(_raw_f32(actor.card["panel_attributes"]["atk"]),ONE+flame+_raw_f32(bonus))

    def attack_speed(self,actor):
        return max(10,min(600,actor.card["panel_attributes"]["attackSpeed"]*self.fields.attack_speed_multiplier(actor)))

    def sp_rate(self,actor):
        return 1+max((a.handler.sp_bonus(actor) for a in self.operators.values() if hasattr(a.handler,"sp_bonus")),default=0)

    def slow(self,target,ratio,until):
        if target.slow_until<=self.frame or ratio>=target.slow_ratio:
            target.slow_ratio,target.slow_until=ratio,until

    def disarmed(self,enemy):
        fear=getattr(enemy,"wolf_fear",None)
        return self.fields.disarmed(enemy) or self.frame<enemy.stun_until or bool(fear and fear.buffs)

    def blocker(self,enemy):
        if enemy.airborne:
            return None
        return next((a for a in self.operators.values() if self.scene.tile(a.row,a.col)["heightType"]=="LOWLAND"
            and a.card["panel_attributes"].get("blockCnt",0)>0 and hypot(enemy.row-a.row,enemy.col-a.col)<.5),None)

    def push(self,actor,target,force_level):
        if target.airborne:
            return
        profile=enemy_push_profile(self.tables,target.prefab)
        target.forced=start_push(self.tables,profile,(target.row,target.col),(target.row-actor.row,target.col-actor.col),
            force_level=force_level,weight=target.attr("massLevel"),frame=self.frame,previous=target.forced,
            move_multiplier=target.cursor.route["level_move_multiplier"])

    def arts(self,actor,target,attack_raw,pen_raw=0,scale_raw=ONE,kind="arts_hit",output_raw=ONE):
        if not target.alive:
            return 0
        pen=sum(_raw_f32(a.handler.penetration_at(actor)) for a in self.operators.values() if hasattr(a.handler,"penetration_at"))
        resistance=target.res+(self.snow.resistance_delta(target,self.frame) if self.snow else 0)
        attack_raw=mul(attack_raw,scale_raw)
        delta=0
        if self.logos:
            attack_raw,delta=self.logos.prepare_arts(actor,target,attack_raw)
        hit=ordinary_arts_hit(attack_raw=attack_raw,resistance_raw=_raw_f32(resistance)+delta,
            output_scale_raw=output_raw,
            fixed_res_penetration_raw=pen_raw+pen,received_scale_raw=_raw_f32(self.fields.damage_multiplier(actor,target,"arts")),
            hp_raw=target.hp)
        before=target.hp
        target.hp=hit.hp_after_raw
        if self.logos:
            self.logos.after_arts(actor,target,hit.damage_raw)
        self.log(kind,operator_id=actor.uid,target=target.id,hp_after=target.hp/ONE,damage=hit.damage_raw/ONE)
        reflect=self.fields.reflected_attack(target,"arts")
        if target.hp<=0:
            self.finish_enemy(target)
        if reflect and actor.alive:
            self.hurt(target,actor,reflect,"arts","sponsor_reflect")
        return before-target.hp

    def finish_enemy(self,target):
        if not target.alive:
            return
        target.alive=False
        self.live.pop(target.id)
        self.finishes.append(dict(instance=target.id,first_possible_frame=self.frame,finished_by_frame=self.frame,
                                  scheduler_killed_by_frame=self.frame))
        self.log("enemy_killed",target=target.id,enemy=target.key)
        loot={p["key"]:p for p in target.unit["talentBlackboard"] or []}
        if "loot.token_key" in loot:
            key=loot["loot.token_key"]["valueStr"]
            self.inventory[key]+=int(loot["loot.cnt"]["value"])
            self.log("token_gained",operator_id=key,count=self.inventory[key])

    def hurt(self,source,target,attack_raw,damage_type,kind):
        if not target.alive:
            return 0
        if target.deployment_protection and target.deployment_protection.active_at(self.frame):
            self.log("manual_deployment_damage_blocked",source=source.id,operator_id=target.uid,attack_kind=kind)
            return 0
        if hasattr(target.handler,"evades") and target.handler.evades(damage_type):
            self.log("magic_evaded",source=source.id,operator_id=target.uid)
            return 0
        panel=target.card["panel_attributes"]
        if self.logos:
            attack_raw=mul(attack_raw,self.logos.enemy_atk_scale(source))
        defense,res=target.handler.defenses() if hasattr(target.handler,"defenses") else (panel["def"],panel["magicResistance"])
        if damage_type in (2,"arts","magic","MAGICAL"):
            hit=ordinary_arts_hit(attack_raw=attack_raw,resistance_raw=_raw_f32(res),hp_raw=target.hp)
        elif damage_type in (1,"physical","PHYSICAL"):
            hit=ordinary_physical_hit(attack_raw=attack_raw,defense_raw=_raw_f32(defense),hp_raw=target.hp)
        else:
            raise ValueError(f"未知伤害类型 {damage_type}")
        before=target.hp
        target.hp=(target.handler.after_damage(source,hit.hp_after_raw,min(before,hit.damage_raw))
                   if hasattr(target.handler,"after_damage") else hit.hp_after_raw)
        self.log(kind,source=source.id,operator_id=target.uid,hp_after=target.hp/ONE,damage=hit.damage_raw/ONE)
        if target.hp<=0:
            self.leave(target,"operator_defeated")
        return before-target.hp

    def leave(self,actor,kind):
        actor.alive=False
        del self.operators[actor.key]
        actor.handler.close()
        if actor.uid!=DEVICE:
            step=_raw_f32(self.dt)
            self.ready_at[actor.uid]=self.frame+(_raw_f32(actor.card["panel_attributes"]["respawnTime"])+step-1)//step
        self.log(kind,operator_id=actor.uid,dp=self.dp)

    def deploy(self,a):
        uid=a["operator_id"]
        token=self.scene.recal.token_cards.get(uid)
        if uid not in self.cards and not token:
            raise ValueError(f"部署了编队外干员 {uid}")
        card=self.cards.get(uid) if not token else dict(operator_id=uid,panel_attributes=token["panel"],skill=1)
        panel=card["panel_attributes"]
        cost=int(panel["cost"]*(1 if token else 1+min(self.paid_count[uid],2)*.5))
        tile=self.scene.tile(a["row"],a["col"])
        if a.get("direction","UP") not in ("LEFT","RIGHT","UP","DOWN"):
            raise ValueError("部署朝向无效")
        if (tile["buildableType"] not in (token["buildable"] if token else ("RANGED","ALL"))
            or (a["row"],a["col"]) in self.occupied()
            or any((a["row"],a["col"]) in p.handler.active_relay_cells() for p in self.operators.values() if hasattr(p.handler,"active_relay_cells"))):
            reason="tile_unavailable"
        elif self.frame<self.ready_at[uid] or (token and not self.inventory[uid]) or (not token and uid in self.operators):
            reason="card_unavailable"
        elif not token and sum(p.uid!=DEVICE for p in self.operators.values())>=self.scene.level["options"]["characterLimit"]:
            reason="deployment_limit"
        elif self.dp<cost:
            reason="insufficient_dp"
        else:
            reason=None
        if reason:
            self.log("deploy_rejected",operator_id=uid,reason=reason,dp=self.dp,cost=cost)
            return
        factory=self.factory(uid,card)
        self.dp-=cost
        self.paid_count[uid]+=1
        key=uid if not token else f"{uid}#{self.paid_count[uid]}"
        if token:
            self.inventory[uid]-=1
            step=_raw_f32(self.dt)
            self.ready_at[uid]=self.frame+(_raw_f32(panel["respawnTime"])+step-1)//step
        place=dict(a,direction=a.get("direction","UP"),tile_z=self.tiles[a["row"],a["col"]]["position"]["z"])
        protection=None if token else ManualDeploymentProtection(self.frame)
        actor=Operator(uid,card,place,_raw_f32(panel["maxHp"]),cost,key,deployment_protection=protection)
        self.operators[key]=actor
        if uid=="char_1046_sbell2":
            from . import recal_sbell
            self.snow=recal_sbell
        if uid in ("char_4133_logos","char_4146_nymph","char_4204_mantra") and self.logos is None:
            from .recal_logos import LogosElements
            self.logos=LogosElements(self)
        self.log("deploy",operator_id=uid,instance_id=key,paid=cost,dp=self.dp,**{k:place[k] for k in ("row","col","direction")})
        actor.handler=factory(self,actor)
        self.handlers.append(actor.handler)

    @staticmethod
    def factory(uid,card):
        if uid=="char_377_gdglow" and card["skill"]==3:
            from .recal_goldenglow import GoldenglowCombat
            return GoldenglowCombat
        if uid=="char_501_durin":
            from .recal_durin import DurinCombat
            return DurinCombat
        if uid==EYJA and card["skill"]==3:
            return EyjaCombat
        if uid=="char_4229_aphris" and card["skill"] in (2,3):
            return AphrisCombat
        if uid=="char_213_mostma" and card["skill"]==3:
            from .recal_mostima import MostimaCombat
            return MostimaCombat
        if uid=="char_1046_sbell2" and card["skill"]==3:
            from .recal_sbell import SbellCombat
            return SbellCombat
        if uid=="char_1038_whitw2" and card["skill"]==1:
            from .recal_lappland import LapplandCombat
            return LapplandCombat
        if uid=="char_1038_whitw2" and card["skill"]==3:
            from .recal_wolves import WolfCombat
            return WolfCombat
        if uid=="char_4133_logos" and card["skill"] in (2,3):
            from .recal_logos import LogosCombat
            return LogosCombat
        if uid=="char_4141_marcil" and card["skill"]==3:
            from .recal_marcil import MarcilleCombat
            return MarcilleCombat
        if uid=="char_4146_nymph" and card["skill"] in (2,3):
            from .recal_nymph import NymphCombat
            return NymphCombat
        if uid=="char_4204_mantra" and card["skill"]==3:
            from .recal_mantra import MantraCombat
            return MantraCombat
        if uid==DEVICE:
            from .recal_device import DeviceCombat
            return DeviceCombat
        raise ValueError(f"H5-1 同场执行尚缺 {uid} 的技能 {card['skill']}")

    def player(self,a):
        kind=a.get("kind","deploy")
        if kind=="deploy":
            self.deploy(a)
            return
        actor=self.operators.get(a.get("instance_id",a["operator_id"]))
        if not actor:
            self.log(kind+"_rejected",operator_id=a["operator_id"],reason="not_deployed")
        elif kind=="withdraw":
            self.dp=min(self.cap,self.dp+min(int(actor.card["panel_attributes"]["cost"]),actor.paid//2))
            self.leave(actor,kind)
        elif kind=="skill" or kind=="chant_stop" and actor.uid=="char_4141_marcil":
            actor.handler.press()
        elif kind in ("relay_deploy","relay_withdraw") and hasattr(actor.handler,"relay"):
            actor.handler.relay(a)
        else:
            raise ValueError(f"无法识别的操作 {kind}")

    def movement(self,e):
        e.previous=e.cursor.position
        if self.frame<e.attack_ready_from:
            return
        ratio=self.fields.movement_multiplier(e)*(1-e.slow_ratio if self.frame<e.slow_until else 1.)
        for a in self.operators.values():
            if hasattr(a.handler,"move_multiplier"):
                ratio*=a.handler.move_multiplier(e)
        cap=e.cursor.normal if ratio==1 else movement_cap(scaled_move_attribute(e.cursor.route["base_move_speed"],_raw_f32(ratio)),e.cursor.route["level_move_multiplier"])
        if e.forced and e.forced.active:
            e.forced=step_push(self.tables,enemy_push_profile(self.tables,e.prefab),e.forced,frame=self.frame,scene=self.scene,continuous=True)
            e.cursor.position,e.cursor.velocity=e.forced.position,(0.,0.)
            return
        if getattr(e,"wolf_fear",None):
            from .recal_wolves import wolf_walk
            if wolf_walk(self,e,cap):
                return
        if self.snow and self.snow.override_walk(self,e,cap):
            return
        _,escaped=e.cursor.step(self.frame,e.id,cap,paused=self.frame<e.stun_until or self.hostile.holding(e) or bool(self.blocker(e)))
        if escaped:
            e.alive=False
            self.live.pop(e.id)
            loss=e.unit["lifePointReduce"]["m_value"]
            self.life=max(0,self.life-loss)
            self.exits.append(dict(instance=e.id,id=e.id,frame=self.frame,remaining_life=self.life,
                scheduler_release_window=[self.frame,self.frame],outcomes=["reached_exit"],life_loss_min=loss,life_loss_max=loss))
            self.log("enemy_escaped",target=e.id,enemy=e.key,hp=e.hp/ONE,life=self.life)

    def run(self):
        last_spawn=max(self.spawns)
        horizon=self.request.get("end_frame")
        finish=None
        settlement=None
        while True:
            f=self.frame
            if self.dp<self.cap:
                self.dp=min(self.cap,self.dp+self.clock.advance(f))
            for spawn in self.spawns.get(f,()):
                self.rng.advance_spawn_offsets(1)
                enemy=Enemy(self,spawn,len(self.enemies))
                self.enemies[enemy.id]=self.live[enemy.id]=enemy
                self.log("spawn",target=enemy.id,enemy=enemy.key,route=spawn["route_index"])
            for e in tuple(self.live.values()):
                self.movement(e)
            self.hostile.objects()
            if self.logos:
                self.logos.tick()
            for h in self.handlers:
                h.objects()
            self.fields.physics()
            self.hostile.coroutines()
            for h in self.handlers:
                h.coroutines()
            for actor in self.cast_starts.pop(f,()):
                if actor.alive:
                    self.mantra.grant(actor)
            for a in self.actions.get(f,()):
                before=self.dp
                self.player(a)
                self.log("player_input",action=a,dp_before=before,dp_after=self.dp,cost_bar=self.clock.bar(before,self.cap))
            if self.life<=0:
                stop="stage_defeat"
                break
            if finish is None and f>=max(last_spawn,self.schedule["waves"][-1]["fragments_complete_frame"]) and not self.live:
                case=dict(through_frame=f,exit_coverage=True,exits=self.exits,unresolved=[],finishes=self.finishes)
                life=ordinary_life_points(self.scene.level["options"]["maxLifePoint"],[case],f)
                settlement=ordinary_battle_outcome(self.scene,self.schedule,[case],life,self.dt,use_stage_objective=True)
                finish=settlement["finish_game_projection"]["frames"][-1]
            if finish is not None and f>=finish:
                stop="stage_victory_projected"
                break
            if f==horizon:
                stop="requested_frame"
                break
            self.frame+=1
        return dict(stage_id=self.request["stage_id"],score=self.scene.recal.score,squad_size=len(self.cards),
            rune_ids=self.scene.recal.rune_ids,rune_selection=describe_selection(self.scene.stage,self.scene.recal.rune_ids),
            battle_random_seed=self.seed,through_frame=f,stop=stop,
            remaining_life=self.life,dp=self.dp,scheduled=self.count,spawned=len(self.enemies),
            kills=sum(e["kind"]=="enemy_killed" for e in self.events),events=self.events,
            remaining={e.id:dict(enemy=e.key,hp=e.hp/ONE,row=e.row,col=e.col) for e in self.live.values()},
            combat_unresolved=[],settlement=settlement)


def run_recal_stage(tables,request):
    return RecalBattle(tables,request).run()
