"""Marcille's S3 chain and high-ground shard control on ordinary stage waves."""
from collections import defaultdict

from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_first_spell import _selected_clip
from .caster_prefabs import caster_prefab_evidence,sample_clip_events
from .damage import HP_EPSILON
from .fixed import ONE
from .marcil_blast import run_marcil_blast
from .marcil_deploy import ordinary_splash,run_marcil_deploy
from .marcil_mana import _blast_payments
from .mystic_timing import fixed_wait_frames
from .ranged_stage import prepare_ranged_stage
from .stage_channel import object_position_frame
from .volley_damage import _raw_f32


UID="char_4141_marcil"
_WALK=("free_motion_sample","exit_motion_sample","post_wait_motion_sample")


def run_marcil_stage(tables,request):
    deploy,button,*later=request["actions"]
    end=request["end_frame"]
    withdrawals=[a for a in later if a.get("kind")=="withdraw"]
    stops=[a for a in later if a.get("kind")!="withdraw"]
    if (button!=dict(kind="skill",operator_id=UID,frame=button["frame"],phase="after_tick")
            or not deploy["frame"]<button["frame"]<=end or len(stops)>1 or len(withdrawals)>1):
        raise ValueError("玛露西尔需要一次部署和一次 after_tick 按键")
    chant_options={}
    if stops:
        stop,=stops
        if (stop!=dict(kind="chant_stop",operator_id=UID,frame=stop["frame"],phase="input") or
                not button["frame"]<stop["frame"]<=end):
            raise ValueError("追加吟唱的中止操作需在按键之后的 INPUT")
        chant_options["repress_frame"]=stop["frame"]
    withdrawal=next(iter(withdrawals),None)
    if withdrawal:
        leave,phase=withdrawal["frame"],withdrawal["phase"]
        if (withdrawal!=dict(kind="withdraw",operator_id=UID,frame=leave,phase=phase)
                or phase not in ("input","after_tick") or not max(button["frame"],chant_options.get("repress_frame",0))<leave<=end):
            raise ValueError("撤退需要发生在最后一次技能操作之后的实际输入阶段")
    card,stage,dep,event,world=prepare_ranged_stage(tables,dict(request,actions=[deploy,button]),operator=UID,
        manual=True,portable=True,track_exits=True,native_first_walk=True,simulate_before_button=True)
    if card["skill"]!=3:
        raise ValueError("此处使用玛露西尔三技能")
    cfg={k:card[k] for k in ("operator_id","elite","level","potential","skill","skill_level","module","module_level")}
    birth=run_marcil_deploy(tables,card,dict(frame=dep["frame"],trigger_frame=button["frame"],phase=button["phase"],
                            chant_options=chant_options,facing=event["view"]))
    start=birth["activation"]
    base=dict(stage=stage["code"],squad=cfg,deployment=dep,birth_gate=birth["birth_gate"],
              mana_at_entry=birth["mana_at_entry"],source=stage["source"])
    if start["cast_frame"] is None:
        return dict(base,start=start,status=start["status"],through_frame=button["frame"],hits=[],stuns=[])
    if chant_options and start["cast_frame"]!=chant_options["repress_frame"]:
        raise ValueError("该次输入未在追加吟唱窗口中完成提交")

    m=world.scene.level["mapData"]["map"]
    high=[(r,c) for r in range(len(m)) for c in range(len(m[0])) if world.scene.tile(r,c)["heightType"]=="HIGHLAND"]
    args=dict(placement=dep,rows=len(m),cols=len(m[0]),high_tiles=high,targets=[])
    for key,stats in world.stats.items():
        attrs=world.scene.enemies[stats["enemy_key"]]["attributes"]
        args["targets"].append(dict(id=key,hp_q32=_raw_f32(stats["hp"]),res_q32=_raw_f32(stats["res"]),
            radius=tables.enemy_circle(stats["prefab"]),eligible=True,stun_immune=bool(attrs["stunImmune"]["m_value"])))

    step=int(tables.fixed_timestep()*ONE)
    buff=tables.read("buff_table.json")["stun"]
    if (buff["overrideType"],buff["lifeTimeType"],buff["statusResistable"])!=("DEFAULT","LIMITED","AUTOMATIC"):
        raise ValueError("高台碎片的标准眩晕配置变化")
    holds=defaultdict(list)
    motion_events=[]
    boundary=None

    def route_frame(key,frame):
        return frame-sum(min(frame,hi)-lo+1 for lo,hi in holds[key] if lo<=frame)

    def observed(key,frame,born):
        return object_position_frame(world.births[key],frame,world.positions,world.active,key,
                                     observer_born=born,observer_phase="COROUTINES")

    def positions(frame,born,hp):
        if world.settle_exits(frame,lambda key:hp[key]>HP_EPSILON) is not None:
            return None
        result={}
        for key in hp:
            if hp[key]<=HP_EPSILON:
                continue
            tick=route_frame(key,observed(key,frame,born))
            if key in world.active.get(tick,()):
                result[key]=world.positions[tick][key]
        return result

    def hold(key,frame,born,seconds):
        nonlocal boundary
        if frame>=end:
            return True
        tick=observed(key,frame,born)
        first=tick+1
        duration=(_raw_f32(seconds)+step-1)//step
        last=first+duration-1
        pauses=holds[key]
        if pauses and first<=pauses[-1][1]+1:
            extension=max(0,last-pauses[-1][1])
            pauses[-1]=(pauses[-1][0],max(last,pauses[-1][1]))
        else:
            source=route_frame(key,tick)
            pair=[[row for kind,row in world.history.get(f,()) if kind in _WALK and row["instance_id"]==key]
                  for f in (source,source+1)]
            if (any(len(rows)!=1 for rows in pair) or pair[0][0].get("leg_end") or
                    pair[0][0].get("leg_source")!=pair[1][0].get("leg_source")):
                boundary=dict(target=key,frame=frame,reason="shard_at_route_state_transition")
                return False
            for before in range(source+1,end+1):
                if any(kind=="checkpoint_begin" and row["instance_id"]==key and
                    row["command"] in ("WAIT_CURRENT_FRAGMENT_TIME","WAIT_CURRENT_WAVE_TIME")
                    for kind,row in world.history.get(before,())):
                    boundary=dict(target=key,frame=frame,reason="shard_shifts_an_absolute_route_wait")
                    return False
            pauses.append((first,last))
            extension=duration
        if extension:
            head=world.exits[:world.exit_index]
            world.exits=head+sorted((f+(extension if target==key else 0),target)
                                    for f,target in world.exits[world.exit_index:])
            motion_events.append(dict(kind="shard_stun",id=key,frame=frame,position=world.positions[route_frame(key,tick)][key],
                                      frozen_through=pauses[-1][1]))
        return True

    spec=caster_prefab_evidence(tables,UID,3,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    hp={x["id"]:x["hp_q32"] for x in args["targets"]}
    params={x["id"]:x for x in args["targets"]}
    ordinary=[]
    dt=tables.fixed_timestep()
    stats=card["panel_attributes"]
    interval=single(single(stats["baseAttackTime"])*100/single(stats["attackSpeed"]))
    duration=fixed_wait_frames(single(interval/dt))
    cells=range_cells(card["base_range"],dep,args["rows"],args["cols"])

    def ordinary_attacks(first,through,mana):
        if first>through:
            return
        branches=[c["fields"] for c in spec["character"]["components"]
                  if c["script"]=="MeleeAttack" and c["path"] in
                  ("Modes/Default/Body/Attack/Normal","Modes/Default/Body/Attack/Power")]
        if len(branches)!=2 or any((a["_animKey"],a["_timeMode"],a["_maxAnimScale"],a["_waitForAttackEvent"],
                                   a["_preDelay"],a["_interuptIfTargetDead"])!=("Attack",0,1,1,0,0) for a in branches):
            raise ValueError("玛露西尔的普攻动作或时钟发生变化")
        clip,_=_selected_clip(tables,spec,"Attack",event["view"])
        scale=max(single(.1),min(1,single(interval/single(clip["duration"]))))
        sample,=sample_clip_events(spec["animations"],event["view"],clip["clip"],playback_speed=single(1/scale),timestep=dt)
        if clip["loop"] or sample["event"]!="OnAttack":
            raise ValueError("玛露西尔普攻需要一条已核对的 OnAttack 动作")
        cast_ticks=max(duration,fixed_wait_frames(single(single(clip["duration"])*scale/dt)))
        search=first
        while search<=through:
            if world.settle_exits(search,lambda key:hp[key]>HP_EPSILON) is not None:
                return
            options={}
            for key in hp:
                if hp[key]<=HP_EPSILON:
                    continue
                tick=route_frame(key,object_position_frame(world.births[key],search,world.positions,world.active,key,
                    observer_born=dep["frame"],observer_phase="INPUT"))
                if key in world.active[tick] and circle_in_range(*world.positions[tick][key],params[key]["radius"],cells,box_query=True):
                    options[key]=tick
            if not options:
                search+=3
                continue
            if len(options)==1:
                target,=options
            else:
                ranked=world.priority(set(options),search,frame_by_target=options)
                if ranked is None:
                    raise ValueError("玛露西尔普攻需要实际的路程优先级")
                target=ranked["target"]
            center=world.positions[options[target]][target]
            ordinary.append(dict(kind="ordinary_attack_start",frame=search,id=target,mana=mana))
            impact=search+sample["clip_tick"]
            if impact>through or world.settle_exits(impact,lambda key:hp[key]>HP_EPSILON) is not None:
                return
            present={}
            for key in hp:
                tick=route_frame(key,impact)
                if hp[key]>HP_EPSILON and key in world.active[tick]:
                    present[key]=world.positions[tick][key]
            row,col=present.get(target,center)
            attack=ordinary_splash(tables,card,dict(frame=impact,input_point=dict(row=row,col=col),
                mana_at_search_q32=mana*ONE,targets=[dict(params[key],hp_q32=hp[key],row=r,col=c)
                                                     for key,(r,c) in present.items()]))
            for hit in attack["hits"]:
                key=hit["target"]
                hp[key]=hit["hp_after_q32"]
                ordinary.append(dict(kind="ordinary_hit",frame=impact,id=key,radius=attack["radius"],
                                     attack_q32=attack["attack_q32"],damage_q32=hit["damage_q32"],hp_after_q32=hp[key]))
            search+=cast_ticks

    ordinary_attacks(birth["birth_gate"]["born_exit_frame"]+1,button["frame"],birth["mana_at_entry"])
    clock=start["blast_clock"]
    committed=not withdrawal or leave>start["cast_frame"] or leave==start["cast_frame"] and (
        phase=="after_tick" or "repress_frame" in chant_options)
    if committed:
        run=run_marcil_blast(tables,card,spec,args,start=start,positions=positions,
                            horizon=leave if withdrawal else end,on_shard=hold,receiver_hp=hp)
    else:
        prefix=[e for e in start["events"] if e["frame"]<leave or e["frame"]==leave and phase=="after_tick"]
        interrupted=dict(status="chant_interrupted",cast_frame=None,spell_frame=None,events=prefix)
        run=dict(start=interrupted,clock=None,hits=[],stuns=[],hp_after_q32=hp)
    if "hits" not in run:
        return dict(base,through_frame=start["cast_frame"],**run)
    status="horizon"
    through=end
    if boundary:
        status,through=boundary["reason"],boundary["frame"]
    else:
        resume=clock["first_unblocked_recovery_tick"]
        if resume is not None:
            ordinary_attacks(resume,leave if withdrawal else end,clock["mana_after"])
        world.settle_exits(through,lambda key:hp[key]>HP_EPSILON)
    if world.defeated_at is not None:
        status,through="stage_defeat",world.defeated_at
    events=[*(dict(e,kind="explosion_hit") for e in run["hits"]),*motion_events,*ordinary]
    if withdrawal and leave<=through:
        payments=_blast_payments(start,leave,ends_in_input=phase=="input") if committed else []
        saved=payments[-1]["remaining"] if payments else birth["mana_at_entry"]
        events.append(dict(kind="withdraw",frame=leave,phase=phase,mana_saved=saved))
        run["withdrawal"]=dict(frame=leave,phase=phase,mana_saved=saved)
    events.sort(key=lambda e:e["frame"])
    out=dict(base,through_frame=through,status=status,stop=status,events=events,**run)
    if boundary:
        out["unresolved"]=boundary
    return world.stage_result(out,fixed_timestep=tables.fixed_timestep(),target_field="id",hp_field="hp_after_q32")
