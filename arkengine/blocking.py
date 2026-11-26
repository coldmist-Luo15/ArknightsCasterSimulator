"""Admission and release at Character/BlockManager callbacks for ordinary combat units."""
from math import isfinite, sqrt

from .baked_mount import single
from .fixed import ONE
from .skill_recovery import _divide


# V079 .rodata at 0x9d84d60. These are separately stored squared radii.
DEFAULT_RADIUS_SQUARED = {"WALK":0.4999903738498688,"FLY":0.7999513745307922}
MIN_BLOCK_SEPARATION_SQUARED = single(single(.1)**2)
CLOSE_DISTANCE = single(.4)
CLOSE_DISTANCE_SQUARED = single(CLOSE_DISTANCE*CLOSE_DISTANCE)
MERGE_FACTOR = single(.2)
BLOCK_TWEEN_DURATION = int(single(.2)*ONE)


def block_distance_squared(blocker_pos,enemy_pos):
    if not all(len(p)==2 and all(type(x) in (int,float) and isfinite(x) for x in p) for p in (blocker_pos,enemy_pos)):
        raise ValueError("阻挡判定需要两个地图平面坐标")
    x,y=(single(single(a)-single(b)) for a,b in zip(enemy_pos,blocker_pos))
    return single(single(x*x)+single(y*y))


def _weight_bucket(distance_squared):
    # WeightedTarget's ordinary small weights use Floor(weight / FP.EN3).
    return _divide(int(distance_squared*ONE),_divide(ONE,1000*ONE))//ONE


def _delta(a,b):
    return tuple(single(single(x)-single(y)) for x,y in zip(a,b))


def _sum(a,b):
    return tuple(single(x+y) for x,y in zip(a,b))


def _scale(v,scale):
    return tuple(single(x*scale) for x in v)


def _length(v):
    return single(sqrt(single(single(v[0]*v[0])+single(v[1]*v[1]))))


def _normal(v):
    length=_length(v)
    return tuple(single(x/length) for x in v) if length>single(1e-5) else (0.,0.)


def character_block_destination(blocker,enemy,stable_positions):
    """Character's ordinary minBlockDist=0.5. Coordinates are (row,col) in native (+y,+x)."""
    offset=_delta(enemy,blocker)
    if block_distance_squared(blocker,enemy)<.25:
        offset=_scale(_normal(offset),.5)
    candidate=_sum(blocker,offset)
    stable_positions=tuple(stable_positions)
    distances=[block_distance_squared(candidate,other) for other in stable_positions]
    if any(d<MIN_BLOCK_SEPARATION_SQUARED for d in distances):
        separation=(0.,0.)
        for other,distance in zip(stable_positions,distances):
            if distance<MIN_BLOCK_SEPARATION_SQUARED:
                row,col=_delta(other,blocker)
                # ARM64 computes (+dy,-dx) in XY order.
                push=_normal((-col,row))
            elif distance<CLOSE_DISTANCE_SQUARED:
                push=_normal(_delta(candidate,other))
                push=_scale(_scale(push,single(CLOSE_DISTANCE-single(sqrt(distance)))),50.)
            else:
                continue
            separation=_sum(separation,push)
        direction=_normal(_sum(offset,_scale(_normal(separation),MERGE_FACTOR)))
        candidate=_sum(blocker,_scale(direction,_length(offset)))
    return candidate


def block_tween_position(origin,destination,elapsed_q32):
    """Tween.Tick followed by the Vector2 setter on the deterministic battle path."""
    if type(elapsed_q32) is not int or elapsed_q32<0:
        raise ValueError("阻挡补间需要非负的定点时间")
    if elapsed_q32>=BLOCK_TWEEN_DURATION:
        return destination
    ratio=single(_divide(elapsed_q32,BLOCK_TWEEN_DURATION)/ONE)
    return _sum(origin,_scale(_delta(destination,origin),ratio))


def enemy_block_profile(tables,prefab_key):
    profile=tables._component_fields("enemies",prefab_key,("_blockVolume","_specificBlockMask","_specialBlockCondition"),
                                     "enemy_blocking_prefabs")
    condition=profile["_specialBlockCondition"]
    return dict(base_volume=profile["_blockVolume"],specific_mask=profile["_specificBlockMask"],
                special_condition=condition)


class CharacterBlocking:
    """One blocker. The scene supplies real search opportunities and updates its moving enemies."""
    def __init__(self,position,capacity,*,mode="WALK",side_mask=2,ignore_block_mode=False,override_radius_squared=None):
        if mode not in DEFAULT_RADIUS_SQUARED or type(capacity) is not int or capacity<0 or type(side_mask) is not int:
            raise ValueError("阻挡者的模式、容量或阵营掩码无效")
        block_distance_squared(position,position)
        self.position, self.capacity, self.mode, self.side_mask = position,capacity,mode,side_mask
        if type(ignore_block_mode) is not bool:
            raise ValueError("阻挡模式忽略标志必须是布尔值")
        self.ignore_block_mode=ignore_block_mode
        value=DEFAULT_RADIUS_SQUARED[mode] if override_radius_squared is None else override_radius_squared
        if type(value) not in (float,int) or not isfinite(value) or value<0:
            raise ValueError("阻挡半径平方无效")
        self.radius_squared=single(value)
        self.blocked={}

    @property
    def used(self):
        return sum(self.blocked.values())

    def check(self,enemy):
        eid=enemy["id"]
        if eid in self.blocked or enemy["blocked_elsewhere"]:
            return "already_blocked"
        if not enemy["alive"] or not enemy["has_combat"] or enemy["block_free"]:
            return "enemy_unavailable"
        if not self.ignore_block_mode and enemy["motion_mode"]!=self.mode:
            return "motion_mode"
        if not enemy["side"] or enemy["side"] & ~self.side_mask:
            return "block_side_mask"
        if not enemy["special_condition_passed"]:
            return "special_block_condition"
        if block_distance_squared(self.position,enemy["position"])>self.radius_squared:
            return "outside_block_radius"
        volume=enemy["base_volume"]+enemy["volume_addition"]
        if type(volume) is not int or volume<0:
            raise ValueError("阻挡体积无效")
        if self.capacity<=self.used or self.used+volume>self.capacity:
            return "insufficient_capacity"
        return "eligible"

    def register(self,enemy):
        status=self.check(enemy)
        if status=="eligible":
            self.blocked[enemy["id"]]=enemy["base_volume"]+enemy["volume_addition"]
            status="registered"
        return dict(status=status,enemy=enemy["id"],used=self.used,remaining=self.capacity-self.used)

    def release(self,enemy_id):
        existed=enemy_id in self.blocked
        self.blocked.pop(enemy_id,None)
        return dict(status="released" if existed else "already_free",enemy=enemy_id,used=self.used,
                    remaining=self.capacity-self.used)


class StationaryBlockSearch:
    """One live blocker at a supplied first search, updated before caster/projectile objects."""
    def __init__(self,tables,setup,targets):
        self.manager=CharacterBlocking(setup["position"],setup["capacity"],mode=setup["mode"])
        self.step=int(tables.fixed_timestep()*ONE)
        self.tweens={}
        self.stable={}
        self.first=setup["first_search_frame"]
        self.withdraw=setup.get("withdraw_frame")
        if type(self.first) is not int or self.first<0 or (self.withdraw is not None
                and (type(self.withdraw) is not int or self.withdraw<=self.first)):
            raise ValueError("阻挡者的首次实际搜索与撤退帧无效")
        self.candidates={}
        for target in targets:
            p=enemy_block_profile(tables,target["prefab"])
            condition=p["special_condition"]["_type"]
            if condition and "special_condition_passed" not in target:
                raise ValueError("靶场需要核对该敌人特殊阻挡条件")
            self.candidates[target["id"]]=dict(id=target["id"],position=(target["row"],target["col"]),
                instance_uid=target.get("instance_uid"),
                motion_mode=target["motion_mode"],side=2,alive=True,has_combat=True,block_free=False,
                blocked_elsewhere=False,special_condition_passed=target.get("special_condition_passed",True),base_volume=p["base_volume"],volume_addition=0)
        self.last=None

    def update(self,frame,enemies,health,log,*,positions=None):
        # The caster may skip an uneventful animation interval while the blocker keeps ticking.
        first=frame if self.last is None else self.last+1
        if positions is not None:
            if first!=frame:
                raise ValueError("移动目标的阻挡搜索需要连续的对象更新位置")
            for eid,candidate in self.candidates.items():
                candidate["alive"]=eid in positions
                if candidate["alive"] and eid not in self.manager.blocked:
                    candidate["position"]=positions[eid]
        for tick in range(first,frame+1):
            self._update_frame(tick,enemies,health,log)
        self.last=frame
        for eid,candidate in self.candidates.items():
            enemies[eid]["position"]=candidate["position"]

    def _update_frame(self,frame,enemies,health,log):
        for eid,(began,origin,destination) in list(self.tweens.items()):
            elapsed=(frame-began)*self.step
            self.candidates[eid]["position"]=block_tween_position(origin,destination,elapsed)
            if elapsed>=BLOCK_TWEEN_DURATION:
                del self.tweens[eid]
        for eid in list(self.manager.blocked):
            if (self.withdraw is not None and frame>=self.withdraw) or health[eid]==0:
                self.manager.release(eid)
                self.stable.pop(eid)
                enemies[eid]["blocked"]=False
                log.append(dict(kind="release_block",frame=frame,target=eid))
        if frame<self.first or (self.withdraw is not None and frame>=self.withdraw) or (frame-self.first)%3:
            return
        eligible=[e for eid,e in self.candidates.items() if health[eid]>0 and self.manager.check(e)=="eligible"]
        by_distance={}
        for e in eligible:
            weight=_weight_bucket(block_distance_squared(self.manager.position,e["position"]))
            by_distance.setdefault(weight,[]).append(e)
        for weight,group in sorted(by_distance.items()):
            if self.manager.used>=self.manager.capacity:
                break
            if len(group)>1:
                uids=[e["instance_uid"] for e in group]
                if any(type(n) is not int or not 0<=n<1<<32 for n in uids) or len(set(uids))!=len(uids):
                    raise ValueError("同一阻挡距离排序档中的敌人需要各自的战斗实例 UID")
                group.sort(key=lambda e:e["instance_uid"])
            for enemy in group:
                result=self.manager.register(enemy)
                if result["status"]=="registered":
                    eid,origin=enemy["id"],enemy["position"]
                    destination=character_block_destination(self.manager.position,origin,self.stable.values())
                    self.stable[eid]=destination
                    self.tweens[eid]=(frame,origin,destination)
                    enemies[eid]["blocked"]=True
                    log.append(dict(kind="register_block",frame=frame,target=eid,used=result["used"],
                                    origin=origin,destination=destination))
