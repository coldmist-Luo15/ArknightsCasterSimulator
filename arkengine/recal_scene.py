"""V079 H5-1 scene preprocessing, before battle actors and the wave scheduler start."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from math import prod
import re

from .attributes import base_integer_at_level
from .opening_scene import OpeningScene, _enemy_rows


STAGE_ID = "level_recalrune_01-03"
DEVICE = "trap_006_antidr"
ATTRIBUTE = {"atk":"atk", "max_hp":"maxHp", "def":"def", "magic_resistance":"magicResistance",
             "move_speed":"moveSpeed", "taunt_level":"tauntLevel"}
# The selected class-cost groups in this V079 stage cover these three professions each.
COST_GROUPS = {22: {"TANK","SNIPER","SUPPORT"}, 104: {"SPECIAL","CASTER","MEDIC"}}


def selected_runes(stage, rune_ids):
    if not isinstance(rune_ids, (list,tuple)) or any(not isinstance(k,str) for k in rune_ids):
        raise ValueError("全息请求须给出已选词条 ID 列表")
    if len(set(rune_ids)) != len(rune_ids):
        raise ValueError("全息词条重复")
    options = stage["runes"]
    unknown = set(rune_ids) - options.keys()
    if unknown:
        raise ValueError(f"未知的全息 H5-1 词条 {sorted(unknown)}")
    def group(r):
        return r["exclusiveGroupId"] or r["runeId"]
    required = {group(r) for r in options.values() if r["essential"]}
    selected = sorted((options[k] for k in rune_ids), key=lambda r:r["sortId"])
    groups = [group(r) for r in selected]
    missing = required - set(groups)
    if missing:
        raise ValueError(f"全息必选组尚未选择 {sorted(missing)}")
    if len(groups) != len(set(groups)):
        raise ValueError("全息同一互斥组不能选取多个词条")
    return selected


def board(effect):
    return {b["key"]:b["value"] if b["valueStr"] is None else b["valueStr"] for b in effect["blackboard"]}


def describe_selection(stage, rune_ids):
    """Resolve the displayed branch and its blackboard values from the same stage as the replay."""
    chosen = selected_runes(stage, rune_ids)
    groups = defaultdict(list)
    for rune in sorted(stage["runes"].values(), key=lambda r: r["sortId"]):
        groups[rune["exclusiveGroupId"] or rune["runeId"]].append(rune["runeId"])
    rows = []
    for r in chosen:
        packed = r["packedRune"]
        values = {k: v for effect in packed["runes"] for k, v in board(effect).items()}
        def number(match):
            value = values[match[1]]
            return f"{value:.0%}" if match[2] == ":0%" else f"{value:g}"
        description = re.sub(r"<@[^>]*>|</>", "", packed["description"])
        description = re.sub(r"<([^<>]+)>", r"\1", description)
        description = re.sub(r"\{([\w.]+)(:0%|:0)?\}", number, description)
        group = r["exclusiveGroupId"] or r["runeId"]
        choices = groups[group]
        rows.append(dict(id=r["runeId"], group=group, choice=choices.index(r["runeId"])+1,
                         choices=len(choices), score=r["score"], description=description))
    return rows


@dataclass
class RecalRules:
    score: int
    rune_ids: tuple
    squad_limit: int = 13
    enabled_groups: set = field(default_factory=set)
    enemy_buffs: dict = field(default_factory=dict)
    token_cards: dict = field(default_factory=dict)
    char_modifiers: list = field(default_factory=list)

    def prepare_panel(self, operator_id, profession, panel):
        """Apply the rune preprocessing pass once to a fresh panel, before battle buffs."""
        result = panel.copy()
        adjustments = defaultdict(list)
        for key, mask, b in self.char_modifiers:
            if b.get("char", operator_id) != operator_id or (mask=="TRAP" and profession!="TRAP"):
                continue
            if key == "char_cost_mul":
                if profession in COST_GROUPS[mask]:
                    adjustments["cost"].append(("mul", b["scale"]))
            elif key == "char_respawntime_mul":
                adjustments["respawnTime"].append(("mul", b["scale"]))
            else:
                for name,value in b.items():
                    if name != "char":
                        adjustments[ATTRIBUTE[name]].append((key.removeprefix("char_attribute_"),value))
        for name,mods in adjustments.items():
            if name in result:
                result[name] = modified(result[name], mods)
        return result


def modified(value, modifiers):
    # AdditiveMul runes register their blackboards in one group. The group
    # is applied to the data before on-field buffs are attached (V079).
    return ((value + sum(v for k,v in modifiers if k=="add"))
            * (1 + sum(v for k,v in modifiers if k=="additive_mul"))
            * prod(v for k,v in modifiers if k=="mul"))


def load_recal_scene(tables, stage_id, rune_ids):
    if stage_id != STAGE_ID:
        raise ValueError(f"全息关卡入口未接入 {stage_id}")
    tables.begin_scene()
    season = tables.read("excel/crisis_v2_table.json")["recalRuneData"]["seasons"]["recalRune_season_1"]
    stage = season["stages"][stage_id]
    # Ordinary scene consumers use code/name and test difficulty before running
    # ordinary-stage victory rules. Recal stages have no ordinary difficulty.
    stage = dict(stage, code=stage["levelCode"], name=stage["levelName"],
                 difficulty=None, stageType=None)
    selection = selected_runes(stage, rune_ids)
    rules = RecalRules(sum(r["score"] for r in selection),tuple(r["runeId"] for r in selection))
    level = deepcopy(tables.read("levels/"+stage["levelId"].lower()+".json"))
    enemies = deepcopy(_enemy_rows(level,tables,allow_talent_override=True))
    attr = defaultdict(list)
    device_buffs = {}
    buildable = ("RANGED",)
    rules.enemy_buffs = {uid:{} for uid in enemies}
    for r in selection:
        for effect in r["packedRune"]["runes"]:
            key, b = effect["key"], board(effect)
            if key.startswith("char_"):
                rules.char_modifiers.append((key,effect["selector"]["professionMask"],b))
            elif key.startswith("enemy_") or key=="env_gbuff_new_with_verify" and "char" not in b:
                targets = b["enemy"].split("|") if "enemy" in b else enemies
                for uid in targets:
                    unit = enemies[uid]
                    if key.startswith("enemy_attribute_"):
                        for name,value in b.items():
                            if name!="enemy":
                                attr[uid,ATTRIBUTE[name]].append((key.removeprefix("enemy_attribute_"),value))
                    elif key=="enemy_attackradius_mul":
                        unit["rangeRadius"]["m_value"] *= b["scale"]
                    elif key=="enemy_talent_blackb_add":
                        rows = {v["key"]:v for v in unit["talentBlackboard"]}
                        for name,value in b.items():
                            if name!="enemy":
                                rows[name]["value"] += value
                    elif key in ("env_gbuff_new_with_verify","enemy_dynamic_ability_new"):
                        rules.enemy_buffs[uid][b["key"]] = b
                        immune = {"silenceimmue":"silenceImmune","stunimmue":"stunImmune","frozenimmue":"frozenImmune"}
                        if b["key"] in immune:
                            unit["attributes"][immune[b["key"]]] = {"m_defined":True,"m_value":True}
                    elif key=="enemy_abnormal_immune" and b.get("sleeping")==1:
                        unit["attributes"]["sleepImmune"] = {"m_defined":True,"m_value":True}
                    else:
                        raise ValueError(f"全息敌人词条尚未解析 {key}")
            elif key=="level_hidden_group_enable":
                rules.enabled_groups.add(b["key"])
            elif key=="level_hidden_group_disable":
                rules.enabled_groups.discard(b["key"])
            elif key=="global_squad_num_limit":
                rules.squad_limit = int(b["value"])
            elif key=="global_lifepoint":
                level["options"]["maxLifePoint"] = int(b["value"])
            elif key=="global_placable_char_num_add":
                level["options"]["characterLimit"] += int(b["value"])
            elif key=="global_cost_recovery_mul":
                level["options"]["costIncreaseTime"] *= b["scale"]
            elif key in ("deck_card_buff","card_buff_with_verify") and b["char"]==DEVICE:
                buildable = ("MELEE","RANGED") if b["buildable_type"]==1 else ("RANGED",)
            elif key=="env_gbuff_new_with_verify" and b.get("char")==DEVICE:
                device_buffs[b["key"]]=b
            else:
                raise ValueError(f"全息词条尚未解析 {key}")
    for (uid,name),mods in attr.items():
        item = enemies[uid]["attributes"][name]
        if not item["m_defined"]:
            raise ValueError(f"全息敌人缺少基础属性 {uid}/{name}")
        item["m_value"] = modified(item["m_value"], mods)
    # Preserve action indices used by native replay IDs and affect admission only.
    for wave in level["waves"]:
        for fragment in wave["fragments"]:
            for action in fragment["actions"]:
                if action["hiddenGroup"] in rules.enabled_groups:
                    action["hiddenGroup"] = None
    unit = tables.read("excel/character_table.json")[DEVICE]
    preset, = level["predefines"]["tokenCards"]
    if preset["inst"]["characterKey"]!=DEVICE:
        raise ValueError("全息 H5-1 的干扰装置预设已改变")
    phase = unit["phases"][0]
    frames = phase["attributesKeyFrames"]
    panel = {k:base_integer_at_level(frames,preset["inst"]["level"],k)
             for k in ("maxHp","atk","def","cost","respawnTime","blockCnt","tauntLevel")}
    panel["magicResistance"] = frames[0]["data"]["magicResistance"]
    skill_id = unit["skills"][preset["skillIndex"]]["skillId"]
    skill = tables.read("excel/skill_table.json")[skill_id]["levels"][preset["mainSkillLvl"]-1]
    rules.token_cards[DEVICE] = dict(initial_count=preset["initialCnt"],panel=rules.prepare_panel(DEVICE,"TRAP",panel),
        slots=tables.placement(DEVICE)["_occupiedRemainingCharacterCnt"],buildable=buildable,
        skill=skill,range=tables.read("excel/range_table.json")[skill["rangeId"]],buffs=device_buffs)
    scene = OpeningScene(stage,level,enemies,[],{},{},tables.source,recal=rules)
    return scene
