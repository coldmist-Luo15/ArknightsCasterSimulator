"""Read selected CN V079 battle components with their Unity object identities."""
from collections import deque
from functools import lru_cache
from hashlib import sha256
import json
from math import isfinite, isnan
import re

from .mystic_cycle import baked_key_sample
from .opening_scene import PREFAB_ROOTS_SHA256, bundle_environment
from .skill_assets import SCRIPT_ASSET_SHA256, _script_classes


def _json_values(value):
    if isinstance(value,float) and not isfinite(value):
        return {"unity_float": "NaN" if isnan(value) else ("+Infinity" if value>0 else "-Infinity")}
    if isinstance(value,list):
        return [_json_values(item) for item in value]
    if isinstance(value,dict):
        return {key:_json_values(item) for key,item in value.items()}
    return value


@lru_cache(maxsize=1)
def _root_index(root):
    raw = (root / "official-prefab-roots.jsonl").read_bytes()
    if sha256(raw).hexdigest() != PREFAB_ROOTS_SHA256:
        raise ValueError("官方预制体根索引摘要不符")
    return {obj["asset"]: obj for obj in map(json.loads, raw.splitlines())}


def _components(root, classes):
    objects = root.assets_file.objects
    results = []

    def resolve(ref):
        if ref["m_FileID"]:
            raise ValueError("子层级或组件意外引用外部 Unity 对象")
        return objects[ref["m_PathID"]]

    def visit(game_object, path, parent_active):
        data = game_object.read_typetree()
        active = parent_active and bool(data["m_IsActive"])
        children = []
        for entry in data["m_Component"]:
            obj = resolve(entry["component"])
            fields = obj.read_typetree()
            kind = obj.type.name
            script = kind
            identity = {}
            if kind == "MonoBehaviour":
                digest = obj.serialized_type.old_type_hash.hex()
                options = classes.get(digest, ())
                script = options[0]["name"] if len(options) == 1 else None
                identity = dict(properties_hash=digest, script_candidates=options)
            if kind == "Transform":
                children = fields["m_Children"]
            results.append(dict(path=path, game_object=game_object.path_id, id=obj.path_id,
                active=active, enabled=bool(fields.get("m_Enabled", 1)), type=kind, script=script,
                **identity, fields={k:_json_values(v) for k,v in fields.items()
                                    if k not in ("m_GameObject","m_Script","m_Name","m_Enabled")}))
        for ref in children:
            child = resolve(resolve(ref).read_typetree()["m_GameObject"])
            name = child.read_typetree()["m_Name"]
            visit(child, path + "/" + name if path else name, active)

    visit(root, "", True)
    return results


def _prefab(tables, kind, name):
    key = ("caster_component_tree", kind, name)
    if key not in tables._prefab_data:
        relative = f"battle/prefabs/{kind}/{name}"
        if kind in ("characters", "[uc]tokens","enemies"):
            entry = _root_index(tables.root).get(relative)
            if not entry:
                return None
            member, path = entry["bundle"], entry["container_path"]
        else:
            entry = None
            member, path = f"battle/prefabs/{kind}.ab", "dyn/" + relative + ".prefab"
        raw, source = tables.verified_bundle(member)
        env = bundle_environment(raw)
        if path not in env.container:
            return None
        root = env.container[path].deref()
        if entry and (root.path_id != entry["root_path_id"] or source["archive"] != entry["source_archive"]):
            raise ValueError("单位预制体来源与根索引不符")
        result = dict(key=name, source=dict(source, container=path, root_id=root.path_id,
                      script_asset_sha256=SCRIPT_ASSET_SHA256), components=_components(root,_script_classes(tables)))
        tables._prefab_data[key] = result
    return tables._prefab_data[key]


def _references(obj):
    """Follow exact asset IDs embedded in serialised fields. Preserve dynamic keys in the source."""
    if isinstance(obj, list):
        for value in obj:
            yield from _references(value)
    elif isinstance(obj, dict):
        if obj.get("loadFromDB") and obj.get("buffKey"):
            yield "buffs", obj["buffKey"]
        if obj.get("templateKey"):
            yield "templates", obj["templateKey"]
        for field,value in obj.items():
            # BB parameter names and audio signals can also start with "projectile_".
            if field not in ("_overrideAudioSignal","key","_blackboardKeys"):
                yield from _references(value)
    elif isinstance(obj, str):
        if obj.startswith("projectile_"):
            yield "projectiles", obj
        elif re.fullmatch(r"token_\d+_[\w\[\]-]+", obj):
            yield "tokens", obj


def _character_events(tables, operator_id, skin):
    rel = f"bakemuzzledata/character/{operator_id}_{skin}.json"
    baked = tables.read(rel)["bakedAnimDatas"]
    return dict(skin=skin, file=rel, sha256=tables.source["files"]["zh_CN/gamedata/"+rel]["sha256"],
                views={view: dict(skip_zero_frame_update=data["skipZeroFrameUpdate"], clips=data["bakedEvents"])
                       for view,data in baked.items()})


def sample_clip_events(profile, facing, clip, *, playback_speed, timestep):
    """Ticks from active clip time zero, including multiple keys. Caller supplies effective speed."""
    row = profile["views"][facing]["clips"][clip]
    return [dict(event=name, seconds=seconds, clip_tick=baked_key_sample(seconds, playback_speed, timestep)[0])
            for name,seconds in zip(row["events"], row["eventTime"], strict=True)]


def caster_prefab_evidence(tables, operator_id, skill_slot, *, skill_level=None, module_id=None,
                           module_level=None, skin="defaultskin"):
    """Selected asset definitions. Reachability in this graph does not imply activation or timing."""
    unit = tables.read("excel/character_table.json")[operator_id]
    char = _prefab(tables, "characters", operator_id)
    if unit["profession"]=="CASTER" and not unit["skills"] and skill_slot is None and skill_level is None:
        skill = sid = rank = None
        skill_board = []
        skill_seeds = []
    else:
        if unit["profession"] != "CASTER" or type(skill_slot) is not int or not 1 <= skill_slot <= len(unit["skills"]):
            raise ValueError("需要术师和有效的技能槽序号")
        binding = unit["skills"][skill_slot-1]
        sid = binding["skillId"]
        levels = tables.read("excel/skill_table.json")[sid]["levels"]
        maximum = 1 + len(unit["allSkillLvlup"]) + len(binding["levelUpCostCond"] or [])
        rank = maximum if skill_level is None else skill_level
        if type(rank) is not int or not 1 <= rank <= maximum or maximum > len(levels):
            raise ValueError("技能等级超出可训练范围")
        level = levels[rank-1]
        prefab = binding["overridePrefabKey"] or level["prefabId"]
        skill = _prefab(tables, "[uc]skills", prefab)
        if not skill:
            raise ValueError(f"技能主预制体缺失 {operator_id} / {prefab}")
        skill_board = level["blackboard"]
        skill_seeds = [skill["components"], binding, level]
    result = dict(operator_id=operator_id, skill_id=sid, slot=skill_slot, rank=rank,
        skill_blackboard=skill_board, character=char, skill=skill, module_id=module_id,
        module_level=module_level, equipment={}, buffs={}, templates={}, tokens={}, projectiles={},
        animations=_character_events(tables, operator_id, skin), unresolved=[])
    seeds = [char["components"], *skill_seeds, unit["displayTokenDict"], unit["talents"], unit.get("tokenKey")]
    if unit["displayTokenDict"]:
        seeds.extend(unit["displayTokenDict"].keys())
    if module_id is None:
        if module_level is not None:
            raise ValueError("未选择模组时不能指定模组等级")
    else:
        equipment = tables.read("excel/uniequip_table.json")
        if (module_id not in equipment["charEquip"].get(operator_id, [])
                or equipment["equipDict"][module_id]["type"] != "ADVANCED"
                or equipment["equipDict"][module_id]["isSpecialEquip"]):
            raise ValueError("选择的模组不属于该术师的常规模组")
        phases = tables.read("excel/battle_equip_table.json")[module_id]["phases"]
        chosen = max(p["equipLevel"] for p in phases) if module_level is None else module_level
        if type(chosen) is not int or chosen not in [p["equipLevel"] for p in phases]:
            raise ValueError("模组等级不存在")
        result["module_level"] = chosen
        phase = next(p for p in phases if p["equipLevel"] == chosen)
        seeds.append(phase)
        for part in phase["parts"]:
            key = part["resKey"]
            if not key:
                continue
            item = _prefab(tables, "[uc]equips", key)
            if item is None:
                result["unresolved"].append(["equipment",key])
                continue
            result["equipment"][key] = item
            seeds.append(item["components"])
    queue = deque(_references(seeds))
    seen = set()
    while queue:
        group, key = queue.popleft()
        if (group,key) in seen:
            continue
        seen.add((group,key))
        if group in ("templates","buffs"):
            file = "battle/buff_template_data.json" if group == "templates" else "buff_table.json"
            data = tables.read(file).get(key)
        elif group == "tokens":
            data = _prefab(tables, "[uc]tokens", key)
        else:
            variants = {suffix:_prefab(tables,"[uc]projectiles",key+suffix) for suffix in ("","_logic")}
            data = {("base" if not suffix else "logic"):v for suffix,v in variants.items() if v}
        if not data:
            result["unresolved"].append([group,key])
            continue
        result[group][key] = data
        if group == "projectiles":
            payload = [v["components"] for v in data.values()]
        elif group == "tokens":
            payload = data["components"]
        else:
            payload = data
        queue.extend(_references(payload))
    return result
