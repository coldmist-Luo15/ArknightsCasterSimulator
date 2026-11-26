"""Aphris beam targeting and persistent movement through sluggish effects."""
from .phalanx_wave import ResumedGroundWalk
from .stage_channel import UnsupportedChannelScene, object_position_frame
from .stage_receivers import StageReceivers
from .stage_route_cursor import ControlledRoute


class AphrisWave(StageReceivers):
    def __init__(self,battle,request,placements,cards,events):
        super().__init__(battle,request,placements,cards,events)
        self.walks,self.slow_until={},{}
        self.slow_ratio=self.tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"][0]["value"]

    def targets(self, frame, cells, *, search=False):
        wave = self.wave
        found = []
        for key,birth in wave.births.items():
            tick = object_position_frame(birth,frame,wave.positions,wave.active,key,
                observer_born=self.battle.deploy["frame"],observer_phase="INPUT") if search else frame
            if (key in wave.active[tick] and self.valid(key,frame)
                    and tuple(round(v) for v in wave.positions[tick][key]) in cells):
                found.append(key)
        return found

    def sluggish(self,key,frame,ticks):
        self.slow_until[key] = max(frame+ticks,self.slow_until.get(key,0))
        if frame<self.end:
            self.start_walk(key,frame)

    def start_walk(self,key,frame):
        wave = self.wave
        if key in self.walks or self.incoming and not self.incoming.prepare_retiming(key,frame):
            return
        try:
            self.walks[key] = ResumedGroundWalk(self.tables,wave,key,frame,self.slow_ratio,live_checkpoints=True)
        except UnsupportedChannelScene:
            self.walks[key] = ControlledRoute(self.tables,wave,key,frame,self.slow_ratio)
        wave.escapes.pop(key,None)
        if self.incoming:
            self.incoming.enemies[key]["rerouted"] = True

    def advance(self,frame):
        wave = self.wave
        if self.incoming:
            self.incoming.advance(frame,{})
        for key,motion in tuple(self.walks.items()):
            if key in self.deaths:
                del self.walks[key]
                continue
            if self.incoming and self.incoming.holding(key,frame):
                continue
            try:
                walking = motion.advance(wave,frame,frame<=self.slow_until[key])
            except UnsupportedChannelScene:
                self.walks[key] = ControlledRoute(self.tables,wave,key,frame,self.slow_ratio,prior=motion)
                wave.positions[frame][key]=motion.position
                wave.active[frame].add(key)
                walking=True
            if not walking:
                wave.escapes[key]=frame
                del self.walks[key]
        if self.incoming and frame<self.end:
            for key,until in self.slow_until.items():
                if key in wave.active[frame] and key not in self.deaths and frame<until:
                    self.start_walk(key,frame)
        self.settle_exits(frame)
