"""V079 ground KnockBack, in (map row, map column) order.

An impulse from COROUTINES is applied after the current PHYSICS pass. The next
enemy OBJECTS tick runs friction, then PHYSICS advances the rigidbody. Callers
retain the route cursor and its steering velocity separately from this body.
"""
from dataclasses import dataclass, replace
from math import floor, isfinite, sqrt

from .baked_mount import single
from .fixed import ONE


@dataclass(frozen=True)
class PushProfile:
    mass: float
    drag: float
    gravity: float
    margin: float
    half_size: tuple
    offset: tuple
    fixed_body: bool
    collision_layer: int
    collision_mask: int


@dataclass(frozen=True)
class ForcedMotion:
    position: tuple
    velocity: tuple
    frame: int
    protect_until: int
    active: bool = True
    boundary: str | None = None


def _length(vector):
    return single(sqrt(single(sum(single(x*x) for x in vector))))


def push_force(tables, force_level, weight):
    if any(type(v) not in (int, float) or not isfinite(v) or int(v) != v for v in (force_level, weight)):
        raise ValueError("推力等级与目标当前重量必须是整数")
    cfg = tables.read("excel/gamedata_const.json")
    forces = cfg["pushForces"]
    index = cfg["pushForceZeroIndex"] + int(force_level) - int(weight)
    return single(forces[max(0, min(index, len(forces)-1))])


def enemy_push_profile(tables, enemy_prefab):
    key = ("enemy_push_profile", enemy_prefab)
    if key in tables._prefab_data:
        return tables._prefab_data[key]
    import UnityPy
    from hashlib import sha256
    from .opening_scene import TIME_SETTINGS_SHA256

    settings_key = ("unbalance_physics_settings",)
    if settings_key not in tables._prefab_data:
        path = tables.root.parent / "2026-11-18-cn2791-base-index/extracted/assets/bin/Data/globalgamemanagers"
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != TIME_SETTINGS_SHA256:
            raise ValueError("国服物理设置的包体哈希不符")
        data, = (o.read_typetree() for o in UnityPy.load(raw).objects if o.type.name == "Physics2DSettings")
        tables._prefab_data[settings_key] = (_length(tuple(data["m_Gravity"].values())), data["m_DefaultContactOffset"],
                                             data["m_LayerCollisionMatrix"])
    gravity, margin, collision_masks = tables._prefab_data[settings_key]
    def fields(component, keys, children=()):
        return tables._component_fields("enemies", enemy_prefab, keys, "enemy_push_" + component,
                                        component_type=component, children=children)
    body = fields("Rigidbody2D", ("m_Mass","m_LinearDrag","m_GravityScale","m_UseAutoMass",
                                 "m_BodyType","m_Constraints","m_Simulated"))
    capsule = fields("CapsuleCollider2D", ("m_Size","m_Offset","m_Enabled","m_IsTrigger"), ("ColliderHolder",))
    transform = fields("Transform", ("m_LocalPosition","m_LocalRotation","m_LocalScale"), ("ColliderHolder",))
    layer = fields("GameObject", ("m_Layer",), ("ColliderHolder",))["m_Layer"]
    if (body["m_Mass"] <= 0 or body["m_UseAutoMass"] or body["m_GravityScale"] or not body["m_Simulated"]
            or body["m_BodyType"] not in (0,1,2) or body["m_Constraints"] not in (4,7)
            or not capsule["m_Enabled"] or capsule["m_IsTrigger"] or any(transform["m_LocalPosition"].values())
            or transform["m_LocalRotation"] != dict(x=0.,y=0.,z=0.,w=1.)
            or any(v != 1 for v in transform["m_LocalScale"].values())):
        raise ValueError(f"{enemy_prefab} 的刚体或胶囊变换需要单独处理")
    profile = PushProfile(body["m_Mass"], body["m_LinearDrag"], gravity, margin,
                          tuple(capsule["m_Size"][k]/2 for k in ("y","x")),
                          tuple(capsule["m_Offset"][k] for k in ("y","x")),
                          body["m_BodyType"] != 0 or body["m_Constraints"] == 7, layer, collision_masks[layer])
    tables._prefab_data[key] = profile
    return profile


def start_push(tables, profile, position, direction, *, force_level, weight, frame,
               previous=None, immune=False, move_multiplier=1.):
    force = push_force(tables, force_level, weight)
    if immune or force <= single(1e-5):
        return previous
    if len(position) != 2 or len(direction) != 2 or not all(map(isfinite, (*position, *direction, move_multiplier))):
        raise ValueError("推移需要有限的行列坐标、方向和关卡倍率")
    if previous is not None and previous.active and (previous.frame != frame or previous.position != tuple(position)):
        raise ValueError("追加推力前须推进到相同的物理帧与坐标")
    old = previous.velocity if previous and previous.active else (0., 0.)
    norm = _length(direction)
    impulse = []
    for component, velocity in zip(direction, old, strict=True):
        unit = single(component/norm) if norm > single(1e-5) and max(map(abs,direction)) > single(1e-5) else 0.
        term = single(single(single(unit*force)*move_multiplier)*single(.02))
        impulse.append(0. if profile.fixed_body else single(velocity+single(term*single(1/profile.mass))))
    protect_until = frame * int(tables.fixed_timestep()*ONE) + int(single(.1)*ONE)
    return ForcedMotion(tuple(position), tuple(impulse), frame, protect_until)


# These ordinary route tiles already have a verified terrain path.
_ORDINARY_FLOOR = {"tile_road","tile_start","tile_end","tile_floor","tile_telin","tile_telout"}


def _plain_tile_without_box(tables, key):
    from .caster_prefabs import _prefab

    prefab = _prefab(tables, "[uc]tiles", key)
    if prefab is None:
        return False
    components = prefab["components"]
    scripts = [c for c in components if c["active"] and c["enabled"] and c["type"] == "MonoBehaviour"]
    return (len(scripts) == 1 and not scripts[0]["path"] and scripts[0]["script"] == "Tile"
            and scripts[0]["fields"]["_tileKey"] == key and not scripts[0]["fields"]["_forceBoxCollider"]
            and not any(c["active"] and c["enabled"] and c["type"].endswith("Collider2D") for c in components))


def _terrain_boundary(tables, scene, profile, before, after):
    """Conservative swept collider bounds: never pass through an unresolved fixture."""
    data = scene.level["mapData"]
    if data["blockEdges"] or data["layerRects"] or data["effects"]:
        return "requires_stage_physics_geometry"
    rows, cols = len(data["map"]), len(data["map"][0])
    limits = []
    for i in range(2):
        lo, hi = sorted((before[i], after[i]))
        mid = .5 + profile.offset[i]
        extent = profile.half_size[i] + profile.margin
        limits.append(range(floor(lo+mid-extent), floor(hi+mid+extent)+1))
    for row in limits[0]:
        for col in limits[1]:
            if not (0 <= row < rows and 0 <= col < cols):
                return "requires_map_edge_collision"
            tile = scene.tile(row, col)
            key = tile["tileKey"]
            if (tile["heightType"] != "LOWLAND" or tile.get("effects") or
                    key not in _ORDINARY_FLOOR and (tile.get("blackboard") or not _plain_tile_without_box(tables, key))):
                return "requires_tile_collision:" + key
    return None


def _sweep_map(scene,profile,before,after,velocity):
    grid=scene.level["mapData"]["map"]
    sizes=len(grid),len(grid[0])
    position=list(before)
    speed=list(velocity)
    for axis in (0,1):
        delta=after[axis]-before[axis]
        if not delta:
            continue
        other=1-axis
        extent=profile.half_size[axis]+profile.margin
        cross=position[other]+profile.offset[other]
        radius=profile.half_size[other]
        origin=before[axis]+profile.offset[axis]
        stop=origin+delta
        for i in range(floor(min(origin,stop)-extent+.5),floor(max(origin,stop)+extent+.5)+1):
            for j in range(floor(cross-radius+.5),floor(cross+radius+.5)+1):
                row,col=(i,j) if axis==0 else (j,i)
                if 0<=row<sizes[0] and 0<=col<sizes[1]:
                    tile=scene.tile(row,col)
                    if tile["heightType"]=="LOWLAND" and tile["passableMask"] in ("ALL","WALK_ONLY"):
                        continue
                face=i-(.5+extent) if delta>0 else i+(.5+extent)
                if delta>0 and origin-1e-4<=face<=stop or delta<0 and stop<=face<=origin+1e-4:
                    stop=face
                    speed[axis]=0.
        position[axis]=single(stop-profile.offset[axis])
    return tuple(position),tuple(speed)


def step_push(tables, profile, state, *, frame, scene=None, friction_factor=1.,continuous=False):
    """One OBJECTS + PHYSICS tick, on ordinary ground with no other active pull."""
    if state.boundary or not state.active or frame != state.frame+1:
        raise ValueError("只能从正在失衡且没有未解碰撞的前一帧推进")
    dt = tables.fixed_timestep()
    speed = _length(state.velocity)
    decel = single(single(single(profile.mass*profile.gravity)*single(.5))*max(0.,friction_factor))
    remaining = max(0., single(speed-single(decel*dt)))
    if remaining <= single(.1) and frame*int(dt*ONE) >= state.protect_until:
        return replace(state, frame=frame, velocity=(0.,0.), active=False)
    velocity = tuple(single(single(v/speed)*remaining) if speed > single(1e-5) else 0. for v in state.velocity)
    if profile.drag and continuous:
        velocity=tuple(single(v/(1+dt*profile.drag)) for v in velocity)
    elif profile.drag:
        # Unity's native 2D integrator for nonzero linear damping needs separate validation.
        return replace(state, frame=frame, boundary="requires_rigidbody_linear_drag")
    position = tuple(single(round(single(p+single(v*dt))*10000)/10000) for p,v in zip(state.position,velocity,strict=True))
    if continuous and scene:
        position,velocity=_sweep_map(scene,profile,state.position,position,velocity)
        return replace(state,frame=frame,velocity=velocity,position=position)
    boundary = _terrain_boundary(tables, scene, profile, state.position, position) if scene else None
    return replace(state, frame=frame, velocity=velocity, position=state.position if boundary else position, boundary=boundary)
