"""Wang's native build/explosion queue transitions, with explicit ability ports.

Selection, placement, damage, ammunition and trigger topology belong to the
supplied ability/map callbacks; this component alone cannot simulate a battle.
"""
from dataclasses import dataclass
from enum import Enum


class BuildResult(Enum):
    SUCCESS = 'success'
    RETRY = 'retry'
    CANCEL = 'cancel'
    OWNER_GONE = 'owner_gone'


@dataclass
class BuildRequest:
    key: str
    center: tuple[int, int]
    due_frame: int
    extra_count: int
    is_free: bool = True


@dataclass
class ExplosionRequest:
    key: str
    position: tuple[int, int]
    due_frame: int
    source_kind: str


def apply_build_result(request, result, build_delay):
    """Return whether the native list keeps this request after one attempt."""
    if not isinstance(result, BuildResult):
        raise TypeError('The build ability must return an explicit BuildResult')
    if result == BuildResult.SUCCESS:
        if not request.is_free:
            request.extra_count -= 1
        keep = request.extra_count >= 1
        if keep:
            # Native code advances the previous deadline, not the attempt frame.
            request.due_frame += build_delay
        request.is_free = False
        return keep
    return result == BuildResult.RETRY


class WangStoneQueues:
    def __init__(self, *, build_delay, token_explode_delay, projectile_explode_delay,
                 build, cancel_build, explode, clear_invalid_stone):
        if any(type(v) is not int or v < 0 for v in (build_delay, token_explode_delay, projectile_explode_delay)):
            raise ValueError('Delays must be nonnegative integer logic frames')
        if not all(callable(f) for f in (build, cancel_build, explode, clear_invalid_stone)):
            raise ValueError('All ability and map callbacks must be explicit')
        self.build_delay = build_delay
        self.explode_delays = {'token': token_explode_delay, 'projectile': projectile_explode_delay}
        self.build = build
        self.cancel_build = cancel_build
        self.explode = explode
        self.clear_invalid_stone = clear_invalid_stone
        # Map iteration order is supplied by registration; cross-map allocation
        # order must be bound by the eventual environment-system integration.
        self.maps = {}
        self.last_tick = -1
        self._ticking = False
        self._exploding = False

    def register_map(self, key):
        if key in self.maps or self._ticking:
            raise ValueError('Duplicate map or map registration during tick')
        self.maps[key] = {'builds': [], 'explosions': []}

    def add_build(self, map_key, *, key, center, frame, extra_count):
        if self._ticking:
            raise NotImplementedError('Reentrant build-list mutation has not been verified')
        self._validate_frame(frame)
        if type(extra_count) is not int or extra_count < 0:
            raise ValueError('extra_count must be a nonnegative integer')
        items = self.maps[map_key]['builds']
        if any(r.key == key for r in items):
            raise ValueError('Duplicate live build request')
        request = BuildRequest(key, tuple(center), frame+self.build_delay, extra_count)
        items.append(request)
        # Native helper is a stable adjacent-swap sort; callback compares b to a.
        items.sort(key=lambda r: r.due_frame, reverse=True)
        return request

    def add_explosion(self, map_key, *, key, position, frame, source_kind):
        if self._exploding:
            raise NotImplementedError('Reentrant explosion-list mutation has not been verified')
        self._validate_frame(frame)
        items = self.maps[map_key]['explosions']
        if any(r.key == key for r in items):
            raise ValueError('Duplicate live explosion request')
        if any(r.position == tuple(position) for r in items):
            raise NotImplementedError('Stacked pending explosions at one position need trigger-map integration')
        request = ExplosionRequest(key, tuple(position), frame+self.explode_delays[source_kind], source_kind)
        items.append(request)
        items.sort(key=lambda r: r.due_frame, reverse=True)
        return request

    def _validate_frame(self, frame):
        if type(frame) is not int or frame < max(0, self.last_tick) or frame >= (1 << 31)-1:
            raise ValueError('Frame must be monotonic and within the supported battle duration')
        if frame+max(self.build_delay, *self.explode_delays.values()) >= (1 << 31)-1:
            raise NotImplementedError('Frame-counter wrap is outside the supported battle duration')

    def tick(self, loop):
        frame = loop.frame
        if self._ticking or frame <= self.last_tick:
            raise ValueError('Wang queues must tick once per increasing logic frame')
        self._ticking = True
        try:
            # Native OnTick handles every map's builds before any map's explosions.
            for map_key, queues in self.maps.items():
                items = queues['builds']
                for index in range(len(items)-1, -1, -1):
                    request = items[index]
                    if request.due_frame > frame:
                        break
                    before = (request.due_frame, request.extra_count, request.is_free)
                    result = self.build(map_key, request, frame)
                    keep = apply_build_result(request, result, self.build_delay)
                    if result == BuildResult.CANCEL:
                        self.cancel_build(map_key, request, frame)
                    if not keep:
                        items.pop(index)
                    loop.emit('wang_build_attempt', map=map_key, request=request.key, result=result.value,
                              before=before, after=(request.due_frame, request.extra_count, request.is_free), kept=keep)
                items.sort(key=lambda r: r.due_frame, reverse=True)
            self._exploding = True
            for map_key, queues in self.maps.items():
                items = queues['explosions']
                for index in range(len(items)-1, -1, -1):
                    request = items[index]
                    if request.due_frame > frame:
                        break
                    succeeded = self.explode(map_key, request, frame)
                    if type(succeeded) is not bool:
                        raise TypeError('The explosion callback must return bool')
                    if succeeded:
                        items.pop(index)
                    else:
                        # ClearExplodedStones removes pending entries at the same
                        # position as well as occupancy/stack/direction records.
                        items[:] = [r for r in items if r.position != request.position]
                        self.clear_invalid_stone(map_key, request, frame)
                    loop.emit('wang_explode_attempt', map=map_key, request=request.key,
                              succeeded=succeeded, kept=False)
            self.last_tick = frame
        finally:
            self._ticking = False
            self._exploding = False
