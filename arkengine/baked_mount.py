"""Sample a non-looping V079 baked position track and apply its prefab parent transforms."""
import math
import struct


def single(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def baked_position(track, seconds, timestep):
    count = track["frameCount"]
    subdivisions = track["bakedStepInterval"]
    data = track["trsData"]
    if type(count) is not int or count < 1 or type(subdivisions) is not int or subdivisions < 1:
        raise ValueError("烘焙挂点采样数或采样间隔无效")
    if not math.isfinite(seconds) or not math.isfinite(timestep) or seconds < 0 or timestep <= 0:
        raise ValueError("烘焙采样时间无效")
    flags = data[1:4]
    if len(flags) != 3 or any(v not in (0.0, 1.0) for v in flags):
        raise ValueError("未支持的烘焙位置通道")
    lengths = [1 if flag else count for flag in flags]
    if data[0] != 3 + sum(lengths) or len(data) <= int(data[0]):
        raise ValueError("烘焙位置数据长度不符")
    # _UpdateCachePointDict uses Q32 step/subdivisions and FloorToInt.
    step_raw = int(timestep * (1 << 32)) // subdivisions
    index = min(int(single(seconds) * (1 << 32)) // step_raw, count - 1)
    step = single(step_raw / (1 << 32))
    blend = min(1.0, max(0.0, single(single(seconds - single(index * step)) / step)))
    offset = 4
    result = []
    for length in lengths:
        i = 0 if length == 1 else index
        a = single(data[offset + i])
        b = single(data[offset + min(i + 1, length - 1)])
        result.append(single(a + single(blend * single(b - a))))
        offset += length
    return tuple(result)


def mount_parents(objects, transform, root_id):
    """Parent transforms of a baked mount up to its prefab root, nearest first."""
    ref = transform["m_Father"]
    parents = []
    seen = set()
    while ref["m_PathID"] and ref["m_FileID"] == 0:
        path_id = ref["m_PathID"]
        if path_id in seen:
            raise ValueError("挂点父级存在循环")
        seen.add(path_id)
        tree = objects[path_id].read_typetree()
        go_id = tree["m_GameObject"]["m_PathID"]
        parents.append(dict(path_id=path_id, object=objects[go_id].read_typetree()["m_Name"],
                            position=tree["m_LocalPosition"], rotation=tree["m_LocalRotation"],
                            scale=tree["m_LocalScale"]))
        if go_id == root_id:
            return parents
        ref = tree["m_Father"]
    raise ValueError("挂点的父级未连接到该模板根对象")


def transform_point(point, parents):
    """Local TRS composition; the order matches Unity Transform.TransformPoint."""
    for parent in parents:
        x, y, z = (a * parent["scale"][k] for a, k in zip(point, "xyz"))
        qx, qy, qz, qw = (parent["rotation"][k] for k in "xyzw")
        if not math.isclose(qx*qx + qy*qy + qz*qz + qw*qw, 1, abs_tol=1e-5):
            raise ValueError("模板旋转四元数未归一化")
        tx, ty, tz = 2*(qy*z - qz*y), 2*(qz*x - qx*z), 2*(qx*y - qy*x)
        pos = parent["position"]
        point = (pos["x"] + x + qw*tx + qy*tz - qz*ty,
                 pos["y"] + y + qw*ty + qz*tx - qx*tz,
                 pos["z"] + z + qw*tz + qx*ty - qy*tx)
    return tuple(map(single, point))
