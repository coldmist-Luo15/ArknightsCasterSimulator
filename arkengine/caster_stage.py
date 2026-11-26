"""Select the implemented stage behaviour using the equipped squad and stage."""
from .aphris_channel import run_aphris_channel
from .aphris_magazine_stage import run_aphris_magazine_stage
from .aphris_relays import RELAY_INPUTS
from .ceobe_live import CeobeBattle
from .nightmare_live import NightmareBattle
from .necras_stage import NecrasBattle
from .hoolheyak_live import HoolheyakBattle
from .blaze_stage import run_blaze_stage
from .elemental_casters import BLAZE
from .caster_catalog import GALLUS,resolve_caster_loadout,stage_loadout,shared_barrier_loadout
from .carnelian_live import CarnelianBattle
from .carnelian_stage import run_carnelian_stage
from .ceobe_stage import run_ceobe_stage
from .ebenholz_stage import run_ebenholz_stage
from .eyja_stage import run_eyja_stage
from .gallus_stage import run_gallus_stage
from .goldenglow_stage import run_goldenglow_stage
from .harmonie_water import run_harmonie_water_stage
from .harmonie_live import HarmonieBattle
from .hoolheyak_stage import run_hoolheyak_stage
from .hoolheyak_tornado import run_tornado_stage
from .ifrit_channel import run_ifrit_channel
from .ifrit_s2_stage import run_ifrit_stage
from .lappland_stage import run_lappland_stage
from .lin_stage import run_lin_stage
from .lin_s3_stage import run_lin_s3_stage
from .logos_stage import run_logos_s2_stage
from .logos_modes import run_logos_mode_stage
from .marcil_stage import run_marcil_stage
from .marcil_toggle import run_marcil_toggle
from .mantra_chain_stage import run_mantra_chain_stage
from .mantra_field import run_mantra_field
from .mostima_channel import run_mostima_channel
from .mostima_live import run_mostima_stage
from .nightmare_stage import run_nightmare_stage
from .nymph_stage import run_nymph_stage
from .opening_replay import run_opening
from .passenger_live import PassengerBattle
from .passenger_stage import run_passenger_stage
from .passenger_chain_stage import run_passenger_chain_stage
from .qanik_link import run_qanik_stage
from .sbell_stage import run_sbell_stage
from .stage_channel import UnsupportedChannelScene
from .veen_stage import run_veen_stage


STAGE_SKILLS = {
    **{("sub_03-3-1","char_426_billro",s):(f"carnelian_s{s}",run_carnelian_stage) for s in (1,2,3)},
    **{("sub_03-3-1","char_1046_sbell2",s):(f"sbell_s{s}",run_sbell_stage) for s in (1,2,3)},
    ("sub_03-3-1","char_466_qanik",2): ("qanik_s2_lasso",run_qanik_stage),
    ("sub_03-3-1","char_4027_heyak",2): ("hoolheyak_s2",run_hoolheyak_stage),
    **{("sub_03-3-1","char_2013_cerber",s): ("ceobe_combat",run_ceobe_stage) for s in (1,2,3)},
    ("sub_03-3-1","char_180_amgoat",2): ("eyja_s2",run_eyja_stage),
    ("sub_03-3-1","char_213_mostma",2): ("mostima_s2_channel",run_mostima_channel),
    ("sub_03-3-1","char_4229_aphris",2): ("aphris_s2_channel",run_aphris_channel),
    ("sub_03-3-1","char_134_ifrit",3): ("ifrit_s3_channel",run_ifrit_channel),
    ("sub_03-3-1","char_4141_marcil",3): ("marcil_s3_blast",run_marcil_stage),
    ("main_01-09","char_472_pasngr",3): ("passenger_s3_storm",run_passenger_stage),
}

ORDINARY_STAGE_SKILLS = {
    ("char_4046_ebnhlz",3):run_ebenholz_stage,
    **{("char_1040_blaze2",s):run_blaze_stage for s in (1,2,3)},
    **{("char_164_nightm",s):run_nightmare_stage for s in (1,2)},
    **{("char_377_gdglow",s):run_goldenglow_stage for s in (1,3)},
    **{("char_1038_whitw2",s):run_lappland_stage for s in (1,2,3)},
    ("char_213_mostma",3):run_mostima_stage,
    **{("char_4146_nymph",s):run_nymph_stage for s in (2,3)},
    ("char_4141_marcil",1):run_marcil_toggle,
    ("char_4141_marcil",3):run_marcil_toggle,
    ("char_180_amgoat",3):run_eyja_stage,
    ("char_4133_logos",2):run_logos_s2_stage,
    **{("char_4133_logos",s):run_logos_mode_stage for s in (1,3)},
    ("char_4229_aphris",2):run_aphris_channel,
    ("char_4229_aphris",3):run_aphris_magazine_stage,
    ("char_297_hamoni",2):run_harmonie_water_stage,
    ("char_472_pasngr",2):run_passenger_chain_stage,
    ("char_472_pasngr",3):run_passenger_stage,
    ("char_134_ifrit",2):run_ifrit_stage,
    ("char_134_ifrit",3):run_ifrit_stage,
    **{("char_4226_veen",s):run_veen_stage for s in (2,3)},
    ("char_4204_mantra",2):run_mantra_chain_stage,
    ("char_4204_mantra",3):run_mantra_field,
    **{("char_4080_lin",s):run_lin_stage for s in (1,2)},
    ("char_4080_lin",3):run_lin_s3_stage,
    ("char_466_qanik",2):run_qanik_stage,
    **{("char_1046_sbell2",s):run_sbell_stage for s in (1,2,3)},
    **{("char_2013_cerber",s):run_ceobe_stage for s in (1,2,3)},
    **{("char_426_billro",s):run_carnelian_stage for s in (1,2,3)},
    ("char_4027_heyak",2):run_hoolheyak_stage,
    ("char_4027_heyak",3):run_tornado_stage,
    ("char_180_amgoat",2):run_eyja_stage,
    ("char_213_mostma",2):run_mostima_stage,
}


def run_caster_stage(tables,request):
    mode=request.get("scene_model")
    actions=request.get("actions",[])
    tracked=any(k.startswith(("track_","sp_sample","cost_sample")) for k in request)
    operators={a.get("operator_id") for a in actions}
    blaze=len(request["squad"])==1 and str(request["squad"][0].get("operator",request["squad"][0].get("operator_id"))).casefold() in (
        BLAZE,"烛煌","blaze the igniting spark") and str(request["squad"][0].get("skill")).casefold() in (
        "2","3","s2","s3","沸血燎原","众恶的焚场","skchr_blaze2_2","skchr_blaze2_3")
    pair={"char_4046_ebnhlz","char_4080_lin"}
    shared=any(str(p.get("operator",p.get("operator_id"))).casefold() in ("林","lin","char_4080_lin")
               and str(p.get("skill")).casefold() in ("2","s2","荫庇","skchr_lin_2") for p in request["squad"])
    aphris="char_4229_aphris" in operators and any(str(p.get("operator",p.get("operator_id"))).casefold() in ("谬因","aphris","char_4229_aphris") and str(p.get("skill")).casefold() in ("2","3","s2","s3","临界瞬爆","混沌的本质","skchr_aphris_2","skchr_aphris_3") for p in request["squad"])
    snow=any(str(p.get("operator",p.get("operator_id"))).casefold() in ("圣聆初雪","pramanix the prerita","char_1046_sbell2") for p in request["squad"]) and len(request["squad"])==1
    logos=any(str(p.get("operator",p.get("operator_id"))).casefold() in ("逻各斯","logos","char_4133_logos") for p in request["squad"]) and len(request["squad"])==1
    eyja_in_squad=any(str(p.get("operator",p.get("operator_id"))).casefold() in ("艾雅法拉","eyjafjalla","char_180_amgoat") for p in request["squad"])
    eyja=eyja_in_squad and (len(request["squad"])==1 and "char_180_amgoat" in operators or
        bool(request.get("ally_arts_hits")) and not operators.intersection(("char_4229_aphris","char_134_ifrit")))
    nymph=len(request["squad"])==1 and "char_4146_nymph" in operators and str(request["squad"][0].get("skill")).lower() in ("2","3","s2","s3","怵然震爆","心防溃决")
    marcil="char_4141_marcil" in operators and all(a.get("kind")!="skill" or a["operator_id"]=="char_4141_marcil" for a in actions)
    ifrit_support=operators=={"char_134_ifrit","char_180_amgoat"}
    continuous_solo=len(request["squad"])==1 and bool(operators.intersection(
        ("char_4204_mantra","char_134_ifrit","char_213_mostma","char_1038_whitw2","char_377_gdglow",GALLUS)))
    live_classes={"char_2013_cerber":(CeobeBattle,(1,2)),"char_4027_heyak":(HoolheyakBattle,(3,)),"char_164_nightm":(NightmareBattle,(1,2)),
                  NecrasBattle.uid:(NecrasBattle,(1,2,3)),PassengerBattle.uid:(PassengerBattle,(2,3)),
                  CarnelianBattle.uid:(CarnelianBattle,(1,2,3)),HarmonieBattle.uid:(HarmonieBattle,(2,))}
    actors=[p for p in request["squad"] if p.get("operator_id",p.get("operator")) in live_classes or
            str(p.get("operator","")).casefold() in ("刻俄柏","ceobe","霍尔海雅","ho'olheyak","夜魔","nightmare","死芒","necrass","异客","passenger","卡涅利安","carnelian","和弦","harmonie")]
    live_handler=None
    skill_users={a["operator_id"] for a in actions if a.get("kind")=="skill"}
    if len(actors)>1 and len(skill_users)==1:
        actors=[p for p in actors if stage_loadout(tables,p)["operator_id"] in skill_users]
    if len(actors)==1:
        selected=stage_loadout(tables,actors[0])
        entry=live_classes.get(selected["operator_id"])
        if (entry and selected["skill"] in entry[1] and not (entry[0] is CarnelianBattle and (shared or marcil))
                and all(a.get("kind")!="skill" or a["operator_id"]==selected["operator_id"] for a in actions)):
            live_handler=entry[0].stage_run
    if mode is None:
        if (not tracked and (live_handler or blaze or operators and (operators<=pair or continuous_solo or marcil or ifrit_support or shared or aphris or snow or logos or nymph or eyja)) and all("frame" in a for a in actions)
                and request.get("movement") and request.get("clock",{}).get("spawn_schedule")=="native_wave"):
            mode="caster_combat"
        else:
            return run_opening(request,tables=tables)
    if mode=="caster_combat" and request.get("track_damage") and "char_4046_ebnhlz" in operators:
        # Requests for the detailed range/HP diagnostic retain its recorded scenario format.
        return run_opening({k:v for k,v in request.items() if k!="scene_model"},tables=tables)
    if mode=="caster_combat":
        tables=tables.for_combat(request.get("battle_random_seed",request.get("eyja_deployment_seed",0)))
    load=shared_barrier_loadout if shared and len(request["squad"])>1 else stage_loadout
    if mode=="caster_combat" and live_handler:
        squad=[selected if p is actors[0] else shared_barrier_loadout(tables,p,position_only=True) for p in request["squad"]]
        return live_handler(tables,dict(request,squad=squad))
    if eyja and len(request["squad"])>1:
        squad=[stage_loadout(tables,p) if str(p.get("operator",p.get("operator_id"))).casefold() in ("艾雅法拉","eyjafjalla","char_180_amgoat")
               else shared_barrier_loadout(tables,p,position_only=True) for p in request["squad"]]
    elif marcil and len(request["squad"])>1:
        squad=[stage_loadout(tables,p) if str(p.get("operator",p.get("operator_id"))).casefold() in ("玛露西尔","marcille","char_4141_marcil","卡涅利安","carnelian","char_426_billro","莫斯提马","mostima","char_213_mostma")
               else shared_barrier_loadout(tables,p,position_only=True) for p in request["squad"]]
    elif aphris and len(request["squad"])>1:
        squad=[stage_loadout(tables,p) if str(p.get("operator",p.get("operator_id"))).casefold() in ("谬因","aphris","char_4229_aphris")
               else shared_barrier_loadout(tables,p,position_only=True) for p in request["squad"]]
    else:
        squad=[load(tables,pick) for pick in request["squad"]]
    if mode=="caster_combat":
        if blaze:
            return run_blaze_stage(tables,dict(request,squad=squad))
        if len(squad)==1 and squad[0]["operator_id"]==GALLUS:
            return run_gallus_stage(tables,dict(request,squad=squad))
        if marcil:
            return run_marcil_toggle(tables,dict(request,squad=squad))
        if len(squad)==1 and squad[0]["operator_id"]=="char_213_mostma":
            return run_mostima_stage(tables,dict(request,squad=squad))
        if len(squad)==1 and squad[0]["operator_id"] in ("char_4204_mantra","char_134_ifrit"):
            if squad[0]["operator_id"]=="char_134_ifrit":
                return run_ifrit_stage(tables,dict(request,squad=squad))
            handler=run_mantra_chain_stage if squad[0]["skill"]==2 else run_mantra_field
            return handler(tables,dict(request,squad=squad))
        if logos:
            handler=run_logos_s2_stage if squad[0]["skill"]==2 else run_logos_mode_stage
            return handler(tables,dict(request,squad=squad))
        if eyja:
            return run_eyja_stage(tables,dict(request,squad=squad))
        if nymph:
            return run_nymph_stage(tables,dict(request,squad=squad))
        if snow:
            return run_sbell_stage(tables,dict(request,squad=squad))
        if aphris:
            return run_aphris_channel(tables,dict(request,squad=squad))
        reason=None
        buttons=[a for a in request["actions"] if a.get("kind")=="skill"]
        if len(squad)==1:
            pick=squad[0]
            uid,slot=pick["operator_id"],pick["skill"]
            match=STAGE_SKILLS.get((request["stage_id"],uid,slot))
            handler=match[1] if match else ORDINARY_STAGE_SKILLS.get((uid,slot))
            if handler:
                card=resolve_caster_loadout(tables,uid,slot,pick["module"],skill_level=pick["skill_level"],
                                          module_level=pick["module_level"])
                manual=card["skill_row"]["skillType"]=="MANUAL"
                snow_suspend=uid=="char_1046_sbell2" and slot==2
                if buttons and not manual and not snow_suspend:
                    raise ValueError(f"{card['skill_row']['name']}由游戏自动触发，输入中含有手动技能操作")
                if manual and not buttons and handler not in (run_blaze_stage,run_nightmare_stage,run_goldenglow_stage,run_lappland_stage,run_ceobe_stage,
                                                               run_mantra_chain_stage,run_passenger_chain_stage,
                                                               run_marcil_toggle,run_lin_stage,run_lin_s3_stage,run_ebenholz_stage):
                    reason="本场未手动开技，此技能的关卡入口尚未单独结算全程普通攻击"
                else:
                    try:
                        return handler(tables,dict(request,squad=squad))
                    except UnsupportedChannelScene as unsupported:
                        reason=str(unsupported)
        deployed={a["operator_id"] for a in actions if a.get("kind","deploy")=="deploy"}
        if "char_4080_lin" in deployed and (len(squad)>=2 and all(a["operator_id"] in ("char_4080_lin","char_426_billro","char_4046_ebnhlz") for a in buttons)
                and any(p["operator_id"]=="char_4080_lin" and p["skill"]==2 for p in squad)
                or deployed=={"char_4080_lin","char_4046_ebnhlz"} and all(p["skill"]==3 for p in squad if p["operator_id"]=="char_4046_ebnhlz")):
            lin=next(p for p in squad if p["operator_id"]=="char_4080_lin")
            handler=run_lin_s3_stage if lin["skill"]==3 else run_lin_stage
            try:
                return handler(tables,dict(request,squad=squad))
            except UnsupportedChannelScene as unsupported:
                if any(a.get("kind")=="withdraw" for a in request["actions"]):
                    return dict(status="unresolved",combat_unresolved=str(unsupported),source=tables.source)
                reason=str(unsupported)
        if len(squad)==2 and {p["operator_id"] for p in squad}=={"char_134_ifrit","char_180_amgoat"}:
            ifrit=next(p for p in squad if p["operator_id"]=="char_134_ifrit")
            handler=run_ifrit_stage if ifrit["skill"] in (2,3) else None
            if handler:
                try:
                    return handler(tables,dict(request,squad=squad))
                except UnsupportedChannelScene as unsupported:
                    return dict(status="unresolved",combat_unresolved=str(unsupported),source=tables.source)
        if (len(squad)==2 and {p["operator_id"] for p in squad}=={"char_4141_marcil","char_426_billro"} and
                any(p["operator_id"]=="char_4141_marcil" and p["skill"]==1 for p in squad)):
            try:
                return run_marcil_toggle(tables,dict(request,squad=squad))
            except UnsupportedChannelScene as unsupported:
                return dict(status="unresolved",combat_unresolved=str(unsupported),source=tables.source)
        opening={k:v for k,v in request.items() if k!="scene_model"}
        if any(a.get("kind") in RELAY_INPUTS for a in request["actions"]):
            return dict(status="unresolved",combat_unresolved=reason or "此阵容中继器操作尚未接入",source=tables.source)
        if reason:
            opening.pop("battle_random_seed",None)
            opening.pop("map_random_seed",None)
            opening.pop("ally_skill_starts",None)
            opening.pop("enemy_invisible",None)
            opening.pop("marcil_allies",None)
            opening.pop("marcil_entry_mana",None)
        if "eyja_deployment_seed" in opening:
            opening.setdefault("track_skill_points",True)
        result=run_opening(opening,tables=tables)
        if reason:
            result["combat_unresolved"]=reason
        return result
    for alias,handler in STAGE_SKILLS.values():
        if mode==alias:
            return handler(tables,dict(request,squad=squad))
    raise ValueError(f"未接入的关卡计算入口 {mode}")
