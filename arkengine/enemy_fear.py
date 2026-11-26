"""CN V079 ordinary FEARED movement and its independent per-buff cursors."""
from math import isfinite

from .baked_mount import single
from .battle_random import BattleRandom
from .enemy_motion import ROUTE_REACH_DISTANCE, advance_direct_move, coast_during_wait
from .enemy_speed import move_attribute, movement_cap
from .fear_tiles import build_fear_tiles, select_fear_tile
from .fixed import ONE, mul
from .skill_recovery import _divide
from .volley_damage import _raw_f32


def _cell(position):
    return tuple(round(single(v)) for v in position)


def _within(position, target, radius):
    row, col = (single(a-b) for a,b in zip(position,target))
    return single(single(row*row)+single(col*col)) <= single(radius*radius)


class FearMotion:
    """Drive one enemy at its OBJECTS phase. External events may share both RNGs across actors."""

    def __init__(self, tables, *, enemy, terrain, start_frame, stream, map_stream):
        if type(start_frame) is not int or start_frame < 0:
            raise ValueError("需要有效的起始逻辑帧")
        self.frame = start_frame
        self.position = (single(enemy["row"]), single(enemy["col"]))
        self.velocity = tuple(map(single,enemy["velocity"]))
        self.original = tuple(map(single,enemy["route_target"]))
        if len(self.velocity)!=2 or len(self.original)!=2 or not all(isfinite(v) for v in (*self.position,*self.velocity,*self.original)):
            raise ValueError("需要两个有限的速度分量和路线坐标")
        self.status, self.reason = "tracked", None
        self.events = []
        self.buffs = {}
        self.goal = self.original
        self.need_checkpoint = False
        self.stream, self.map_stream = stream, map_stream
        self.navigation=terrain.get("navigation")
        self.cells = set(self.navigation.cost) if self.navigation else {tuple(cell) for cell in terrain["walkable"]}
        if not self.cells or any(len(cell)!=2 or any(type(v) is not int or v<0 for v in cell) for cell in self.cells):
            raise ValueError("恐惧可通行地块须按非负整数 (row,col) 提供")
        rows,cols = zip(*self.cells)
        self.rows,self.cols=(self.navigation.rows,self.navigation.cols) if self.navigation else (max(rows)+1,max(cols)+1)
        exit_cell = tuple(terrain["exit"])
        self.excluded = {tuple(cell) for cell in terrain.get("excluded",())} | {exit_cell}
        self.original_reachable=self.cells
        self.diagonal=enemy.get("allow_diagonal",True)
        if self.navigation:
            self.excluded |= self.navigation.excluded
            self.original_reachable=self.navigation.get_map(exit_cell,self.diagonal)[0]
        if not self.navigation and (len(set(rows))>1 and len(set(cols))>1 or len(self.cells)!=1+max(rows)-min(rows)+max(cols)-min(cols)
                or any(cell not in self.cells for cell in (_cell(self.position),_cell(self.original),exit_cell))):
            self.status,self.reason = "unresolved","requires_native_navigation_nodes_for_turns_or_obstacles"
        elif enemy.get("blocked") or enemy.get("movement_state","MOVE")!="MOVE":
            self.status,self.reason = "unresolved","requires_current_state_and_block_release_transition"
        self.wait = enemy.get("remaining_wait_frames",0)
        if type(self.wait) is not int or self.wait<0:
            raise ValueError("原路线剩余停驻帧须为非负整数")
        self.start = start_frame
        self.dt = tables.fixed_timestep()
        self.step = int(self.dt*ONE)
        self.resistance_factor = enemy.get("one_minus_status_resistance",1)
        self.fear_resistance = enemy.get("fear_resistance",0)
        if enemy.get("status_resistance") and "one_minus_status_resistance" not in enemy:
            self.status,self.reason = "unresolved","requires_actual_one_minus_status_resistance_attribute"
        self.steering = tables.enemy_steering(enemy["prefab"])
        self.base_speed = enemy["move_speed"]
        self.move_ratio = enemy.get("move_ratio_delta",0)
        self.level_multiplier = terrain.get("level_move_multiplier",1)

    def _at(self,frame):
        if type(frame) is not int or frame!=self.frame:
            raise ValueError("增删恐惧须使用敌人当前所在逻辑帧")

    def _buff_delta(self):
        if any(type(v) not in (int,float) or not isfinite(v) or not 0<=v<=1
               for v in (self.resistance_factor,self.fear_resistance)):
            raise ValueError("状态抗性的剩余倍率和恐惧抗性须在 0 至 1 之间")
        scale=mul(_raw_f32(self.resistance_factor),ONE-_raw_f32(self.fear_resistance))
        return _divide(self.step,max(_raw_f32(.001),scale))

    def _select(self):
        key = next(reversed(self.buffs))
        buff = self.buffs[key]
        probe=(self.navigation.probe if self.navigation else
               lambda a,b:(b in self.cells,abs(a[0]-b[0])+abs(a[1]-b[1])))
        pick = select_fear_tile(buff["pool"],origin=_cell(self.position),random_stream=self.stream,
            map_random=self.map_stream,route_probe=probe)
        buff["pool"] = pick["remaining_candidates"]
        row,col = pick["destination"]
        dx,dy = pick["reach_offset"]
        self.goal = single(row+dy),single(col+dx)
        self.need_checkpoint = False
        self.events.append(dict(pick,kind="fear_checkpoint",frame=self.frame,buff_id=key,map_target=self.goal,
                                remaining_candidates=list(buff["pool"])))

    def apply(self,frame,source,duration_seconds,buff_id):
        """ON_BUFF_START/AddFearTargetTiles. A key identifies a live native buff instance."""
        self._at(frame)
        if not isfinite(duration_seconds) or duration_seconds<=0:
            raise ValueError("须提供实际生效的恐惧持续时间")
        if self.status!="tracked":
            return
        if not isinstance(self.stream,BattleRandom) or not isinstance(self.map_stream,BattleRandom) or self.stream is self.map_stream:
            self.status,self.reason = "unresolved","requires_independent_map_and_battle_random_states"
            return
        pool = build_fear_tiles(rows=self.rows,cols=self.cols,target_position=self.position,source_position=source,
            original_reachable=self.original_reachable.__contains__,excluded_tile=self.excluded.__contains__)
        if buff_id in self.buffs:
            # The existing-key arm is AddRange only; the buff timer belongs to its owner.
            self.buffs[buff_id]["pool"].extend(pool)
            return
        duration = _raw_f32(duration_seconds)
        delta = self._buff_delta()
        expiry = frame+(duration+delta-1)//delta
        self.buffs[buff_id] = dict(pool=pool,remaining=duration,expiry=expiry)
        self._select()

    def remove(self,frame,buff_id):
        self._at(frame)
        if buff_id not in self.buffs:
            return
        current = next(reversed(self.buffs))
        del self.buffs[buff_id]
        self.events.append(dict(kind="fear_removed",frame=frame,buff_id=buff_id,
                                remaining_wait_frames=max(0,self.wait-(frame-self.start))))
        if current!=buff_id:
            return
        if self.buffs:
            self._select()
        else:
            self.goal = self.original
            self.need_checkpoint = False

    def advance(self,frame,move_ratio_delta=None,*,one_minus_status_resistance=None,fear_resistance=None):
        """Execute exactly one active enemy tick, preserving draws between external callbacks."""
        if type(frame) is not int or frame!=self.frame+1:
            raise ValueError("敌方更新须按连续逻辑帧推进")
        if self.status!="tracked":
            return
        self.frame = frame
        if move_ratio_delta is not None:
            self.move_ratio = move_ratio_delta
        if one_minus_status_resistance is not None:
            self.resistance_factor = one_minus_status_resistance
        if fear_resistance is not None:
            self.fear_resistance = fear_resistance
        cap = movement_cap(move_attribute(self.base_speed,ratio_delta=self.move_ratio),self.level_multiplier)
        if cap<=0:
            self.status,self.reason = "unresolved","requires_stationary_or_forced_movement_state"
            return
        if self.buffs and self.need_checkpoint:
            self._select()
        waiting = not self.buffs and frame-self.start<=self.wait
        if waiting:
            tick = coast_during_wait(dict(zip(("row","col"),self.position)),self.velocity,1,self.steering,self.dt,round_position=True)
            self.position,self.velocity = (tick["position"]["row"],tick["position"]["col"]),tuple(tick["velocity"])
        else:
            target=self.goal
            if self.navigation:
                cell,goal=_cell(self.position),_cell(self.goal)
                distances,following=self.navigation.get_map(goal,bool(self.buffs) or self.diagonal)
                if cell not in distances:
                    self.status,self.reason="unresolved","no_path_to_active_checkpoint"
                    return
                next_cell=following.get(cell,goal)
                if next_cell!=goal:
                    target=next_cell
            if _within(self.position,target,single(cap*self.dt)):
                self.position = tuple(single(round(v*10000)/10000) for v in target)
            else:
                self.position,self.velocity = advance_direct_move(self.position,self.velocity,target,cap,self.steering,self.dt,True)
        if _cell(self.position) not in self.cells:
            self.status,self.reason = "unresolved","movement_requires_invalid_or_impassable_tile_correction"
            return
        if self.navigation and self.navigation.cost.get(_cell(self.position))!=1:
            self.status,self.reason="unresolved","special_terrain_entry_during_fear"
            return
        # Entity first updates MoveState, then BuffContainer; Enemy subsequently ticks its cursors.
        if self.buffs:
            delta = self._buff_delta()
            for key,buff in list(self.buffs.items()):
                buff["remaining"] -= delta
                if buff["remaining"]<=0:
                    self.remove(frame,key)
                else:
                    buff["expiry"] = frame+(buff["remaining"]+delta-1)//delta
        if not waiting and _within(self.position,self.goal,ROUTE_REACH_DISTANCE):
            if self.buffs:
                self.need_checkpoint = True
            elif frame-self.start>=self.wait:
                self.status = "original_checkpoint_reached"


def trace_fear_motion(tables,*,enemy,source,terrain,start_frame,end_frame,duration_seconds,stream,map_stream=None):
    """Single application after enemy OBJECTS. map_seed means a fresh explicitly seeded map stream."""
    if type(end_frame) is not int or end_frame<start_frame:
        raise ValueError("恐惧时窗需要有效的结束逻辑帧")
    if map_stream is None and "map_seed" in terrain:
        map_stream = BattleRandom(terrain["map_seed"])
    actor = FearMotion(tables,enemy=enemy,terrain=terrain,start_frame=start_frame,stream=stream,map_stream=map_stream)
    actor.apply(start_frame,source,duration_seconds,"fear")
    expiry = actor.buffs.get("fear",{}).get("expiry")
    positions = {start_frame:actor.position}
    for frame in range(start_frame+1,end_frame+1):
        if actor.status!="tracked":
            break
        actor.advance(frame)
        if actor.status!="unresolved":
            positions[frame] = actor.position
    result = dict(status=actor.status,through_frame=max(positions),positions=positions,events=actor.events,
                  last_velocity=actor.velocity,original_route_target=actor.original,expiry_frame=expiry)
    if actor.reason:
        result["reason"] = actor.reason
    return result
