"""V079 fear_buff candidate geometry and temporary-route choice. Coordinates are (row,col)."""
from math import acos,isfinite,sqrt

from .baked_mount import single
from .fixed import ONE
from .skill_recovery import READY_EPSILON


EPS=single(1e-5)
RAD_TO_DEG=single(57.295780181884766)


def _point(value):
    if len(value)!=2 or any(type(n) not in (float,int) or not isfinite(n) for n in value):
        raise ValueError("恐惧的地图位置需要两个有限坐标 (row,col)")
    return tuple(single(n) for n in value)


def _cell(value):
    if len(value)!=2 or any(type(n) is not int for n in value):
        raise ValueError("恐惧路线地块必须使用整数 (row,col)")
    return tuple(value)


def _squared(v):
    return single(single(v[0]*v[0])+single(v[1]*v[1]))


def _angle(a,b):
    # Torappu.MathUtil.GetVector2AngleUnsigned returns 90 degrees for an input
    # component-wise equal to zero, before delegating to the Vector2.Angle arithmetic.
    if max(map(abs,a))<=EPS or max(map(abs,b))<=EPS:
        return 90.0
    den=single(sqrt(single(_squared(a)*_squared(b))))
    if den<single(1e-15):
        return 0.0
    dot=single(single(a[0]*b[0])+single(a[1]*b[1]))
    cos=max(-1,min(1,single(dot/den)))
    return single(single(acos(cos))*RAD_TO_DEG)


def build_fear_tiles(*,rows,cols,target_position,source_position,original_reachable,excluded_tile,
                     radius=10.0,max_degree=45.0,tile_position=None):
    """AddFearTargetTiles: enumerate valid game tiles, retain original native order.

    original_reachable(cell) is original Route.CheckReachable(cell), not the temporary
    route's weighted reachability. excluded_tile(cell) supplies native end/HoleTile status.
    Use tile_position(cell) to supply different map-space centers, or None for no such tile.
    """
    if any(type(n) is not int or n<=0 for n in (rows,cols)) or any(type(n) not in (float,int) or not isfinite(n) for n in (radius,max_degree)):
        raise ValueError("恐惧区域需要有效的地图尺寸、半径与角度")
    if source_position is None:
        return []
    target=_point(target_position)
    source=_point(source_position)
    away=tuple(single(t-s) for t,s in zip(target,source))
    cap=single(single(max(0,radius))+EPS)
    degrees=int(single(max_degree)*ONE)
    selected=[]
    for col in range(cols):
        for row in range(rows):
            cell=(row,col)
            position=cell if tile_position is None else tile_position(cell)
            if position is None:
                continue
            delta=tuple(single(p-o) for p,o in zip(_point(position),target))
            if (single(sqrt(_squared(delta)))<=cap and int(_angle(away,delta)*ONE)<degrees+READY_EPSILON and
                    original_reachable(cell) and not excluded_tile(cell)):
                selected.append(cell)
    return selected


def select_fear_tile(candidates,*,origin,random_stream,route_probe,map_random):
    """One complete FearController.UpdateFearCursor route build after its owner/buff gates.

    Supply the current ordered candidate list of the selected fear buff. Failed trials
    are erased for the lifetime of that buff; pass remaining_candidates on the next build.
    A successful destination stays in that list and can be drawn again on a future build.
    In ordinary stages map_random is a separate Map-owned BattleRandomWrapper.
    """
    origin=_cell(origin)
    pool=[_cell(cell) for cell in candidates]
    if map_random is random_stream:
        raise ValueError("普通地图的检查点偏移与战斗选点使用各自的 RNG 实例")
    offset=tuple(single(single(.5*map_random.value())-.25) for _ in range(2))
    trials=[]
    while pool:
        index=random_stream.range_int(0,len(pool))
        cell=pool[index]
        reachable,distance=route_probe(origin,cell)
        if type(reachable) is not bool or (reachable and (type(distance) is not int or distance<0)):
            raise ValueError("临时路线需要实际可达标记，且可达节点应有非负整数终点距离")
        accepted=reachable and distance<=5
        trials.append(dict(index=index,cell=cell,reachable=reachable,dist_to_final=distance if reachable else None,accepted=accepted))
        if accepted:
            break
        pool.pop(index)
    destination=pool[index] if pool else origin
    return dict(origin=origin,destination=destination,reach_offset=offset,used_fallback=not pool,
                remaining_candidates=pool,trials=trials)
