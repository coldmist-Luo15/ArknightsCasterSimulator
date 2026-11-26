"""Read action bindings and event keys from a verified local character skin."""
from functools import lru_cache
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from .baked_mount import mount_parents
from .opening_scene import bundle_environment
from .runtime_data import DATA_ROOT


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = DATA_ROOT / "runs/2026-11-17-H9-6-independent/spine-runtime/spine-core.js"
READER = ROOT / "tools/read_spine_events.cjs"


def read_spine_events(script):
    payload=script.encode("utf-8","surrogateescape") if isinstance(script,str) else bytes(script)
    return _read_spine_events(payload)


@lru_cache(maxsize=24)
def _read_spine_events(payload):
    with TemporaryDirectory() as temp:
        binary = Path(temp) / "action.skel"
        output = Path(temp) / "action.json"
        binary.write_bytes(payload)
        crashes={0x80000003,0xc0000005,0xc0000374}
        attempts=((),("--jitless","--no-expose-wasm"),()) if sys.platform=="win32" else ((),)
        for flags in attempts:
            run=subprocess.run(["node",*flags,str(READER),str(RUNTIME),str(binary),str(output)],
                               stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            if run.returncode==0:
                return json.loads(output.read_bytes())
            if sys.platform!="win32" or run.returncode&0xffffffff not in crashes:
                run.check_returncode()
        run.check_returncode()


def read_skin_actions(tables, member, skin_name, action_keys, mounts=(), *, front_only=(), allow_unbound=False):
    raw, source = tables.verified_bundle(member)
    env = bundle_environment(raw)
    roots = [(name, ref.deref()) for name, ref in env.container.items()
             if "/prefabs/skins/character/" in name and name.rsplit("/", 1)[-1] == skin_name + ".prefab"]
    if len(roots) != 1:
        raise ValueError(f"外观 {skin_name} 在 {member} 中未唯一定位")
    container, root = roots[0]

    def target(ref):
        if ref["m_FileID"] != 0 or not ref["m_PathID"]:
            raise ValueError(f"外观 {skin_name} 含外部或空的资源引用")
        return root.assets_file.objects[ref["m_PathID"]]

    components = [target(ref["component"]) for ref in root.read_typetree()["m_Component"]]
    skins = [(obj.path_id, tree) for obj in components if obj.type.name == "MonoBehaviour"
             and "_animations" in (tree := obj.read_typetree())]
    if len(skins) != 1:
        raise ValueError("未唯一定位外观动作映射")
    component_id, skin = skins[0]
    bindings = [row for row in skin["_animations"] if row["animKey"] in action_keys]
    keys = [row["animKey"] for row in bindings]
    if len(set(keys)) != len(keys) or (not allow_unbound and sorted(keys) != sorted(action_keys)):
        raise ValueError("外观动作映射缺失或重复")
    source.update(container=container, root_path_id=root.path_id, component_path_id=component_id)
    views = {}
    for view in ("front", "back"):
        skeleton = target(skin["_" + view]["skeleton"]).read_typetree()
        data = target(skeleton["skeletonDataAsset"]).read_typetree()
        asset = target(data["skeletonJSON"])
        script = asset.read_typetree()["m_Script"]
        decoded = read_spine_events(script)
        animations = {entry["name"]: entry for entry in decoded["animations"]}
        actions = {}
        for binding in bindings:
            if view == "back" and binding["animKey"] in front_only:
                continue
            if allow_unbound and binding["animName"] not in animations:
                actions[binding["animKey"]] = dict(clip=binding["animName"], status="clip_missing_in_this_view")
                continue
            anim = animations[binding["animName"]]
            actions[binding["animKey"]] = dict(clip=anim["name"], duration=anim["duration"],
                loop=bool(binding["loop"]), speed=binding["speed"],
                events=[dict(name=e["name"], seconds=e["time"]) for e in anim["events"]])
        views[view] = dict(skeleton_path_id=asset.path_id, spine_version=decoded["spineVersion"],
                           skeleton_sha256=decoded["skeletonSha256"], actions=actions)
        if mounts:
            parents = {}
            for number, field in mounts:
                ref = skin["_" + view][field]
                node = target(ref).read_typetree()
                parents[number] = mount_parents(root.assets_file.objects, node, root.path_id)
            views[view]["mount_parents"] = parents
        source["runtime_sha256"] = decoded["runtimeSha256"]
    result = dict(source=source, views=views)
    switcher = skin.get("_faceSwitcher")
    if switcher and not switcher["m_FileID"] and switcher["m_PathID"]:
        horizontal = target(switcher).read_typetree().get("_defaultLOrR")
        if horizontal in (1, 3):
            result["default_horizontal_face_sign"] = 1 if horizontal == 1 else -1
    return result
