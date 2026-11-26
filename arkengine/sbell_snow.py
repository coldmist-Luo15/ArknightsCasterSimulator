"""One existing snow-tile contact, from entry through receiver ticks and ordinary departure."""
from .caster_prefabs import caster_prefab_evidence
from .attack_range import range_cells
from .caster_ranges import DIRECTION
from .damage import HP_EPSILON, ordinary_arts_hit
from .fixed import ONE, mul
from .phalanx_skills import SNOW, _aura_scale, _talent
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def _grid(card,event):
    rows,cols = event["rows"],event["cols"]
    maximum = int(_talent(card,0,"1")["max_cast_cnt"])
    terrain = {tuple(p) for p in event["snow_eligible_tiles"]}
    if any(len(p)!=2 or any(type(v) is not int for v in p) or not (0<=p[0]<rows and 0<=p[1]<cols) for p in terrain):
        raise ValueError("可覆雪地块须为图内格坐标")
    snow = {}
    for tile in event["initial_snow"]:
        p = tile["row"],tile["col"]
        n = tile["layers"]
        if p in snow or p not in terrain or type(n) is not int or not 1<=n<=maximum:
            raise ValueError("初始积雪有重复坐标、超出地形或层数无效")
        snow[p] = n
    return terrain,snow,set(range_cells(card["base_range"],event["placement"],rows,cols)),maximum


def _tile_rows(snow,base):
    return [dict(row=r,col=c,layers=n,permanent=(r,c) in base) for (r,c),n in sorted(snow.items())]


def run_s1_snow_forward(tables,card,event):
    """S1 cast-start buff, using the live terrain and the owner's snow immediately before this cast."""
    if card["operator_id"]!=SNOW or card["skill"]!=1:
        raise ValueError("铃音吹雪的扩散须选择圣聆初雪一技能")
    start,end = event["frame"],event["end_frame"]
    if type(start) is not int or type(end) is not int or not 0<=start<=end:
        raise ValueError("扩雪起止帧无效")
    ev = caster_prefab_evidence(tables,SNOW,1,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    behaviour, = [r for r in ev["skill"]["components"] if r["script"]=="BuffDuringCastingFixed" and not r["path"]]
    buff, = behaviour["fields"]["_buffs"]
    nodes = tables.read("battle/buff_template_data.json")[buff["templateKey"]]["eventToActions"]
    if (not nodes["ON_BUFF_START"][0]["_clearS1SnowTileList"] or not nodes["ON_BUFF_TRIGGER"][0]["_triggerS1SnowForward"]
            or buff["triggerLifeType"]!=1 or buff["triggerCnt"] or not buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"]!=-1):
        raise ValueError("吹雪次数、首跳或清除地块列表的配置发生变化")
    budget = int(next(v["value"] for v in ev["skill_blackboard"] if v["key"]=="trig_cnt"))
    terrain,snow,base,maximum = _grid(card,event)
    dr,dc = DIRECTION[event["placement"]["direction"]]
    front = set()
    step = _raw_f32(tables.fixed_timestep())
    timer = RecoveryTimer(_raw_f32(buff["triggerInterval"]))
    log,emitted = [],0
    growth = set(event.get("base_growth_frames",[]))
    if any(type(f) is not int or not start<f<=end for f in growth):
        raise ValueError("新增的常驻层数须发生在本次开技后、观测截止帧前")
    stop = "horizon"
    through = start
    for frame in range(start+1,end+1):
        pulse = timer.advance(step)
        if frame in growth:
            updated = {p:min(maximum,snow.get(p,0)+1) for p in terrain & base if snow.get(p,0)<maximum}
            if updated and pulse:
                stop = "requires_order_of_coincident_snow_growth_and_forward_update"
                break
            snow.update(updated)
            if updated:
                log.append(dict(frame=frame,kind="base_snow_growth",changes=_tile_rows(updated,base)))
        through = frame
        if not pulse:
            continue
        # _S1Prepare seeds from occupied permanent tiles each pulse. Previously discovered
        # tiles stay in this cast's list. Destinations are committed only after sampling every source.
        front.update(base.intersection(snow))
        pending = {(r+dr,c+dc):snow[r,c] for r,c in front if (r,c) in snow and (r+dr,c+dc) in terrain}
        changes = []
        for p,forward in sorted(pending.items()):
            layers = max(snow.get(p,0),forward)
            if snow.get(p,0)!=layers:
                changes.append(dict(row=p[0],col=p[1],layers=layers,permanent=p in base))
            snow[p] = layers
        front.update(pending)
        log.append(dict(frame=frame,kind="snow_forward",changes=changes))
        emitted += 1
        if emitted==budget:
            stop = "all_forward_pulses"
            break
    return dict(status="s1_snow_forward",scope="stable_terrain_no_unlisted_layer_change_or_cast_interruption",
                stop=stop,through_frame=through,events=log,tiles=_tile_rows(snow,base))


def run_s2_snow_outward(tables,card,event):
    """One SnowTrigger update while S2 is active. The source supplies actual passability and occupied goal tiles."""
    if card["operator_id"]!=SNOW or card["skill"]!=2:
        raise ValueError("霜涛覆岭的扩散须选择圣聆初雪二技能")
    terrain,snow,base,maximum = _grid(card,event)
    ends = {tuple(p) for p in event["end_tiles"]}
    occupied = {tuple(p) for p in event.get("occupied_end_tiles",[])}
    if not occupied<=ends:
        raise ValueError("占用的目标点必须属于地图目标点")
    board = {b["key"]:b for b in card["skill_row"]["blackboard"]}
    cap = int(board["talent@max_cast_tile_count"]["value"])
    token = board["talent@token_key"]["valueStr"]
    graph = tables.read("battle/buff_template_data.json")["sbell2_t1[snow_trigger]"]["eventToActions"]["ON_BUFF_TRIGGER"]
    branch = graph[0]
    spread,freeze = branch["_succeedNodes"]
    if (branch["_conditionNode"]["_buffKeys"]!=["sbell2_s2[mark]"] or not spread["_triggerS2SnowOutward"] or
            not freeze["_tryCreateTokenEndTile"] or graph[1]["_abilityName"]!="SnowAccumulation"):
        raise ValueError("二技能向外扩散、冻结目标点或常驻地块叠层的回调顺序发生变化")
    budget = cap-len(set(snow)-base)
    candidates = {}
    for (r,c),n in snow.items():
        if n==maximum:
            for dr,dc in DIRECTION.values():
                p = r+dr,c+dc
                if p in terrain:
                    candidates[p] = candidates.get(p,0)+1
    origin = event["placement"]
    def priority(p):
        # S2SnowTileStruct stores Manhattan distance. The native comparer orders
        # the whole candidate list by distance, row, then column before debiting quota.
        return abs(p[0]-origin["row"])+abs(p[1]-origin["col"]),p[0],p[1]
    for p in sorted(candidates,key=priority):
        if p not in snow and p not in base:
            if budget<=0:
                continue
            budget -= 1
        snow[p] = min(maximum,snow.get(p,0)+candidates[p])
    frozen = [p for p in sorted(ends-occupied) if snow.get(p)==maximum]
    for p in frozen:
        del snow[p]
        terrain.discard(p)
    for p in base & terrain:
        snow[p] = min(maximum,snow.get(p,0)+1)
    return dict(status="s2_snow_outward",scope="one_active_talent_pulse",
                tokens=[dict(row=r,col=c,token=token) for r,c in frozen],tiles=_tile_rows(snow,base))


def natural_snow_pulses(tables,card,deploy_frame,end):
    """The non-mode talent group's passive buff first ticks after deployment INPUT."""
    _snow_rules(tables,card)
    step = _raw_f32(tables.fixed_timestep())
    timer = RecoveryTimer(_raw_f32(_talent(card,0,"1")["interval"]))
    for frame in range(deploy_frame+1,end+1):
        if timer.advance(step):
            yield frame


def s2_terrain_from_deploy(tables,card,event,cast_frame,end):
    """Replay the passive talent clock. S2 activation changes its action graph, not its running clock."""
    terrain,snow,base,maximum = _grid(card,{**event,"initial_snow":[]})
    talent = _talent(card,0,"1")
    if talent["first_snow"]:
        snow = {p:int(talent["first_snow"]) for p in terrain & base}
    log = []
    frozen = set()
    for frame in natural_snow_pulses(tables,card,event["frame"],end):
        if cast_frame is None or frame<=cast_frame:
            # Buffs update before the owner's automatic skill trigger.
            for p in base & terrain:
                snow[p] = min(maximum,snow.get(p,0)+1)
            continue
        old = snow
        pulse = run_s2_snow_outward(tables,card,{**event,"frame":frame,"initial_snow":_tile_rows(old,base),
                    "snow_eligible_tiles":terrain-frozen})
        snow = {(r["row"],r["col"]):r["layers"] for r in pulse["tiles"]}
        frozen.update((r["row"],r["col"]) for r in pulse["tokens"])
        log.append(dict(frame=frame,kind="snow_outward",changes=_tile_rows({p:n for p,n in snow.items() if old.get(p)!=n},base),
                        tokens=pulse["tokens"]))
    return dict(status="resolved",events=log,tiles=_tile_rows(snow,base),frozen_end_tiles=sorted(frozen))


def _snow_rules(tables,card):
    key = ("sbell_snow_contact",card["skill_id"],card["module"],card["module_level"])
    if key in tables._prefab_data:
        return tables._prefab_data[key]
    evidence = caster_prefab_evidence(tables,SNOW,card["skill"],skill_level=card["skill_level"],
                                     module_id=card["module"],module_level=card["module_level"])
    components = [c for c in evidence["character"]["components"] if c["active"] and c["enabled"]]
    listener, = (r["fields"] for r in components if r["script"]=="BuffToEnemyTileListener"
                 and r["path"].endswith("/SnowAccumulation"))
    talent_path = "Others/Direction/Talents/1"
    talent, = [c for c in components if c["path"]==talent_path and c["script"]=="Talent"]
    group, = [c for c in components if c["path"]==talent_path and c["script"]=="AbilityParallelGroup"]
    children = {r["m_PathID"] for r in group["fields"]["_abilities"]}
    passive, = [c for c in components if c["id"] in children and c["script"]=="PassiveBuffAbility"]
    if (talent["fields"]["_attachInDummy"] or passive["path"]!=talent_path+"/SnowTrigger" or
            not any(b["templateKey"]=="sbell2_t1[snow_trigger]" and b["waitFirstTriggerInterval"] and
                    b["firstTriggerInterval"]==-1 for b in passive["fields"]["_buffs"])):
        raise ValueError("无垠的雪景不再经部署天赋组同步挂入角色的周期 Buff")
    on_enter, = listener["_buffsWhenEnemyEnter"]
    on_leave, = listener["_buffsWhenEnemyLeave"]
    table = tables.read("battle/buff_template_data.json")
    enter = table[on_enter["templateKey"]]["eventToActions"]["ON_BUFF_START"]
    leave = table[on_leave["templateKey"]]["eventToActions"]["ON_BUFF_START"]
    dot = enter[-1]["_buff"]
    periodic = table[dot["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    slow = enter[6]["_buff"]["attributes"]["attributeModifiers"]
    excluded = set(enter[1]["_abnormalFlags"])
    if (enter[0]["_mode"]!="WALK" or excluded!={"INVINCIBLE","INVISIBLE","TARGET_FREE"} or
            enter[3]["_damageType"]!="MAGICAL" or enter[6]["_stackCntKey"]!="casted_times" or
            slow[0]["attributeType"]!="MOVE_SPEED" or slow[0]["formulaItem"]!="FINAL_SCALER" or
            enter[7]["_skillIndex"]!=1 or not dot["waitFirstTriggerInterval"] or
            periodic[0]["_buffKeys"]!=["sbell2_s2[mark]"] or set(periodic[1]["_abnormalFlags"])!=excluded or
            periodic[3]["_damageType"]!="MAGICAL" or periodic[3]["_attackType"]!="BUFF" or
            leave[0]["_conditionNode"]["_buffKeys"]!=["sbell2_t1[move_slow]"] or
            leave[2]["_mode"]!="WALK" or not leave[4]["_removeTileListener"] or leave[6]["_buffKey"]!="c2e_cold"):
        raise ValueError("积雪的进出监听、减速或周期伤害模板发生变化")
    rules = dict(excluded=excluded,dot_period=next(x["value"] for x in dot["blackboard"] if x["key"]=="interval"),
                 poll_period=next(x["value"] for x in on_enter["blackboard"] if x["key"]=="interval"),
                 enter_scale=enter[3]["_atkScaleVar"],dot_scale="talent@"+periodic[3]["_atkScaleVar"])
    tables._prefab_data[key] = rules
    return rules


def run_snow_contact(tables,card,event):
    """Movement enters after the receiver's buff update. Same-frame growth can come from either side of that update."""
    if card["operator_id"]!=SNOW:
        raise ValueError("积雪接触须选择圣聆初雪")
    first,last = event["frame"],event["end_frame"]
    destination = event.get("departure")
    if type(first) is not int or type(last) is not int or not 0<=first<=last or destination not in (None,"walk","teleport"):
        raise ValueError("积雪接触的首末帧或离开方式无效")
    window = event.get("skill_window")
    if window is not None and (len(window)!=2 or any(type(v) is not int for v in window) or not 0<=window[0]<window[1]):
        raise ValueError("技能生效窗口使用起点（含）和终点（不含）的逻辑帧")
    def active(frame):
        return window is not None and window[0]<=frame<window[1]

    rules = _snow_rules(tables,card)
    skill = {v["key"]:v["value"] for v in card["skill_row"]["blackboard"]}
    talent = _talent(card,0,"1")
    layers,permanent = event["layers"],event["permanent"]
    maximum = int(talent["max_cast_cnt"])
    if type(layers) is not int or not 1<=layers<=maximum or type(permanent) is not bool:
        raise ValueError("接触入口须为已存在且至少有一层的积雪")
    before_tick,after_tick = (event.get(k,[]) for k in ("layer_pulses_before_receiver","layer_pulses_after_receiver"))
    for pulses in (before_tick,after_tick):
        if any(type(f) is not int or f<first or f>last for f in pulses) or list(pulses)!=sorted(set(pulses)):
            raise ValueError("同一地块的叠雪帧须在片段内严格递增")
    before_tick,after_tick = set(before_tick),set(after_tick)
    if before_tick.intersection(after_tick) or first in before_tick:
        raise ValueError("进格帧更早的叠雪需计入初始层数，同一次叠雪只能出现一次")
    enemy = event["enemy"]
    if enemy["motion"] not in ("WALK","FLY") or type(enemy["hp_q32"]) is not int or enemy["hp_q32"]<=HP_EPSILON:
        raise ValueError("需指定存活敌人的移动类型和生命")
    applies = enemy["motion"]=="WALK" and not rules["excluded"].intersection(enemy.get("flags",[]))
    hp = enemy["hp_q32"]
    slow_layers = layers if applies else 0
    aura = _aura_scale(card,event.get("aura_count"))
    enter_aura = _aura_scale(card,event.get("aura_count_at_entry",event.get("aura_count")))
    events = []
    def hit(frame,kind,multiplier):
        nonlocal hp
        packet=_snow_hit(card,skill,dict(enemy,hp_q32=hp),frame,kind,multiplier,active(frame),
                         enter_aura if kind=="snow_entry_arts" else aura)
        hp=packet["hp_after_q32"]
        events.append(packet)

    if applies:
        hit(first,"snow_entry_arts",talent[rules["enter_scale"]])
    poll,dot = RecoveryTimer(_raw_f32(rules["poll_period"])),RecoveryTimer(_raw_f32(rules["dot_period"]))
    step = _raw_f32(tables.fixed_timestep())
    for frame in range(first,last+1):
        if hp<=HP_EPSILON:
            # Enemy finish can remove occupancy. The tile is not advanced past that callback.
            return dict(status="snow_contact",stop="enemy_defeated",events=events,hp_after_q32=hp,through_frame=frame)
        if frame in before_tick:
            layers = min(layers+1,maximum)
        if applies and frame>first:
            if poll.advance(step) and slow_layers!=layers:
                slow_layers = layers
                events.append(dict(frame=frame,kind="snow_slow_stacks",stacks=layers))
            if card["skill"]==2 and dot.advance(step) and active(frame):
                hit(frame,"snow_periodic_arts",skill[rules["dot_scale"]])
                if hp<=HP_EPSILON:
                    return dict(status="snow_contact",stop="enemy_defeated",events=events,hp_after_q32=hp,through_frame=frame)
        if frame in after_tick:
            layers = min(layers+1,maximum)
    if destination:
        if destination=="walk" and applies:
            layers = 0
            if card["skill"]==2 and active(last):
                events.append(dict(kind="cold_request",frame=last,seconds=skill["talent@cold"]))
        slow_layers = 0
    return dict(status="snow_contact",stop="departed" if destination else "horizon",through_frame=last,
                events=events,hp_after_q32=hp,layers=layers,tile_exists=permanent or layers>0,
                slow_stacks=slow_layers,move_speed_ratio_q32=ONE+slow_layers*_raw_f32(talent["move_speed"]))


def _snow_hit(card,skill,target,frame,kind,multiplier,active,aura):
    atk=_raw_f32(card["panel_attributes"]["atk"])
    res=target["resistance_q32"]
    if card["skill"]==3 and active:
        atk=mul(atk,ONE+_raw_f32(skill["atk"]))
        res-=_raw_f32(skill["magic_resist_penetrate_fixed"])
    hp=target["hp_q32"]
    damage=ordinary_arts_hit(attack_raw=mul(atk,_raw_f32(multiplier)),resistance_raw=res,hp_raw=hp)
    amount=mul(damage.damage_raw,aura)
    return dict(frame=frame,kind=kind,damage_q32=amount,hp_after_q32=hp-amount if hp-amount>=HP_EPSILON else 0)


class SnowTileContact:
    """The tile listener's attached slow and periodic damage on one live receiver."""
    def __init__(self,tables,card,tile,layers):
        self.card=card
        self.tile=tile
        self.layers=layers
        self.rules=_snow_rules(tables,card)
        self.talent=_talent(card,0,"1")
        self.skill={r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
        self.poll=RecoveryTimer(_raw_f32(self.rules["poll_period"]))
        self.dot=RecoveryTimer(_raw_f32(self.rules["dot_period"]))
        self.step=_raw_f32(tables.fixed_timestep())

    def enter(self,frame,target,active,aura_count):
        return _snow_hit(self.card,self.skill,target,frame,"snow_entry_arts",self.talent[self.rules["enter_scale"]],
                         active,_aura_scale(self.card,aura_count))

    def tick(self,frame,target,layers,active,aura_count):
        events=[]
        if self.poll.advance(self.step) and self.layers!=layers:
            self.layers=layers
            events.append(dict(frame=frame,kind="snow_slow_stacks",stacks=layers))
        if self.card["skill"]==2 and self.dot.advance(self.step) and active:
            events.append(_snow_hit(self.card,self.skill,target,frame,"snow_periodic_arts",self.skill[self.rules["dot_scale"]],
                                    active,_aura_scale(self.card,aura_count())))
        return events
