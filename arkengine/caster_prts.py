"""Select revision-pinned PRTS evidence for a single caster configuration."""
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json

from .opening_scene import COMMIT
from .runtime_data import DATA_ROOT

DATA = DATA_ROOT / "mechanics/caster-prts.json"
DATA_SHA256 = "614567cd43c04f60e7f3a7e8912aad56e89dba8cfab1b6d192bdfd38b24d8223"


# Permanent revision links also work without a local copy of the wiki notes.
PAGE_REVISIONS = {
    "char_1038_whitw2": 446481,
    "char_1040_blaze2": 446744,
    "char_1046_sbell2": 446621,
    "char_134_ifrit": 446705,
    "char_164_nightm": 446711,
    "char_180_amgoat": 446390,
    "char_2013_cerber": 446496,
    "char_2015_dusk": 446667,
    "char_213_mostma": 442621,
    "char_297_hamoni": 446455,
    "char_344_beewax": 446562,
    "char_377_gdglow": 446428,
    "char_4027_heyak": 446487,
    "char_4046_ebnhlz": 450494,
    "char_4080_lin": 446545,
    "char_4133_logos": 446567,
    "char_4141_marcil": 446552,
    "char_4146_nymph": 446578,
    "char_4204_mantra": 446746,
    "char_4226_veen": 446691,
    "char_4227_gallus": 446406,
    "char_4229_aphris": 446565,
    "char_426_billro": 446498,
    "char_450_necras": 446634,
    "char_466_qanik": 446701,
    "char_472_pasngr": 446713,
}


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
    if not DATA.is_file():
        revision = PAGE_REVISIONS[character_id]
        return dict(source=dict(revision=revision, permalink=f"https://prts.wiki/index.php?oldid={revision}"),
                    notes_available=False)
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
