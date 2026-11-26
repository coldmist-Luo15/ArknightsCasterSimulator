"""Read ordinary projectile, receiver mount and tile geometry from the release assets."""
from .baked_mount import baked_position, mount_parents
from .opening_scene import bundle_environment
from .skill_assets import _script_classes


def stage_tile_positions(tables, scene):
    level = (scene.level["mapId"] or scene.stage["levelId"]).lower()
    cache = ("stage_tile_positions", scene.stage["stageId"], level)
    if cache in tables._prefab_data:
        positions, source = tables._prefab_data[cache]
        tables.source["stage_geometry"] = source
        return positions
    basename = level.rsplit("/", 1)[-1]
    member = f"scenes/{level}/{basename}.ab"
    raw, source = tables.verified_bundle(member)
    env = bundle_environment(raw)
    maps = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        node = obj.read_typetree()
        if "_anchorTransform" in node and "_tiles" in node:
            maps.append((obj, node))
    if len(maps) != 1:
        raise ValueError("关卡场景未唯一定位地图与地块数组")
    owner, node = maps[0]
    objects = owner.assets_file.objects
    positions = {}
    for ref in node["_tiles"]["_tiles"]:
        if ref["m_FileID"] or not ref["m_PathID"]:
            raise ValueError("场景地块引用未解析")
        tile = objects[ref["m_PathID"]].read_typetree()
        go = objects[tile["m_GameObject"]["m_PathID"]].read_typetree()
        components = [objects[r["component"]["m_PathID"]] for r in go["m_Component"]]
        transform, = [o.read_typetree() for o in components if o.type.name == "Transform"]
        pos = transform["m_LocalPosition"]
        row, col = int(pos["y"]), int(pos["x"])
        if (row != pos["y"] or col != pos["x"] or (row, col) in positions
                or scene.tile(row, col)["tileKey"] != tile["_tileKey"]
                or abs(pos["z"] + tile["_height"]) > 1e-5):
            raise ValueError("场景地块物理坐标与关卡格网不一致")
        positions[row, col] = dict(position=pos, locate_height_offset=tile["_locateHeightOffset"])
    source.update(map_component=owner.path_id, tile_count=len(positions))
    tables.source["stage_geometry"] = source
    tables._prefab_data[cache] = positions, source
    return positions


def enemy_hit_mount(tables, prefab):
    cache=("ground_hit_mount_and_source",prefab)
    filename=f"bakemuzzledata/enemy/{prefab}.json"
    if cache in tables._prefab_data:
        mount,sources=tables._prefab_data[cache]
        tables.read(filename)
        for ledger,entry in sources.items():
            tables.source.setdefault(ledger,{})[prefab]=entry
        return mount
    config = tables._component_fields("enemies", prefab, ("_hitTransform", "_spineScale"),
                                     "enemy_hit_mount", children=("Graphic",))
    source = tables.source["enemy_hit_mount"][prefab]
    raw, _ = tables.verified_bundle(source["member"])
    env = bundle_environment(raw)
    root = env.container[source["container"]].deref()
    ref = config["_hitTransform"]
    if ref["m_FileID"] or not ref["m_PathID"] or config["_spineScale"] != 1:
        return None
    objects = root.assets_file.objects
    tree = objects[ref["m_PathID"]].read_typetree()
    parents = mount_parents(objects, tree, root.path_id)
    # The spawned actor takes the projected route position in place of its prefab root position.
    root_parent = parents.pop()
    if (root_parent["scale"] != dict(x=1, y=1, z=1)
            or root_parent["rotation"] != dict(x=0, y=0, z=0, w=1)):
        return None
    data = tables.read(filename)["bakedAnimDatas"]
    # Movement clips can be named Run_Loop, Move, etc. Waiting and timing
    # boundaries also admit other poses. A common fixed position across every
    # baked facing and clip removes any need to guess the current animation.
    tracks = {(face, clip): points.get("1") for face, baked in data.items()
              for clip, points in baked["bakedMPDatas"].items()}
    if not tracks or any(not tr or tr["frameCount"] != 1 for tr in tracks.values()):
        return None
    positions = {baked_position(tr, 0, tables.fixed_timestep()) for tr in tracks.values()}
    if len(positions) != 1:
        return None
    heights = tables._component_fields("enemies", prefab, ("_height", "_useSpineLikeHeightUpdate"),
                                       "enemy_projectile_height")
    tables.source.setdefault("receiver_hit_pose", {})[prefab] = dict(bake_file=filename,
        clips_by_facing={face:sorted(baked["bakedMPDatas"]) for face, baked in data.items()},
        local_position=list(positions.pop()), transform_id=ref["m_PathID"], initial_height=heights["_height"])
    mount=dict(parents=parents,hit_track=next(iter(tracks.values())),transform_id=ref["m_PathID"],
               height=heights["_height"],spine_height_update=bool(heights["_useSpineLikeHeightUpdate"]))
    sources={k:tables.source[k][prefab] for k in ("enemy_hit_mount","enemy_projectile_height","receiver_hit_pose")}
    tables._prefab_data[cache]=mount,sources
    return mount


def projectile_profiles(tables, keys):
    keys = tuple(dict.fromkeys(keys))
    cache = "mystic_projectile_profiles", tuple(sorted(keys))
    if cache in tables._prefab_data:
        profiles, source = tables._prefab_data[cache]
        tables.source["ordinary_projectiles"] = source
        return profiles
    raw, source = tables.verified_bundle("battle/prefabs/[uc]projectiles.ab")
    env = bundle_environment(raw)
    classes = _script_classes(tables)
    profiles = {}
    for key in keys:
        variants = {}
        for suffix in ("", "_logic"):
            name = key + suffix
            root = env.container[f"dyn/battle/prefabs/[uc]projectiles/{name}.prefab"].deref()
            values = {}
            components = []
            for ref in root.read_typetree()["m_Component"]:
                obj = root.assets_file.objects[ref["component"]["m_PathID"]]
                if obj.type.name != "MonoBehaviour":
                    components.append(obj.type.name)
                    continue
                scripts = classes.get(obj.serialized_type.old_type_hash.hex(), ())
                components.append(scripts[0]["name"] if len(scripts) == 1 else None)
                data = obj.read_typetree()
                if "_moveType" in data:
                    fields = ("_moveType", "_speed", "_speedKey", "_delayToStart", "_delayAfterReached",
                              "_onlyReachedInTime", "_addInertia", "_useDynamicSpeed", "_flyToPredefinedLocation",
                              "_flyToTargetLocationOnly", "_comeBack", "_forceUseTargetMountPoint")
                    values["movement"] = {k: data[k] for k in fields}
                    values["movement_id"] = obj.path_id
                    values["movement_script_id"] = obj.serialized_type.script_id.hex()
                if "_stopWhenSourceInvalid" in data:
                    fields = ("_randomDelayToBorn", "_randomDelayToBornKey", "_alwaysHitTraceTargetInTheEnd",
                              "_alwaysHitTraceTargetWhenReached", "_alwaysReachInTheEnd", "_lifeTime",
                              "_stopWhenSourceInvalid")
                    values["lifecycle"] = {k: data[k] for k in fields}
                    values["projectile_id"] = obj.path_id
                    values["has_only_trace_hit"] = (bool(data["_alwaysHitTraceTargetInTheEnd"])
                        and not any(data[k] for k in ("_alwaysReachInTheEnd", "_alwaysHitTraceTargetWhenReached",
                            "_containsChildrenBehaviours", "_fireProjectileReachedEvent"))
                        and not any(data["_actionController"].values()))
            values["has_only_trace_hit"] = (values.get("has_only_trace_hit", False) and len(components) == 4
                and set(components) == {"Transform", "SimpleProjectile", "EffectBehaviour", "AdvancedMovement"})
            variants[name] = dict(root_id=root.path_id, **values)
        profiles[key] = variants
    tables.source["ordinary_projectiles"] = source
    tables._prefab_data[cache] = profiles, source
    return profiles
