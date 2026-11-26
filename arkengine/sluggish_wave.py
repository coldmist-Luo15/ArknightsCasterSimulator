"""Project ordinary MOVE legs after an attached sluggish buff, preserving per-object observations."""
from math import hypot

from .enemy_motion import ROUTE_REACH_DISTANCE,advance_direct_move
from .enemy_speed import scaled_move_attribute,movement_cap
from .fixed import ONE
from .volley_damage import _raw_f32
from .phalanx_wave import ResumedGroundWalk,resume_move_leg
from .stage_channel import UnsupportedChannelScene,object_position_frame


class SluggishWave:
    def __init__(self,tables,world,*,continue_route=False):
        self.tables,self.world=tables,world
        self.legs={}
        self.boundaries={}
        self.expiry={}
        self.continue_route=continue_route
        self.retimed=set()
        mod,=tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"]
        if (mod["attributeType"],mod["formulaItem"],mod["loadFromBlackboard"])!=("MOVE_SPEED","FINAL_SCALER",False):
            raise ValueError("停顿移速修正已变化")
        self.modifier=mod["value"]

    def apply(self,key,frame,born,ticks,end,*,after_objects=False):
        w=self.world
        after=frame if after_objects else object_position_frame(w.births[key],frame,w.positions,w.active,key,
                                                               observer_born=born,observer_phase="COROUTINES")
        if after+1>end:
            return
        if not self.continue_route and self.expiry.get(key,-1)>after:
            self.boundaries[key]=frame+1
            return
        self.expiry[key]=max(self.expiry.get(key,-1),after+ticks)
        reduced=ONE+_raw_f32(self.modifier)
        self.project_scale(key,after,end,lambda f:reduced if f<=self.expiry[key] else ONE)

    def project_scale(self,key,after,end,scale_at):
        """Recompute ordinary movement from its last completed update."""
        w=self.world
        if after>=end:
            return
        if self.continue_route:
            self.boundaries.pop(key,None)
            f=after+1
            try:
                walk=ResumedGroundWalk(self.tables,w,key,after,self.modifier,already_retimed=key in self.retimed)
                self.retimed.add(key)
                caps={ONE:walk.normal}
                for f in range(after+1,end+1):
                    scale=scale_at(f)
                    if scale not in caps:
                        caps[scale]=movement_cap(scaled_move_attribute(walk.route["base_move_speed"],scale),
                                                 walk.route["level_move_multiplier"])
                    if not walk.advance(w,f,False,speed_cap=caps[scale]):
                        for tick in range(f+1,end+1):
                            w.active[tick].discard(key)
                        break
            except UnsupportedChannelScene:
                self.boundaries[key]=f
            return
        rows=[(kind,r) for kind,r in w.history.get(after,()) if r["instance_id"]==key]
        if len(rows)!=1 or "velocity" not in rows[0][1]:
            self.boundaries[key]=after+1
            return
        if key not in self.legs:
            leg=resume_move_leg(self.tables,w.scene,w,key,after)
            if leg is None:
                self.boundaries[key]=after+1
                return
            route,goal,normal,steering,_,_=leg
            self.legs[key]=route,goal,normal,steering
        route,goal,normal,steering=self.legs[key]
        (kind,row),=rows
        pos,velocity=w.positions[after][key],row["velocity"]
        dt=self.tables.fixed_timestep()
        self.boundaries.pop(key,None)
        w.exits[w.exit_index:]=[(f,k) for f,k in w.exits[w.exit_index:] if k!=key]
        caps={ONE:normal}
        for f in range(after+1,end+1):
            scale=scale_at(f)
            if scale not in caps:
                caps[scale]=movement_cap(scaled_move_attribute(route["base_move_speed"],scale),route["level_move_multiplier"])
            cap=caps[scale]
            if hypot(pos[0]-goal[0],pos[1]-goal[1])<=max(ROUTE_REACH_DISTANCE,cap*dt):
                self.boundaries[key]=f
                return
            pos,velocity=advance_direct_move(pos,velocity,goal,cap,steering,dt,True)
            w.positions[f][key]=pos
            w.active[f].add(key)
            history=[event for event in w.history.get(f,()) if event[1]["instance_id"]!=key]
            history.append((kind,dict(row,row=pos[0],col=pos[1],velocity=velocity,speed=hypot(*velocity),
                                      active_ticks=row["active_ticks"]+f-after,leg_end=False)))
            w.history[f]=history
