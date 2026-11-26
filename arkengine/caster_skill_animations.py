"""Resolve explicit skill-prefab animation references to default-skin Spine and baked tracks."""
from collections import Counter
from .animation_assets import read_skin_actions
from .caster_catalog import target_casters
from .caster_prefabs import _prefab


def _refs(component):
    fields=component["fields"]
    refs=[]
    def bind(role, field, action, enabled=True):
        if action:
            refs.append(dict(role=role, field=field, key=action, enabled=bool(enabled),
                             component=component["id"], path=component["path"], script=component["script"]))
    if "_playSkillBeginAnim" in fields:
        bind("controller_begin","_beginAnim",fields["_beginAnim"],fields["_playSkillBeginAnim"])
        bind("controller_begin_down","_beginAnimDown",fields["_beginAnimDown"],fields["_playSkillBeginAnim"])
        bind("controller_end","_endAnim",fields.get("_endAnim"))
    for key,role in (("_animKey","ability_action"),("_upAnimKey","ability_action_up"),("_downAnimKey","ability_action_down"),
                     ("_endAnimKey","ability_end"),("_downEndAnimKey","ability_end_down"),("_upEndAnimKey","ability_end_up")):
        bind(role,key,fields.get(key))
    if component["script"] in ("ThreePartChannelingAnimation","ChantBeforeSkill"):
        for prop,group,gate in (("_default","channel",True),("_up","channel_up",fields.get("_overrideUpAnimation")),
                                ("_down","channel_down",fields.get("_overrideDownAnimation")),("_animation","chant",True)):
            for sub,role in (("beginAnim","begin"),("loopAnim","loop"),("endAnim","end")):
                bind(group+"_"+role,prop+"."+sub,fields.get(prop,{}).get(sub),gate)
    return refs


def operator_skill_animations(tables, uid):
    cache=("caster_default_skin_skill_animations",uid)
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    unit=target_casters(tables)[uid]
    actions=set()
    rows=[]
    for slot,binding in enumerate(unit["skills"],1):
        sid=binding["skillId"]
        levels=tables.read("excel/skill_table.json")[sid]["levels"]
        level=1+len(unit["allSkillLvlup"])+len(binding["levelUpCostCond"] or [])
        entry=levels[level-1]
        key=binding["overridePrefabKey"] or entry["prefabId"]
        prefab=_prefab(tables,"[uc]skills",key)
        components=[c for c in prefab["components"] if c["active"] and c["enabled"]]
        ctrl,=(c for c in components if c["path"]=="" and "_playSkillBeginAnim" in c["fields"])
        f=ctrl["fields"]
        refs=[r for c in components for r in _refs(c)]
        actions.update(r["key"] for r in refs)
        rows.append(dict(slot=slot,skill_id=sid,skill_name=entry["name"],skill_level=level,skill_prefab=key,
                         skill_source=prefab["source"],controller=dict(script=ctrl["script"],id=ctrl["id"],
                         play_begin=bool(f["_playSkillBeginAnim"]),begin_key=f["_beginAnim"],down_key=f["_beginAnimDown"],
                         begin_validator=f["_beginAnimValidator"],force_default_facing=bool(f["_forceAnimFaceToDefault"])),
                         timeline_fields=[dict(script=c["script"],component=c["id"],path=c["path"],
                             values={k:v for k,v in c["fields"].items() if k in
                            ("_preDelay","_escapeTime","_timeMode","_maxAnimScale","_waitForAttackEvent","_waitForCasting",
                             "_waitForAttachFinishEvent","_fireAttackFinishEvent","_fireAnimEndEvent","_onlyPlayBeginAnimWhenFirstAttack",
                             "_delaySecond","_delayToFireAttackSkillEvent","_delayToAnimateEndAnimation","_delayToAnimateEndAnimationTime")})
                             for c in components if any(k in c["fields"] for k in ("_preDelay","_waitForCasting","_waitForAttachFinishEvent"))],
                         chant_parameters={r["key"]:r["value"] for r in entry["blackboard"] if "chant" in r["key"]},
                         references=refs))
    skin=read_skin_actions(tables,"chararts/"+uid+".ab","defaultskin",sorted(actions),allow_unbound=True)
    path=f"bakemuzzledata/character/{uid}_defaultskin.json"
    baked=tables.read(path)["bakedAnimDatas"]
    tracks={}
    for key in sorted(actions):
        views={}
        for view in ("front","back"):
            data=skin["views"][view]["actions"].get(key)
            if data is None:
                views[view]=dict(status="key_missing_in_skin")
                continue
            if "duration" not in data:
                views[view]=data
                continue
            bake=baked[view.capitalize()]["bakedEvents"].get(data["clip"])
            views[view]=dict(status="bound",**data, duration_at_binding_speed=(data["duration"]/data["speed"] if data["speed"]>0 else None),
                             baked_events=bake)
        tracks[key]=views
    for row in rows:
        control=row["controller"]; start=control["begin_key"]
        if control["play_begin"] and start:
            row["controller_begin_status"]="explicit_begin_track"
        elif control["play_begin"]:
            row["controller_begin_status"]="begin_enabled_empty_key"
        else:
            row["controller_begin_status"]="controller_begin_disabled"
    result=dict(operator_id=uid,name=unit["name"],skin="defaultskin",skin_source=skin["source"],
                skeletons={v:{k:data[k] for k in ("skeleton_path_id","spine_version","skeleton_sha256")}
                           for v,data in skin["views"].items()},
                baked_source=dict(path=path,**tables.source["files"]["zh_CN/gamedata/"+path]),skills=rows,tracks=tracks)
    tables._prefab_data[cache]=result
    return result


def skill_animation_evidence(tables,uid,slot):
    """Selected skill with only its referenced keys. Controller, chant/channel and abilities retain separate roles."""
    operator=operator_skill_animations(tables,uid)
    row,=(s for s in operator["skills"] if s["slot"]==slot)
    return dict(**row,skin_source=operator["skin_source"],skeletons=operator["skeletons"],baked_source=operator["baked_source"],
                tracks={r["key"]:operator["tracks"][r["key"]] for r in row["references"]})


def collect_skill_animations(tables):
    rows=[operator_skill_animations(tables,uid) for uid in target_casters(tables)]
    stats=Counter(s["controller_begin_status"] for op in rows for s in op["skills"])
    return dict(source=tables.source,operators=rows,scope="explicit_skill_prefab_references_and_default_skin_tracks",
                summary=dict(operators=len(rows),skill_slots=sum(stats.values()),controller_begin_status=dict(stats)))
