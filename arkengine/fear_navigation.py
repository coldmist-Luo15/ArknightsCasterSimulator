"""V079 ground route maps for fear checkpoints on ordinary fixed terrain."""
from collections import deque
from math import hypot

from .enemy_fear import FearMotion, _cell
from .enemy_motion import _WALK_TILES
from .fixed import ONE
from .routes import compile_route
from .volley_damage import _raw_f32


class GroundFearNavigation:
    # GridPosition's four-neighbor array uses x first, while scene cells are (row, col).
    _NEIGHBORS = ((0,1),(1,0),(0,-1),(-1,0))

    def __init__(self,scene,*,static_terrain=False):
        data=scene.level["mapData"]
        if not static_terrain and (data["blockEdges"] or data["effects"] or data["layerRects"] or scene.predefines):
            raise ValueError("恐惧寻路需要普通固定地形")
        self.rows,self.cols=len(data["map"]),len(data["map"][0])
        self.cost={}
        self.excluded=set()
        self.maps={}
        for row in range(self.rows):
            for col in range(self.cols):
                tile=scene.tile(row,col)
                if tile["passableMask"]!="ALL":
                    continue
                key=tile["tileKey"]
                if not static_terrain and (key not in _WALK_TILES and key!="tile_hole" or tile["effects"] or tile["blackboard"]):
                    raise ValueError(f"恐惧寻路尚需确定 {key} 的动态通行状态")
                cell=(row,col)
                self.cost[cell]=1_000_000 if key=="tile_hole" else 1
                if key in ("tile_end","tile_hole"):
                    self.excluded.add(cell)

    def get_map(self,goal,diagonal=True):
        goal=_cell(goal)
        cache_key=(goal,diagonal)
        cached=self.maps.get(cache_key)
        if cached is not None:
            return cached
        if goal not in self.cost:
            return {},{}
        distances={goal:0}
        following={}
        pending=deque([goal])
        queued={goal}
        while pending:
            current=pending.popleft()
            queued.remove(current)
            row,col=current
            for dr,dc in self._NEIGHBORS:
                near=(row+dr,col+dc)
                if near not in self.cost:
                    continue
                cost=distances[current]+self.cost[near]
                if near in distances and cost>=distances[near]:
                    continue
                distances[near]=cost
                following[near]=current
                if near not in queued:
                    pending.append(near)
                    queued.add(near)

        # SPFA preserves the original path cost when it shortens the next-node chain.
        for origin in sorted(following,key=lambda cell:(cell[1],cell[0])):
            next_cell=following[origin]
            while next_cell in following:
                shortcut=following[next_cell]
                if diagonal:
                    allowed=self._clear_segment(origin,shortcut)
                else:
                    allowed=origin[0]==shortcut[0] or origin[1]==shortcut[1]
                if not allowed:
                    break
                next_cell=shortcut
            following[origin]=next_cell
        result=distances,following
        self.maps[cache_key]=result
        return result

    def _clear_segment(self,origin,goal):
        y0,x0=origin
        y1,x1=goal
        clear=lambda row,col:self.cost.get((row,col))==1
        if min(abs(x1-x0),abs(y1-y0))<=1:
            return all(clear(row,col) for row in range(min(y0,y1),max(y0,y1)+1)
                       for col in range(min(x0,x1),max(x0,x1)+1))
        swap=abs(x1-x0)>abs(y1-y0)
        if swap:
            x0,y0,x1,y1=y0,x0,y1,x1
        if y0>y1:
            x0,y0,x1,y1=x1,y1,x0,y0
        dx,dy=abs(x1-x0),y1-y0
        step=1 if x1>x0 else -1
        error=-dy
        x=x0
        def usable(major,minor):
            return clear(minor,major) if swap else clear(major,minor)
        for y in range(y0,y1+1):
            if not usable(y,x):
                return False
            error+=2*dx
            if error>=0 and y<y1:
                if not usable(y,x+step) or not usable(y+1,x):
                    return False
                x+=step
                error-=2*dy
        return True

    def probe(self,origin,goal):
        distances,_=self.get_map(goal)
        return origin in distances,distances.get(origin)

    def remaining_distance(self,position,commands,index,diagonal):
        cell=_cell(position)
        goal=_target_cell(commands[index])
        distance,following=self.get_map(goal,diagonal)
        if cell not in distance:
            return None
        total=distance[cell]
        direction=following.get(cell)
        if direction is not None:
            dr,dc=direction[0]-cell[0],direction[1]-cell[1]
            total+=(dr*(cell[0]-position[0])+dc*(cell[1]-position[1]))/hypot(dr,dc)
        for command in commands[index+1:]:
            if command["kind"]=="WAIT_FOR_SECONDS":
                continue
            if command["kind"] not in ("MOVE","END"):
                return None
            next_goal=_target_cell(command)
            distance=self.get_map(next_goal,diagonal)[0].get(goal)
            if distance is None:
                return None
            total+=distance
            goal=next_goal
        return total


def _target_cell(command):
    target=command["target"]
    return target["row"],target["col"]


class FearedGroundRoute:
    """Keep the original route cursor while a temporary fear cursor owns movement."""

    def __init__(self,tables,world,key,frame,nav,stream,map_stream):
        self.frame=frame
        self.key=key
        self.navigation=nav
        birth=world.births[key]
        route=compile_route(world.scene,birth["route_index"],birth["enemy_key"])
        self.commands=route["commands"]
        if (route["motion_mode"]!="WALK" or route["flags"]["visitEveryTileCenter"] or route["flags"]["visitEveryNodeCenter"] or
                any(cmd.get("randomize_reach_offset") or cmd.get("reach_distance") or any(cmd.get("reach_offset",{}).values())
                    for cmd in self.commands)):
            raise ValueError("恐惧后的原路径需要特殊目标点或移动类型")
        self.diagonal=route["flags"]["allowDiagonalMove"]
        samples=[(kind,row) for kind,row in world.history.get(frame,()) if row["instance_id"]==key and
                 kind in ("free_motion_sample","exit_motion_sample","post_wait_motion_sample")]
        if len(samples)!=1 or not samples[0][1].get("velocity"):
            raise ValueError("恐惧生效帧需要敌人的原路线检查点和速度")
        kind,sample=samples[0]
        source=sample.get("leg_source") or (self.commands[-1] if kind=="exit_motion_sample" else self.commands[0])["source"]
        self.index=next(i for i,cmd in enumerate(self.commands) if cmd["source"]==source)
        wait=0
        step=int(tables.fixed_timestep()*ONE)
        if self.commands[self.index]["kind"]=="WAIT_FOR_SECONDS":
            begin=next((tick for tick in range(frame,birth["frame"]-1,-1)
                        if any(event=="checkpoint_begin" and row["instance_id"]==key and row["source"]==source
                               for event,row in world.history.get(tick,()))),None)
            if begin is None:
                raise ValueError("恐惧生效时缺少原路线停驻计时的起点")
            wait=begin-frame
            while self.commands[self.index]["kind"]=="WAIT_FOR_SECONDS":
                raw=_raw_f32(self.commands[self.index]["seconds"])
                wait+=max(1,(raw+step-1)//step)
                self.index+=1
            if self.commands[self.index]["kind"] not in ("MOVE","END"):
                raise ValueError("恐惧覆盖的停驻之后需要特殊检查点")
        row,col=world.positions[frame][key]
        exit_cell=_target_cell(self.commands[-1])
        self.motion=FearMotion(tables,enemy=dict(row=row,col=col,velocity=sample["velocity"],route_target=_target_cell(self.commands[self.index]),
            prefab=world.stats[key]["prefab"],move_speed=route["base_move_speed"],allow_diagonal=self.diagonal,
            remaining_wait_frames=max(0,wait)),
            terrain=dict(navigation=nav,exit=exit_cell,level_move_multiplier=route["level_move_multiplier"]),
            start_frame=frame,stream=stream,map_stream=map_stream)
        self.event_index=0
        self.exited=False
        self.distances={}
        if sample.get("leg_end"):
            self._next_original_checkpoint()

    def _next_original_checkpoint(self):
        self.index+=1
        if self.index==len(self.commands):
            self.exited=True
            return
        wait=0
        while self.commands[self.index]["kind"]=="WAIT_FOR_SECONDS":
            raw=_raw_f32(self.commands[self.index]["seconds"])
            wait+=max(1,(raw+self.motion.step-1)//self.motion.step)
            self.index+=1
        command=self.commands[self.index]
        if command["kind"] not in ("MOVE","END"):
            self.motion.status,self.motion.reason="unresolved","original_route_requires_non_walk_checkpoint"
            return
        self.motion.wait=self.frame-self.motion.start+wait
        self.motion.original=_target_cell(command)
        self.motion.goal=self.motion.original
        self.motion.status="tracked"
        self.motion.events.append(dict(kind="fear_resume_checkpoint",frame=self.frame,goal=self.motion.original,
                                       wait_ticks=wait))

    def apply(self,frame,source,duration,buff_id=None):
        self.motion.apply(frame,source,duration,frame if buff_id is None else buff_id)
        self.distances[frame]=self._dist_to_exit()

    def advance(self,frame,move_ratio_delta=None):
        self.frame=frame
        was_feared=bool(self.motion.buffs)
        self.motion.advance(frame,move_ratio_delta=move_ratio_delta)
        if was_feared and not self.motion.buffs:
            self.motion.events.append(dict(kind="fear_return_to_route",frame=frame,position=self.motion.position,
                                           original_goal=self.motion.original,remaining_wait_frames=max(0,
                                               self.motion.wait-(frame-self.motion.start))))
        if self.motion.status=="original_checkpoint_reached":
            self._next_original_checkpoint()
        if self.motion.status=="tracked":
            self.distances[frame]=self._dist_to_exit()

    def _dist_to_exit(self):
        return self.navigation.remaining_distance(self.motion.position,self.commands,self.index,self.diagonal)

    def emit_events(self,events):
        for event in self.motion.events[self.event_index:]:
            events.append(dict(event,target=self.key))
        self.event_index=len(self.motion.events)


class CursorFearRoute:
    """Carry the same fear buffs through non-MOVE checkpoints and authored route flags."""
    def __init__(self,tables,world,key,frame,navigation,stream,map_stream,prior=None):
        from .stage_route_cursor import ControlledRoute
        from .enemy_fear import FearMotion
        self.world,self.key,self.frame=world,key,frame
        self.walk=ControlledRoute(tables,world,key,frame,0)
        route=self.walk.route
        cursor=self.walk.cursor
        nav=navigation
        if route["motion_mode"]=="FLY":
            nav=GroundFearNavigation(world.scene,static_terrain=True)
            nav.cost={(r,c):1 for r in range(nav.rows) for c in range(nav.cols)}
        self.navigation=nav
        self.motion=FearMotion(tables,enemy=dict(row=cursor.position[0],col=cursor.position[1],velocity=cursor.velocity,
            route_target=self.walk.goal,prefab=world.stats[key]["prefab"],move_speed=route["base_move_speed"]),
            terrain=dict(navigation=nav,exit=_target_cell(route["commands"][-1]),level_move_multiplier=route["level_move_multiplier"]),
            start_frame=frame,stream=stream,map_stream=map_stream)
        self.event_index=0
        self.exited=False
        self.distances={}
        if prior is not None:
            self.motion.position,self.motion.velocity=prior.motion.position,prior.motion.velocity
            self.motion.buffs=prior.motion.buffs
            self.motion.goal=prior.motion.goal if prior.motion.buffs else self.walk.goal
            self.motion.need_checkpoint=prior.motion.need_checkpoint
            self.motion.resistance_factor=prior.motion.resistance_factor
            cursor.index=min(prior.index,len(route["commands"])-1)
            cursor.position,cursor.velocity=self.motion.position,self.motion.velocity
        world.assumptions.append(dict(kind="rule_applied",frame=frame,target=key,rule="fear_on_stage_navigation_then_resume_authored_checkpoint"))

    def apply(self,frame,source,duration,buff_id=None):
        self.motion.apply(frame,source,duration,frame if buff_id is None else buff_id)

    def advance(self,frame,move_ratio_delta=None):
        from .enemy_speed import move_attribute,movement_cap
        m=self.motion
        cap=None
        if move_ratio_delta is not None:
            cap=movement_cap(move_attribute(self.walk.route["base_move_speed"],ratio_delta=move_ratio_delta),self.walk.route["level_move_multiplier"])
        self.frame=m.frame=frame
        if m.buffs:
            if m.need_checkpoint:
                m._select()
            cursor=self.walk.cursor
            dest=m.goal
            cell=_cell(cursor.position)
            goal=_cell(dest)
            following=self.navigation.get_map(goal,True)[1]
            waypoint=following.get(cell,goal)
            self.walk.advance(self.world,frame,False,speed_cap=cap,temporary_goal=dest if waypoint==goal else waypoint)
            m.position,m.velocity=cursor.position,cursor.velocity
            for buff_id,buff in list(m.buffs.items()):
                buff["remaining"]-=m._buff_delta()
                if buff["remaining"]<=0:
                    m.remove(frame,buff_id)
            if m.buffs:
                m.need_checkpoint=hypot(m.position[0]-m.goal[0],m.position[1]-m.goal[1])<=.1
            else:
                m.events.append(dict(kind="fear_return_to_route",frame=frame,position=m.position,original_goal=self.walk.goal))
        else:
            self.exited=not self.walk.advance(self.world,frame,False,speed_cap=cap)
            m.position,m.velocity=self.walk.position,self.walk.cursor.velocity
        end=_target_cell(self.walk.route["commands"][-1])
        self.distances[frame]=hypot(m.position[0]-end[0],m.position[1]-end[1])

    emit_events=FearedGroundRoute.emit_events


class StageFearRoutes:
    """Put altered enemy routes back into the normal stage target snapshots."""

    def __init__(self,tables,world,stream,map_stream):
        self.tables,self.world,self.stream,self.map_stream=tables,world,stream,map_stream
        self.navigation=GroundFearNavigation(world.scene,static_terrain=hasattr(world,"wave"))
        self.routes={}

    def track(self,key,frame):
        route=self.routes.get(key)
        if route is None:
            world=self.world
            try:
                route=FearedGroundRoute(self.tables,world,key,frame,self.navigation,self.stream,self.map_stream)
                route.distances[frame]=route._dist_to_exit()
            except ValueError:
                route=CursorFearRoute(self.tables,world,key,frame,self.navigation,self.stream,self.map_stream)
            self.routes[key]=route
            world.exits=[pair for i,pair in enumerate(world.exits) if i<world.exit_index or pair[1]!=key]
            if getattr(world,"incoming",None) and key in world.incoming.enemies:
                world.incoming.enemies[key]["rerouted"]=True
            if hasattr(world,"wave"):
                world.wave.escapes.pop(key,None)
        return route

    def apply(self,frame,key,source,duration,buff_id,events):
        from .stage_channel import UnsupportedChannelScene
        enemy=self.world.scene.enemies[self.world.stats[key]["enemy_key"]]
        if enemy["attributes"]["fearedImmune"]["m_value"]:
            return
        route=self.track(key,frame)
        if hasattr(self.world,"wave"):
            route.motion.resistance_factor=.5 if "STATUS_RESISTANCE" in self.world.wave.flags.at(key,frame) else 1.
        route.apply(frame,source,duration,buff_id)
        route.emit_events(events)
        if getattr(self.world,"incoming",None) and key in self.world.incoming.enemies:
            self.world.incoming.enemies[key].update(fear_until=max((v["expiry"] for v in route.motion.buffs.values()),default=frame),
                                                  shot=None,hold_until=frame)

    def advance(self,frame,alive,events,slow=None):
        from .stage_channel import UnsupportedChannelScene
        world=self.world
        for key,route in self.routes.items():
            if not alive(key) or route.exited:
                world.active[frame].discard(key)
                continue
            route.advance(frame,move_ratio_delta=slow.get(key,0) if slow else 0)
            route.emit_events(events)
            if route.motion.status=="unresolved":
                route=self.routes[key]=CursorFearRoute(self.tables,world,key,frame,self.navigation,self.stream,self.map_stream,prior=route)
            if route.exited:
                world.active[frame].discard(key)
                world.exits=world.exits[:world.exit_index]+sorted([*world.exits[world.exit_index:],(frame,key)])
            else:
                world.active[frame].add(key)
                world.positions[frame][key]=route.motion.position

    def select(self,pool,frame):
        from .stage_channel import UnsupportedChannelScene, object_position_frame
        world=self.world
        if len(pool)<2:
            return next(iter(pool),None)
        if not any(row["id"] in self.routes for row in pool):
            return world.select(pool,frame)
        def priority(row):
            key=row["id"]
            birth=world.births[key]
            observed=object_position_frame(birth,frame,world.positions,world.active,key,observer_born=world.deployment_frame,
                                           observer_phase="INPUT")
            route=self.routes.get(key)
            distance=route.distances.get(observed) if route else None
            if distance is None:
                samples=world.priority({key},frame,frame_by_target={key:observed},distance_samples=True)
                if samples and samples.get(key):
                    distance=min(samples[key])
                else:
                    end=world.scene.level["routes"][birth["route_index"]]["endPosition"]
                    distance=hypot(row["row"]-end["row"],row["col"]-end["col"])
            return int(distance*ONE)//_raw_f32(.1),birth["frame"],birth["order"]
        return min(pool,key=priority)
