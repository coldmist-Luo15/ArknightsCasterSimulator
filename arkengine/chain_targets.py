"""Select an unvisited, live ground receiver by collider contact and root distance."""
from math import hypot

from .baked_mount import single
from .stage_channel import UnsupportedChannelScene, object_position_frame
from .target_priority import nearest_hatred_winner


def projectile_hatred_tie(world,keys,frame,born):
    observed={k:object_position_frame(world.births[k],frame,world.positions,world.active,k,
              observer_born=born,observer_phase="COROUTINES",same_phase_rule=getattr(world,"enemy_before_projectile",None)) for k in keys}
    selected=world.priority(keys,frame,frame_by_target=observed,nearest_tie=True)
    return selected["target"] if selected else None


def nearest_chain_target(frame,center,rows,radii,visited,radius,*,max_candidates=None,nearest_tie=None,continuous=False):
    distances=[]
    count=0
    for row in rows:
        key=row["id"]
        x=single(single(row["col"])-single(center["col"]))
        y=single(single(row["row"])-single(center["row"]))
        gap=hypot(x,y)-radius-radii[key]
        if not continuous and abs(gap)<1e-5:
            raise UnsupportedChannelScene(f"F{frame} 弹射范围与 {key} 碰撞圆相切")
        if gap<0 or continuous and gap==0:
            if continuous and max_candidates is not None and count==max_candidates:
                break
            count+=1
            if key not in visited:
                distances.append((single(single(x*x)+single(y*y)),key,row))
    if max_candidates is not None and count>max_candidates:
        raise UnsupportedChannelScene(f"F{frame} 弹射候选超过选择器的 {max_candidates} 实体上限")
    distances.sort(key=lambda r:r[0])
    if len(distances)>1:
        eps=single(1e-5)
        def equal_distance(low, high):
            # LT(low,high) and GT(high,low) round the respective adjusted operand.
            less = low < single(high-eps)
            greater = high > single(low+eps)
            if not continuous and less != greater:
                raise UnsupportedChannelScene(f"F{frame} 距离恰在单精度舍入边界，需原生候选排序顺序")
            return not less
        tied=[(d,k,r) for d,k,r in distances if equal_distance(distances[0][0],d)]
        if len(tied)>1:
            if not continuous and len(tied)<len(distances) and equal_distance(tied[-1][0],distances[len(tied)][0]):
                raise UnsupportedChannelScene(f"F{frame} 多个浮点距离在容差边缘交叠，需原生候选排序顺序")
            keys={k for _,k,_ in tied}
            if nearest_tie is not None:
                result=nearest_tie(keys)
            elif all("hatred_q32" in r and "instance_uid" in r for _,_,r in tied):
                result=nearest_hatred_winner({k:[r["hatred_q32"]] for _,k,r in tied},{k:r["instance_uid"] for _,k,r in tied})
            else:
                result=None
            if result is None and continuous:
                return tied[0][1]
            if result is None:
                raise UnsupportedChannelScene(f"F{frame} 等距弹射需要该帧的仇恨和实体分配次序")
            return result
    return distances[0][1] if distances else None
