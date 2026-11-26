"""Select revision-pinned PRTS evidence for a single caster configuration."""
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json

from .opening_scene import COMMIT
from .runtime_data import DATA_ROOT

DATA = DATA_ROOT / "mechanics/caster-prts.json"
DATA_SHA256 = "614567cd43c04f60e7f3a7e8912aad56e89dba8cfab1b6d192bdfd38b24d8223"


@lru_cache(maxsize=1)
def _records():
    raw = DATA.read_bytes()
    if sha256(raw).hexdigest() != DATA_SHA256:
        raise ValueError("PRTS 机制记录摘要不匹配")
    data = json.loads(raw)
    if data["commit"] != COMMIT:
        raise ValueError("PRTS 证据对应的技能表提交不同")
    return data["operators"]


def caster_prts_evidence(character_id, skill_slot, module_id=None, module_level=None):
    """M3 text and applicable stage records. Empty notes mean the table has no remark row."""
    record = _records().get(character_id)
    if record is None or (record["skills"] or skill_slot is not None) and (
            type(skill_slot) is not int or str(skill_slot) not in record["skills"]):
        raise ValueError("PRTS 机制目录中不存在该干员或技能槽")
    module = None
    if module_id is None and module_level is not None:
        raise ValueError("未装备模组时不能选择模组等级")
    if module_id is not None:
        item = record["modules"].get(module_id)
        if item is None:
            raise ValueError("该干员没有选定的模组")
        if module_level is None:
            module_level = max(map(int, item["levels"]))
        if type(module_level) is not int or str(module_level) not in item["levels"]:
            raise ValueError("该模组没有选定的等级")
        module = dict(id=module_id, name=item["name"], level=module_level,
                      stage_changes_before_potential={level: text for level, text in item["levels"].items()
                                                      if int(level) <= module_level})
    fields = {key: record[key] for key in ("name", "source", "branch", "trait", "branch_notes", "talent_notes")}
    return deepcopy(dict(**fields, skill=record["skills"].get(str(skill_slot)), module=module))
