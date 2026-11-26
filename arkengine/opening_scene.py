"""Load a normal stage and its opening dependencies from the indexed CN snapshot."""

from dataclasses import dataclass
from copy import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile
from .runtime_data import DATA_ROOT


if sys.platform=="win32" and sys.version_info[:2]==(3,14):
    # Select both Python fallbacks before UnityPy imports its native type-tree node class.
    sys.modules.setdefault("UnityPy.UnityPyBoost",None)


DEFAULT_SNAPSHOT = DATA_ROOT / "runs/2026-11-18-cn2791-gamedata"
INDEX_SHA256 = "120a1e5d17aa2f3654247581b317429ff0da55027f3bf624460e4db6881b3e74"
COMMIT = "7e560050b03c34661db1d322d008733072352b2a"
VERSION = "[CN UPDATE] Client:2.7.91 Data:26-11-13-06-37-23-89286612"
PREFAB_ROOTS_SHA256 = "c68415e7e2e458da598985b4382935acb3d89c794119c56d5b9b86ccce640a26"
CATALOG_SHA256 = "fbcc848ed5900ee3e486d68e12d2ee788f37eb02f4e4af23a800afc71e163868"
TIME_SETTINGS_SHA256 = "bd2717e42be5e9c7e0a83c8337ee551778c58737a0ca03a24f10bffdbf8bc3f9"


class PinnedTables:
    """Verify the index and every consumed file; cache parsed tables within a run."""

    battle_seed = 0
    _group_level = None
    _after_spawn_groups = None

    def for_combat(self, seed):
        # A replay may rebuild its wave queue. Isolate its initial random state
        # from other requests while sharing the immutable tables and bundle caches.
        scene = copy(self)
        scene.battle_seed = seed
        scene._group_level = scene._after_spawn_groups = None
        return scene

    def admit_combat_actions(self, level):
        from .battle_random import BattleRandom
        from .opening_spawns import select_combat_actions
        if self._group_level is level:
            return self._admitted_actions, self._after_spawn_groups.draws
        stream = BattleRandom(self.battle_seed)
        self._admitted_actions = select_combat_actions(level, stream)
        self._group_level = level
        self._after_spawn_groups = stream
        return self._admitted_actions, stream.draws

    def combat_random(self, seed):
        from .battle_random import BattleRandom
        if self._after_spawn_groups is None or seed != self.battle_seed:
            return BattleRandom(seed)
        stream = copy(self._after_spawn_groups)
        stream.values = stream.values.copy()
        return stream

    def __init__(self, snapshot=DEFAULT_SNAPSHOT):
        self.root = Path(snapshot)
        try:
            manifest = json.loads((self.root / "manifest.json").read_bytes())
        except FileNotFoundError as exc:
            raise FileNotFoundError("请先运行 python -m tools.prepare_data 准备 V079 数据") from exc
        self.public_assets = manifest.get("asset_delivery") == "official_single"
        index = (self.root / "files.jsonl").read_bytes()
        if (manifest["commit"] != COMMIT or manifest["version_label_in_commit"] != VERSION
                or manifest["index"]["sha256"] != INDEX_SHA256
                or hashlib.sha256(index).hexdigest() != INDEX_SHA256):
            raise ValueError("配置提交、资源版本或逐文件哈希索引与锁定版本不符")
        self._entries = {entry["path"]: entry for entry in map(json.loads, index.splitlines())}
        self._cache = {}
        self._prefab_data = {}
        self._prefab_roots = {}
        self._source_name = manifest["source"]
        self.begin_scene()

    def begin_scene(self):
        """Start a fresh read ledger while reusing verified immutable input bytes."""
        self.source = dict(server="zh_CN", source=self._source_name, commit=COMMIT,
                           version_label=VERSION, index_sha256=INDEX_SHA256, files={}, official_prefabs={})

    def read(self, relative):
        path = "zh_CN/gamedata/" + relative
        entry = self._entries.get(path)
        if entry is None:
            raise ValueError(f"锁定版本未收录文件 {path}")
        if relative not in self._cache:
            data = (self.root / path).read_bytes()
            if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"文件大小或 SHA-256 不匹配 {path}")
            self._cache[relative] = json.loads(data)
        self.source["files"][path] = dict(sha256=entry["sha256"], bytes=entry["bytes"])
        return self._cache[relative]

    def placement(self, key):
        """Read slot count and placement conditions on the official hot-update prefab."""
        category = "characters" if key.startswith("char_") else "[uc]tokens"
        fields = ("_occupiedRemainingCharacterCnt", "_buildCondition", "_rewriteTileOptions",
                  "_rewriteTileHeightTypeOnMode", "_rewriteTileHeightTypeOnModeBySelf", "_hideTileOptions")
        return self._component_fields(category, key, fields)

    def enemy_steering(self, prefab_key):
        """Read the enemy's own steering component from the matching release bundle."""
        fields = ("_steeringFactor", "_maxSteeringForce", "_halfBodyWidth")
        return self._component_fields("enemies", prefab_key, fields)

    def enemy_birth(self, prefab_key):
        fields = ("_delayToBorn", "_onlyDelayToBornOnTileStart", "_idleWhenBorn")
        return self._component_fields("enemies", prefab_key, fields, "enemy_birth_prefabs")

    def enemy_circle(self, prefab_key):
        fields = ("m_Radius", "m_Offset", "m_Enabled", "m_IsTrigger")
        shape = self._component_fields("enemies", prefab_key, fields, "enemy_circle_prefabs",
                                       component_type="CircleCollider2D")
        if (not shape["m_Enabled"] or not shape["m_IsTrigger"] or any(shape["m_Offset"].values())
                or shape["m_Radius"] <= 0):
            raise ValueError(f"{prefab_key} 的根碰撞体不是受支持的居中触发圆")
        return shape["m_Radius"]

    def mystic_attack(self, key):
        return self._component_fields("characters", key, ("_chargeAnimKey", "_timeMode", "_resetCdStrategy",
                                      "_waitForAttackEvent", "_projectileKey", "_mountPointType",
                                      "_useMountGroup", "_projectileDestination"), "mystic_attack_prefabs",
                                      children=("Modes", "Default", "Body", "Attack"))

    def character_birth(self, key):
        return self._component_fields("characters", key, ("_useRealBornTimeFromAnim", "m_overrideBornTime"),
                                      "character_birth_prefabs")

    def mystic_storage(self, key):
        attack = self._component_fields("characters", key, ("_chargeAbility", "_useChargeableGroup"),
                                        "mystic_storage_binding", children=("Modes", "Default", "Body", "Attack"))
        storage = self._component_fields("characters", key, ("_maxChargeTimes", "_maxExChargeTimes",
                                         "_forceExCharge", "_projectileKey", "_exProjectileKey",
                                         "_mountPointForProjectiles", "_exMounts"),
                                         "mystic_storage_prefabs",
                                         children=("Trait", "ChargeAttackGroup", "ExChargeAttackDefault"))
        target = self.source["mystic_storage_prefabs"][key]["component_path_id"]
        if attack["_useChargeableGroup"] or attack["_chargeAbility"] != dict(m_FileID=0, m_PathID=target):
            raise ValueError("默认普攻未绑定当前储能组件")
        return storage

    def fixed_timestep(self):
        member = "assets/bin/Data/globalgamemanagers"
        if member not in self._cache:
            path = self.root.parent / "2026-11-18-cn2791-base-index/extracted" / member
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != TIME_SETTINGS_SHA256:
                raise ValueError("基础包时间设置 SHA-256 不符")
            manager, = [obj for obj in load_unity_data(raw).objects if obj.type.name == "TimeManager"]
            self._cache[member] = manager.path_id, manager.read_typetree()["Fixed Timestep"]
        path_id, seconds = self._cache[member]
        self.source["time_manager"] = dict(member=member, path_id=path_id, sha256=TIME_SETTINGS_SHA256)
        return seconds

    def verified_bundle(self, member):
        release = self.root.parent / "2026-11-18-core-cn2791"
        if not hasattr(self, "_catalog"):
            catalog = (release / "hot_update_list.json").read_bytes()
            if hashlib.sha256(catalog).hexdigest() != CATALOG_SHA256:
                raise ValueError("官方热更目录 SHA-256 不符")
            self._catalog = {row["name"]: row for row in json.loads(catalog)["abInfos"]}
        entry = self._catalog[member]
        single = str(PurePosixPath(member).with_suffix("")).replace("/", "_").replace("#", "__")
        archive_name = (entry.get("pid") or single) + ".dat"
        if self.public_assets:
            from .public_assets import fetch_bundle
            archive_name = single + ".dat"
            dest = release / "hot-update" / archive_name
            if not dest.is_file():
                fetch_bundle(member, entry, dest)
        with zipfile.ZipFile(release / "hot-update" / archive_name) as archive:
            raw = archive.read(member)
        if len(raw) != entry["abSize"] or hashlib.md5(raw).hexdigest() != entry["md5"]:
            raise ValueError(f"官方模板包长度或 MD5 不符 {member}")
        return raw, dict(archive=archive_name, member=member, sha256=hashlib.sha256(raw).hexdigest(),
                         official_md5=entry["md5"], catalog_sha256=CATALOG_SHA256)

    def prefab_root(self, asset):
        if self.public_assets:
            if not self._prefab_roots:
                raw = (self.root / "zh_CN/resource_manifest_idx.json").read_bytes()
                if hashlib.sha256(raw).hexdigest() != self._entries["zh_CN/resource_manifest_idx.json"]["sha256"]:
                    raise ValueError("资源定位表与 V079 索引不符")
                resource = json.loads(raw)
                bundles = resource["bundles"]
                self._prefab_roots.update((r["assetName"], (bundles[r["bundleIndex"]]["name"], r["path"]))
                                          for r in resource["assetToBundleList"]
                                          if r["assetName"].startswith("battle/prefabs/"))
            if asset not in self._prefab_roots:
                return None
            key = ("public_prefab_root", asset)
            if key not in self._prefab_data:
                member, path = self._prefab_roots[asset]
                raw, source = self.verified_bundle(member)
                obj = bundle_environment(raw).container[path].deref()
                self._prefab_data[key] = dict(bundle=member, container_path=path, root_path_id=obj.path_id,
                                              source_archive=source["archive"])
            return self._prefab_data[key]
        if not self._prefab_roots:
            data = (self.root / "official-prefab-roots.jsonl").read_bytes()
            if hashlib.sha256(data).hexdigest() != PREFAB_ROOTS_SHA256:
                raise ValueError("官方资源定位索引 SHA-256 不符")
            entries = map(json.loads, data.splitlines())
            self._prefab_roots.update((e["asset"], e) for e in entries)
        return self._prefab_roots.get(asset)

    def _component_fields(self, category, key, fields, ledger="official_prefabs", component_type="MonoBehaviour",
                          children=()):
        cache_key = (category, key, fields, component_type, children)
        if cache_key in self._prefab_data:
            data, evidence = self._prefab_data[cache_key]
            self.source.setdefault(ledger, {})[key] = evidence
            return data
        entry = self.prefab_root(f"battle/prefabs/{category}/{key}")
        if entry is None:
            raise ValueError(f"官方对象索引未收录 {category}/{key}")
        raw, evidence = self.verified_bundle(entry["bundle"])
        if entry["source_archive"] != evidence["archive"]:
            raise ValueError("对象索引与官方目录指向不同资源包")
        data, component = _read_fields(raw, entry, fields, component_type, children)
        evidence.update(container=entry["container_path"], root_path_id=entry["root_path_id"],
                        component_path_id=component)
        self._prefab_data[cache_key] = (data, evidence)
        self.source.setdefault(ledger, {})[key] = evidence
        return data


def load_unity_data(raw, **options):
    import UnityPy
    from UnityPy.helpers import TypeTreeHelper

    # The native type-tree reader can corrupt references during GC on CPython 3.14 for Windows.
    if sys.platform=="win32" and sys.version_info[:2]==(3,14):
        TypeTreeHelper.read_typetree_boost=None
    return UnityPy.load(raw, **options)


@lru_cache(maxsize=6)
def bundle_environment(raw):
    """Share read-only parsed Unity bundles across lookups of identical verified bytes."""
    from fsspec.implementations.memory import MemoryFileSystem
    from UnityPy.enums import CompressionFlags
    from UnityPy.helpers import CompressionHelper
    from tools.vendor.lz4ak import decompress_lz4ak

    CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress_lz4ak
    # Every external dependency is loaded explicitly by its verified catalog entry.
    # UnityPy's local-file fallback would search the entire workspace for each preload.
    return load_unity_data(raw, fs=MemoryFileSystem(), path="")


def _read_fields(raw, entry, keys, component_type="MonoBehaviour", children=()):
    env = bundle_environment(raw)
    roots = [ref for path, ref in env.container.items()
             if path.casefold() == entry["container_path"].casefold()]
    if len(roots) != 1:
        raise ValueError(f"部署对象容器内的路径不唯一 {entry['container_path']}")
    root = roots[0].deref()
    if root.path_id != entry["root_path_id"] or root.type.name != "GameObject":
        raise ValueError("部署对象根 ID 或类型不符")
    for name in children:
        objects = root.assets_file.objects
        components = [objects[ref["component"]["m_PathID"]] for ref in root.read_typetree()["m_Component"]]
        transform, = [obj.read_typetree() for obj in components if obj.type.name == "Transform"]
        matches = []
        for ref in transform["m_Children"]:
            child_transform = objects[ref["m_PathID"]].read_typetree()
            child = objects[child_transform["m_GameObject"]["m_PathID"]]
            if child.read_typetree()["m_Name"] == name:
                matches.append(child)
        if len(matches) != 1:
            raise ValueError(f"官方对象中 {name} 子节点缺失或不唯一")
        root = matches[0]
    if component_type == "GameObject":
        data = root.read_typetree()
        return {k: data[k] for k in keys}, root.path_id
    found = []
    for ref in root.read_typetree()["m_Component"]:
        pointer = ref["component"]
        if pointer["m_FileID"] != 0:
            continue
        target = root.assets_file.objects[pointer["m_PathID"]]
        if target.type.name == "Transform" and component_type == "CircleCollider2D":
            scale = target.read_typetree()["m_LocalScale"]
            if scale["x"] != 1 or scale["y"] != 1:
                raise ValueError("根碰撞体有额外缩放")
        if target.type.name != component_type:
            continue
        fields = target.read_typetree()
        if keys[0] in fields:
            found.append(({k: fields[k] for k in keys if k in fields}, target.path_id))
    if len(found) != 1:
        raise ValueError(f"对象 {entry['container_path']} 的 {keys[0]} 组件缺失或不唯一")
    return found[0]


@dataclass
class OpeningScene:
    stage: dict
    level: dict
    enemies: dict
    predefines: list
    device_skills: dict
    predefine_slot_costs: dict
    source: dict

    def enemy_level(self, enemy):
        """Level-zero DB rows overlay a newly allocated EnemyData whose levelType starts at NORMAL."""
        field = self.enemies[enemy]["levelType"]
        return field["m_value"] if field["m_defined"] else "NORMAL"

    def tile(self, row, col):
        """Game row 0 is the bottom row. map[0] is the top row."""
        data = self.level["mapData"]
        grid = data["map"]
        if (type(row) is not int or type(col) is not int or not 0 <= row < len(grid)
                or not 0 <= col < len(grid[0])):
            raise ValueError(f"地图坐标越界或不是整数 ({row}, {col})")
        return data["tiles"][grid[len(grid) - 1 - row][col]]


def _enemy_rows(level, tables):
    if level["enemies"]:
        raise ValueError("尚未实现关卡内联敌人数据")
    refs = level["enemyDbRefs"] or []
    if not refs:
        return {}
    database = {entry["Key"]: entry["Value"] for entry in
                tables.read("levels/enemydata/enemy_database.json")["enemies"]}
    enemies = {}
    for ref in refs:
        key = ref["id"]
        if (ref["useDb"] is not True or ref["overwrittenData"] is not None
                or ref["level"] != 0 or key in enemies):
            raise ValueError(f"尚未支持敌人行的覆盖、非基础等级或重复引用 {key}")
        rows = [r for r in database.get(key, []) if r["level"] == ref["level"]]
        if len(rows) != 1:
            raise ValueError(f"敌人基础等级行不唯一或缺失 {key}")
        enemies[key] = rows[0]["enemyData"]
    return enemies


def _opening_predefines(scene, tables):
    group = scene.level["predefines"] or {}
    if group.get("characterCards") or group.get("tokenCards"):
        raise ValueError("尚未实现关卡预置手牌")
    for category in ("characterInsts", "tokenInsts"):
        for i, unit in enumerate(group.get(category) or []):
            if unit["hidden"]:
                continue
            if any(unit.get(k) for k in ("overrideSkillBlackboard", "overrideTalents", "tmplId", "uniEquipIds")):
                raise ValueError("尚未实现预置单位的技能覆盖、天赋覆盖、模板或模组")
            scene.tile(**unit["position"])
            scene.predefines.append(unit)
            key = unit["inst"]["characterKey"]
            identity = unit["alias"] or f"{category}[{i}]"
            if identity in scene.predefine_slot_costs:
                raise ValueError(f"预置单位标识冲突 {identity}")
            scene.predefine_slot_costs[identity] = tables.placement(key)["_occupiedRemainingCharacterCnt"]
            skills = tables.read("excel/character_table.json")[key]["skills"]
            if not skills:
                continue
            index, rank = unit["skillIndex"], unit["mainSkillLvl"]
            if not 0 <= index < len(skills):
                raise ValueError(f"预置单位技能序号未支持 {key} {index}")
            skill_id = skills[index]["skillId"]
            levels = tables.read("excel/skill_table.json")[skill_id]["levels"]
            if not 1 <= rank <= len(levels):
                raise ValueError(f"预置单位技能等级无效 {key} {rank}")
            scene.device_skills[identity] = levels[rank - 1]


def load_opening_scene(snapshot=DEFAULT_SNAPSHOT, *, stage_id, tables=None):
    tables = tables if tables is not None else PinnedTables(snapshot)
    tables.begin_scene()
    stage = tables.read("excel/stage_table.json")["stages"].get(stage_id)
    if stage is None or stage["difficulty"] != "NORMAL":
        raise ValueError(f"需要已收录的普通难度关卡 ID，收到 {stage_id}")
    level = tables.read("levels/" + stage["levelId"].lower() + ".json")
    if (any(r["difficultyMask"] != "FOUR_STAR" for r in level["runes"] or [])
            or level["optionalRunes"] or level["globalBuffs"]):
        raise ValueError("尚未解析影响普通关开局的符文、可选词条或全局状态")
    grid, tiles = level["mapData"]["map"], level["mapData"]["tiles"]
    if (not grid or not grid[0] or any(len(row) != len(grid[0]) for row in grid)
            or any(type(i) is not int or not 0 <= i < len(tiles) for row in grid for i in row)):
        raise ValueError("地图格网或地块索引无效")
    scene = OpeningScene(stage, level, _enemy_rows(level, tables), [], {}, {}, tables.source)
    _opening_predefines(scene, tables)
    for wave in level["waves"]:
        for fragment in wave["fragments"]:
            for action in fragment["actions"]:
                if action["actionType"] == "SPAWN":
                    route = action["routeIndex"]
                    if type(route) is not int or not 0 <= route < len(level["routes"]):
                        raise ValueError(f"出兵行引用无效路线 {route}")
                    if action["key"] not in scene.enemies:
                        raise ValueError(f"出兵行引用未知敌人 {action['key']}")
    return scene
