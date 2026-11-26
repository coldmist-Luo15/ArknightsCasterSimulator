"""Mantra’s deployment talent and its receiver update on the wave clock."""

from .damage import HP_EPSILON
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def palsy_delay(tables):
    graph=tables.read("battle/buff_template_data.json")["palsy[stack]"]["eventToActions"]
    grant=graph["ON_ABILITY_START"][3]
    interval=tables.read("buff_table.json")[grant["_buffKey"]]["triggerInterval"]
    timer=RecoveryTimer(_raw_f32(interval))
    frames=1
    while not timer.advance(_raw_f32(tables.fixed_timestep())):
        frames+=1
    return frames


class PalsyAura:
    """GlobalAuraAbility refreshes eligible recipients periodically and on mode changes."""
    def __init__(self,evidence,world):
        self.world=world
        self.period=next(p["fields"]["_interval"] for p in evidence["character"]["components"]
                         if p["script"]=="GlobalAuraAbility" and p["path"]=="Modes/Default/Body/Talents/1")
        self.clock=RecoveryTimer(_raw_f32(self.period))
        self.mode=None
        self.members=set()

    def advance(self,frame,step,mode,concealed=None):
        if not self.world.present:
            self.members.clear()
            self.mode=None
            return
        if mode!=self.mode:
            self.clock.remaining=0
            self.mode=mode
        if not self.clock.advance(step):
            return
        members=set()
        for key in self.world.active[frame]:
            flags=self.world.wave.flags.at(key,frame)
            if flags.intersection(("TARGET_FREE","INVINCIBLE","TOWER_TARGET_FREE")):
                continue
            if mode!=3 and ("INVISIBLE" in flags or "SLEEPING" in flags or concealed and concealed(key,frame)):
                continue
            members.add(key)
        self.members=members


def entry_tile_priority(tile,placement):
    """V079 TileSelector's MANHATTAN_OWNER_DIR_CENTER_PERFERED comparison."""
    row,col=tile[0]-placement["row"],tile[1]-placement["col"]
    direction=placement["direction"]
    forward={"UP":row,"DOWN":-row,"RIGHT":col,"LEFT":-col}[direction]
    lateral=col if direction in ("UP","DOWN") else row
    return abs(row)+abs(col),forward<0,abs(lateral)


def entry_talent(tables,card,evidence,world,deployment,*,seed=None,stream=None):
    """Select START lowlands and find the receiver updates that dispatch the tile-enter event."""
    parts=evidence["character"]["components"]
    tile,=(c["fields"] for c in parts if c["script"]=="TileSelector" and "TileListener" in c["path"])
    listen,=(c["fields"] for c in parts if c["script"]=="BuffToEnemyTileListener")
    buff,=listen["_buffsWhenEnemyEnter"]
    verify,add=evidence["templates"][buff["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    stack=add["_buff"]
    interrupt=evidence["templates"]["palsy[stack]"]["eventToActions"]["ON_ABILITY_START"]
    if ((tile["_filterType"],tile["_limitTargetNum"],tile["_maxNum"],tile["_tileSortType"],tile["_alwaysSort"])!=(23,1,1,5,1) or
            (tile["_options"]["checkTileTypes"],tile["_options"]["exceptTileTypes"],tile["_options"]["tileTypesMask"],
             tile["_options"]["checkHeightType"],tile["_options"]["heightType"])!=(1,0,8,1,0) or
            not any(c["id"]==listen["_targetValidator"]["m_PathID"] and c["script"]=="FilterTargetIsBornInStartTileValidator" for c in parts)
            or listen["_buffsWhenCasted"] or not buff["waitFirstTriggerInterval"] or buff["triggerCnt"]!=1
            or verify["_targetOptions"]["abnormalFlag"]!="INVISIBLE" or not verify["_targetOptions"]["ignoreTargetFree"]
            or not verify["_targetOptions"]["onlyIgnoreSomeOfTargetFreeCase"] or buff["firstTriggerInterval"]!=-1
            or not stack["loadFromDB"] or stack["buffKey"]!="palsy[stack]" or interrupt[-1]["_buffKey"]!="palsy_trigger"):
        raise ValueError("全局洞悉的侵入点筛选、延时施加或麻痹触发方式已变化")
    grid=world.scene.level["mapData"]["map"]
    start_types=tables.read("battle/battle_misc_table.json")["tileTypeDict"]["START"]
    starts=[(r,c) for r in range(len(grid)) for c in range(len(grid[0]))
            if (cell:=world.scene.tile(r,c))["tileKey"] in start_types and cell["heightType"]=="LOWLAND"]
    order={p:entry_tile_priority(p,deployment) for p in starts}
    best=min(order.values(),default=None)
    nearest=[p for p in starts if order[p][0]==best[0]]
    winners=[p for p in nearest if order[p]==best]
    selected=winners[0] if len(winners)==1 else None
    if stream is None and seed is not None:
        stream=tables.combat_random(seed)
    prior_draws=stream.draws if stream is not None else 0
    if stream is not None and starts:
        # RANDOM post-filter, then the sort's StableRandomShuffle and stable comparison.
        shuffled=stream.select_targets(starts,len(starts))
        shuffled=stream.select_targets(shuffled,len(shuffled))
        selected=min(shuffled,key=order.__getitem__)
    origins={selected} if selected is not None else set(winners)
    candidates=[]
    contacts={}
    unresolved=[]
    for frame,rows in world.history.items():
        # BornState exits in OBJECTS. Deployment INPUT installs the listener
        # synchronously, after that frame's existing enemy updates.
        if frame<=deployment["frame"]:
            continue
        for kind,row in rows:
            key=row["instance_id"]
            if kind!="born_default_requested" or world.stats[key]["palsy_immune"]:
                continue
            birth=world.births[key]
            origin=birth["route_start"]["row"],birth["route_start"]["col"]
            if origin not in origins:
                continue
            position=world.positions[frame].get(key)
            if position is not None and tuple(int(v+.5) for v in position)!=origin:
                continue
            candidates.append(key)
            if selected is not None:
                contacts[key]=frame
            else:
                unresolved.append(dict(target=key,frame=frame,reason="entry_shuffle"))
    return dict(nearest_start_tiles=nearest,priority_start_tiles=winners,selected_start_tile=selected,
                entry_random_draws=stream.draws-prior_draws if stream is not None else None,
                eligible_spawn_instances=candidates,
                first_trigger_interval_seconds=buff["triggerInterval"],source_template=buff["templateKey"],
                tile_enter_frames=contacts,unresolved=unresolved,exact_stack_grant_frames={},ordinary_enemy_attack_interrupts=0)


def entry_recipient_tick(talent,key,state,frame,world):
    enter=talent["tile_enter_frames"].get(key)
    if frame==enter and getattr(world,"present",True):
        state.mantra_entry_timer=RecoveryTimer(_raw_f32(talent["first_trigger_interval_seconds"]))
    timer=state.mantra_entry_timer
    if timer is not None and timer.advance(state.step):
        state.mantra_entry_timer=None
        # For ordinary unblocked walkers, active excludes disappear and exit.
        if key in world.active[frame] and state.hp>HP_EPSILON:
            state.add_palsy(frame,1,"mantra_t_2")
            talent["exact_stack_grant_frames"][key]=frame
