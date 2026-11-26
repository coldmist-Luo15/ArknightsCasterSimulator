"""Continue an authored enemy route from the last integrated position and velocity."""
from math import ceil, floor, hypot

from .enemy_motion import ROUTE_REACH_DISTANCE, advance_direct_move, advance_wait_coast
from .enemy_speed import move_attribute, movement_cap
from .fear_navigation import GroundFearNavigation
from .fixed import ONE
from .route_timing import TIMED_WAITS


class StageRouteCursor:
    def __init__(self, route, source, position, velocity, steering, dt, clocks, notes, navigation):
        self.route=route
        self.index=next((i for i,c in enumerate(route["commands"]) if c["source"]==source),0)
        self.position,self.velocity=position,velocity
        self.steering,self.dt,self.clocks=steering,dt,clocks or {}
        self.navigation=navigation if route["motion_mode"]=="WALK" else None
        self.normal=movement_cap(move_attribute(route["base_move_speed"]),route["level_move_multiplier"])
        self.notes=notes
        self.wait_until=None
        self.hidden=False
        self.ticks=0

    def step(self,frame,key,cap,paused=False):
        cmd=self.route["commands"][self.index]
        kind=cmd["kind"]
        rows=[]
        source=cmd["source"]
        self.ticks+=1
        finished=False
        if kind in ("MOVE","END"):
            target=cmd["target"]
            offset=cmd.get("reach_offset",{})
            goal=target["row"]+offset.get("y",0),target["col"]+offset.get("x",0)
            radius=cmd.get("reach_distance") or ROUTE_REACH_DISTANCE
            if not paused and hypot(self.position[0]-goal[0],self.position[1]-goal[1])>radius:
                waypoint=goal
                if self.navigation:
                    cell=tuple(floor(v+.5) for v in self.position)
                    tile=target["row"],target["col"]
                    following=self.navigation.get_map(tile,self.route["flags"]["allowDiagonalMove"])[1]
                    waypoint=following.get(cell,goal)
                    if waypoint==tile:
                        waypoint=goal
                moved=advance_direct_move(self.position,self.velocity,waypoint,cap,self.steering,self.dt,True)
                if moved:
                    self.position,self.velocity=moved
            reached=not paused and hypot(self.position[0]-goal[0],self.position[1]-goal[1])<=radius
            rows.append(("free_motion_sample",dict(row=self.position[0],col=self.position[1],velocity=self.velocity,
                        speed=hypot(*self.velocity),leg_source=source,active_ticks=self.ticks,leg_end=reached)))
            if reached:
                finished=kind=="END"
                if finished:
                    rows.append(("route_end_in_range",dict(target=target,source=source)))
                else:
                    self.index+=1
                    self.ticks=0
        else:
            if self.ticks==1:
                if kind=="DISAPPEAR":
                    self.hidden=True
                elif kind=="APPEAR_AT_POS":
                    self.hidden=False
                    self.position=tuple(cmd["target"][a]+cmd["reach_offset"][b] for a,b in (("row","y"),("col","x")))
                elif kind=="UNSUPPORTED":
                    self.notes.append(dict(kind="rule_applied",frame=frame,target=key,rule="advance_nonmovement_checkpoint",
                                           command=cmd["raw"]["type"],source=source))
                wait=cmd.get("seconds",0)
                step=int(self.dt*ONE)
                duration=ceil(wait/self.dt)
                if kind in self.clocks:
                    duration=(self.clocks[kind]+int(wait*ONE)+step-1)//step-frame
                self.wait_until=frame+max(1,duration)
                rows.append(("checkpoint_begin",dict(command=kind,source=source,disappeared=self.hidden,
                    position=dict(zip(("row","col"),self.position)))))
            if kind in TIMED_WAITS and not self.hidden and not paused:
                self.position,self.velocity=advance_wait_coast(self.position,self.velocity,cap,self.steering,self.dt)
                rows.append(("free_motion_sample",dict(row=self.position[0],col=self.position[1],velocity=self.velocity,
                            speed=hypot(*self.velocity),leg_source=source,active_ticks=self.ticks,leg_end=False)))
            if frame+1>=self.wait_until:
                self.index+=1
                self.ticks=0
        return [(kind,dict(row,instance_id=key)) for kind,row in rows],finished


def stage_navigation(scene):
    # The same ground path cost field is shared by route cursors on this map.
    return GroundFearNavigation(scene,static_terrain=True)


def extend_route(history,key,birth,profile,frame,end,source,clocks,dt,navigation,notes,born_delay):
    route=profile["route"]
    start=route["start"]
    pos=(start["row"]+route["spawn_offset"]["y"],start["col"]+route["spawn_offset"]["x"])
    velocity=(0.,0.)
    for previous in range(frame,birth["frame"]-1,-1):
        samples=[r for _,r in history.get(previous,()) if r["instance_id"]==key and "row" in r]
        if samples:
            pos=samples[-1]["row"],samples[-1]["col"]
            velocity=samples[-1].get("velocity",velocity)
            break
    cursor=StageRouteCursor(route,source,pos,velocity,profile["steering"],dt,clocks,notes,navigation)
    if frame==birth["frame"]:
        history[frame].append(("free_motion_sample",dict(instance_id=key,row=pos[0],col=pos[1],active_ticks=0)))
        timer=profile["birth_timer"]
        delay=timer.get("first_default_request_update",max(1,ceil(profile["birth_fields"]["_delayToBorn"]/dt)))
        frame+=born_delay+delay
    for tick in range(frame,end+1):
        rows,finished=cursor.step(tick,key,cursor.normal)
        history[tick].extend(rows)
        if finished:
            return tick

class ControlledRoute:
    """Resume a speed-modified MOVE or checkpoint after an ordinary leg ends."""
    def __init__(self,tables,wave,key,frame,ratio,prior=None):
        from .routes import compile_route
        self.key=key
        b=wave.births[key]
        route=compile_route(wave.scene,b["route_index"],b["enemy_key"])
        unit=wave.scene.enemies[b["enemy_key"]]
        steering=tables.enemy_steering(unit["prefabKey"]["m_value"] or b["enemy_key"])
        commands=route["commands"]
        if prior is not None:
            position,velocity=prior.position,prior.velocity
            index=prior.index
        else:
            position=wave.positions[frame][key]
            row=next((r for _,r in reversed(wave.history.get(frame,())) if r["instance_id"]==key
                      and ("leg_source" in r or "source" in r or "velocity" in r)),{})
            velocity=row.get("velocity",(0,0))
            source=row.get("leg_source",row.get("source"))
            index=next((i for i,c in enumerate(commands) if c["source"]==source),0)
            if row.get("leg_end") and index+1<len(commands):
                index+=1
        self.cursor=StageRouteCursor(route,commands[index]["source"],position,velocity,steering,tables.fixed_timestep(),
            wave.route_clocks.get((b["wave_index"],b["fragment_index"])),wave.assumptions,stage_navigation(wave.scene))
        self.slow=movement_cap(move_attribute(route["base_move_speed"],ratio_delta=ratio),route["level_move_multiplier"])
        wave.assumptions.append(dict(frame=frame,target=key,kind="rule_applied",rule="resume_control_on_authored_route",
                                     source=commands[index]["source"]))

    @property
    def route(self):
        return self.cursor.route

    @property
    def goal(self):
        cmd=self.route["commands"][self.cursor.index]
        target=cmd.get("target")
        return (target["row"],target["col"]) if target else self.position

    @property
    def position(self):
        return self.cursor.position

    @position.setter
    def position(self,value):
        self.cursor.position=value

    def advance(self,wave,frame,slowed,*,paused=False,speed_cap=None,temporary_goal=None,forced_position=None,navigation=None):
        key=self.key
        cursor=self.cursor
        cap=speed_cap if speed_cap is not None else self.slow if slowed else cursor.normal
        if forced_position is not None or temporary_goal is not None:
            if forced_position is not None:
                cursor.position,cursor.velocity=forced_position,(0.,0.)
            elif not paused and hypot(cursor.position[0]-temporary_goal[0],cursor.position[1]-temporary_goal[1])>ROUTE_REACH_DISTANCE:
                dest=temporary_goal
                if navigation and cursor.navigation:
                    cell=tuple(floor(v+.5) for v in cursor.position)
                    dest=navigation.get_map(dest)[1].get(cell,dest)
                cursor.position,cursor.velocity=advance_direct_move(cursor.position,cursor.velocity,dest,cap,cursor.steering,cursor.dt,True)
            rows=[("free_motion_sample",dict(instance_id=key,row=cursor.position[0],col=cursor.position[1],velocity=cursor.velocity,
                  leg_source=self.route["commands"][cursor.index]["source"],active_ticks=cursor.ticks,leg_end=False))]
            ended=False
        else:
            rows,ended=cursor.step(frame,key,cap,paused)
        history=wave.history.setdefault(frame,[])
        history[:]=[(kind,r) for kind,r in history if r["instance_id"]!=key]
        history.extend(rows)
        wave.positions[frame][key]=self.cursor.position
        if self.cursor.hidden or ended:
            wave.active[frame].discard(key)
        else:
            wave.active[frame].add(key)
        return not ended
