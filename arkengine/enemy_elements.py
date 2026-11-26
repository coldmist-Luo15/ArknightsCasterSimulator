"""Element gauges and burst effects on one ordinary living enemy, in receiver-update order."""
from .caster_equipment_traits import _trait_values
from .caster_catalog import resolve_caster_loadout
from .damage import HP_EPSILON, MINIMUM_RATIO, ONE_PERCENT, ordinary_arts_hit, ordinary_physical_hit
from .fixed import ONE, SIGN64, mul
from .logos_curse import LogosCurse
from .skill_recovery import READY_EPSILON, _divide
from .volley_damage import _raw_f32


# ElementType IDs 1–5 in the V079 enemy EP array.
ELEMENTS_BY_ID = ("SANITY","WATER","FIRE","DARK","ANGER")


def _raw(value):
    if type(value) is not int or not 0 <= value < SIGN64:
        raise ValueError("需要非负 Q32.32 整数")
    return value


class EnemyElements:
    """Call tick once per living enemy update. Hits before/after it use actual actor order.

    Assumes ordinary HP and synchronous buffs on an initialized enemy, no other modifiers,
    break-speed change, passive EP recovery or death interception. Fallen actors stop ticking.
    """

    @classmethod
    def from_scene_enemy(cls,tables,enemy):
        """Read the live scene's element gauge, resistance and immunity attributes."""
        return cls(tables,enemy_level=enemy["level"],hp_q32=enemy["hp_q32"],max_ep_q32=enemy.get("max_ep_q32"),
                   buildup_resistance_q32=enemy.get("ep_resistance_q32",0),
                   elemental_resistance_q32=enemy.get("ep_damage_resistance_q32",0),
                   neutral=enemy.get("neutral",False),element_immune=enemy.get("element_immune",False),
                   palsy_immune=enemy.get("palsy_immune",False))

    def __init__(self, tables, *, enemy_level, hp_q32, max_ep_q32=None, buildup_resistance_q32=0,
                 elemental_resistance_q32=0, neutral=False, element_immune=False, palsy_immune=False):
        if enemy_level not in ("NORMAL", "ELITE", "BOSS"):
            raise ValueError("需要已解析的敌人等级")
        self.max_ep = _raw(max_ep_q32 if max_ep_q32 is not None else (2000 if enemy_level=="BOSS" else 1000)*ONE)
        self.hp = _raw(hp_q32)
        self.before_fire_break = self.after_fire_break = None
        from .enemy_silence import ReceiverSilence
        self.silence=ReceiverSilence()
        if self.max_ep == 0 or self.hp <= HP_EPSILON:
            raise ValueError("受击者需要有正元素值上限且存活")
        self.buildup_resistance = _raw(buildup_resistance_q32)
        self.elemental_resistance = _raw(elemental_resistance_q32)
        if any(type(flag) is not bool for flag in (neutral,element_immune,palsy_immune)):
            raise ValueError("阵营和免疫标志需要布尔值")
        self.neutral, self.element_immune, self.palsy_immune = neutral, element_immune, palsy_immune
        self.step = int(tables.fixed_timestep()*ONE)
        self.rules = tables.read("battle/ep_breakbuff_table.json")
        if self.rules.keys()!=set(ELEMENTS_BY_ID):
            raise ValueError("敌方元素枚举与该版本的爆发表不一致")
        self.buffs = tables.read("buff_table.json")
        self.templates = tables.read("battle/buff_template_data.json")
        self.gauges = dict.fromkeys(self.rules,self.max_ep)
        self.burst = None
        self.elapsed = self.duration = 0
        self.def_add = self.res_add = self.atk_ratio = self.attack_speed_add = 0
        self.tick_due = self.weaken_due = self.ticks = 0
        self.last_tick = None
        self.last_frame = None
        self.logos_curse = None
        self.palsy_stacks = 0
        self.palsy_limit = None
        self.palsy_overflow = 0
        self.mantra_entry_timer = None
        self.logos_curse_remaining = 0
        self.gallus_marks = {}
        self.scorched_earth = {}
        self.scorch_defense = {}
        self.qanik_auras = {}
        self.airborne = False
        self.events = []

    def _at(self, frame):
        if type(frame) is not int or frame<0 or self.last_frame is not None and frame<self.last_frame:
            raise ValueError("受击事件需要单调不减的逻辑帧")
        self.last_frame = frame

    def _source_calculate_damage(self, card):
        if card is None or card["operator_id"] != "char_4133_logos":
            return
        curse = LogosCurse.from_card(self.templates, card)
        if curse is not None:
            # EXTEND refills the existing target buff; it doesn't add a stack
            # or replace its attribute snapshot. The source may leave afterward.
            if self.logos_curse is None:
                self.logos_curse = curse
            self.logos_curse_remaining = max(self.logos_curse_remaining, curse.duration)

    def _arts_hit(self, attack, resistance, output_scale, resistance_scale, received_scale=ONE):
        resistance += sum(delta for delta,remaining in self.scorched_earth.values())
        if self.logos_curse is not None:
            attack += self.logos_curse.attack_add
            resistance += self.logos_curse.resistance_add
        if resistance_scale is not None and resistance_scale != ONE:
            resistance = mul(resistance,_raw(resistance_scale))
        for scale, _ in self.gallus_marks.values():
            resistance = mul(resistance, scale)
        weakness = max((aura.multiplier for aura in self.qanik_auras.values()),default=ONE)
        return ordinary_arts_hit(attack_raw=attack, resistance_raw=resistance,
                                 hp_raw=self.hp, output_scale_raw=output_scale,received_scale_raw=mul(received_scale,weakness))

    def qanik_event(self,tables,card,source,frame,event):
        from .qanik_aura import QanikAura, fragility_profile
        self._at(frame)
        if self.hp<=HP_EPSILON:
            return
        if source not in self.qanik_auras:
            self.qanik_auras[source]=QanikAura(fragility_profile(tables,card))
        aura=self.qanik_auras[source]
        kind=event["kind"]
        if kind=="qanik_aura_contact":
            touching=event["touching"]
            if type(touching) is not bool:
                raise ValueError("光环接触状态须为布尔值")
        free=event.get("target_free",False)
        if type(free) is not bool:
            raise ValueError("目标不可选状态须为布尔值")
        if kind=="qanik_aura_contact":
            previous=aura.weak
            aura.contact(touching,self.airborne,free)
            if previous and not aura.weak:
                self.events.append(dict(kind="qanik_fragility_removed",frame=frame,source=source,reason="contact_exit"))
        else:
            aura.retry(self.airborne,free)

    def apply_gallus_mark(self, frame, source, scale, duration):
        self._at(frame)
        previous_scale, remaining = self.gallus_marks.get(source, (scale, 0))
        self.gallus_marks[source] = previous_scale, max(remaining, duration)

    def apply_scorched_earth(self,frame,owner,debuff):
        self._at(frame)
        if self.hp<=HP_EPSILON:
            return
        # Same-source copies carry the same value. Their active union lasts
        # until the last copy expires; no tick or triggered action is stacked.
        self.scorched_earth[owner]=debuff
        self.events.append(dict(kind="scorched_earth_applied",frame=frame,owner=owner,
                                resistance_add_q32=debuff[0],remaining_q32=debuff[1]))

    def receive_ordinary(self, frame, *, source, damage_type, attack_q32, mitigation_q32,output_scale_q32=ONE,
                         source_card=None, resistance_scale_q32=ONE,received_scale_q32=ONE):
        """Supply DEF or RES before shared skill debuffs and the final RES factor."""
        self._at(frame)
        if self.hp<=HP_EPSILON:
            return None
        if damage_type=="arts":
            self._source_calculate_damage(source_card)
            hit = self._arts_hit(attack_q32,mitigation_q32,output_scale_q32,resistance_scale_q32,received_scale_q32)
        elif damage_type=="physical":
            if output_scale_q32!=ONE:
                raise ValueError("当前普通物理包只处理未修正的输出")
            self._source_calculate_damage(source_card)
            defense=self.effective_defense(mitigation_q32)
            hit = ordinary_physical_hit(attack_raw=attack_q32,defense_raw=defense,hp_raw=self.hp)
        else:
            raise ValueError("普通伤害包需要法术或物理类型")
        damage=hit.damage_raw
        receiver=getattr(self,"on_take_ordinary_damage",None)
        if receiver:
            damage=receiver(frame,source,damage_type,damage)
        if damage!=hit.damage_raw:
            remaining=self.hp-damage
            self.hp=remaining if remaining>=HP_EPSILON else 0
        else:
            self.hp=hit.hp_after_raw
        row = dict(kind="ordinary_hit",frame=frame,source=source,damage_type=damage_type,
                   attack_q32=attack_q32,damage_q32=damage,hp_after_q32=self.hp)
        if damage!=hit.damage_raw:
            row["prevented_q32"]=hit.damage_raw-damage
        self.events.append(row)
        return row

    def effective_defense(self,base):
        return max(0,_raw(base)+self.def_add+sum(delta for delta,_ in self.scorch_defense.values()))

    def apply_scorch_defense(self,frame,owner,debuff):
        self._at(frame)
        if self.hp<=HP_EPSILON:
            return
        delta,life=debuff
        self.scorch_defense[owner]=(delta,life)
        self.events.append(dict(kind="scorch_defense_applied",frame=frame,owner=owner,defense_add_q32=delta))

    def apply_elemental_hp(self, frame, amount_q32, *, source="caster", output_scale_q32=ONE, source_card=None):
        self._at(frame)
        amount = _raw(amount_q32)
        if self.hp<=HP_EPSILON:
            row = dict(frame=frame,kind="elemental_hp_skipped",source=source,reason="dead")
            self.events.append(row)
            return row
        # V079 CalculateDamageInternal's ELEMENT branch uses the same arithmetic
        # order and lower bound as ordinary magical damage, with a different attribute.
        self._source_calculate_damage(source_card)
        hit = ordinary_arts_hit(attack_raw=amount,resistance_raw=self.elemental_resistance,hp_raw=self.hp)
        damage = mul(hit.damage_raw,_raw(output_scale_q32))
        self.hp = max(0,self.hp-damage)
        if self.hp<HP_EPSILON:
            self.hp = 0
        row = dict(frame=frame,kind="elemental_hp",source=source,base_q32=amount,
                   damage_q32=damage,hp_after_q32=self.hp)
        self.events.append(row)
        return row

    def add_palsy(self,frame,count,source):
        self._at(frame)
        if (self.palsy_immune and self.palsy_limit is None) or self.hp<=HP_EPSILON:
            return
        cap=self.buffs["palsy[stack]"]["maxStackCnt"] if self.palsy_limit is None else self.palsy_limit
        if self.palsy_limit is not None:
            self.palsy_overflow+=max(0,self.palsy_stacks+count-cap)
        self.palsy_stacks=min(cap,self.palsy_stacks+count)
        self.events.append(dict(kind="palsy_stack_grant",frame=frame,source=source,stacks=self.palsy_stacks))

    def limit_palsy(self,cap):
        self.palsy_limit=cap
        if cap is not None and self.palsy_stacks>cap:
            self.palsy_overflow+=self.palsy_stacks-cap
            self.palsy_stacks=cap

    def apply_buildup(self, frame, element, amount_q32):
        self._at(frame)
        if element not in self.gauges:
            raise ValueError("未知的元素类型")
        amount = _raw(amount_q32)
        reason = ("dead" if self.hp<=HP_EPSILON else "neutral" if self.neutral else
                  "element_immune" if self.element_immune else "break_recovery" if self.burst else None)
        if reason:
            row = dict(frame=frame,kind="buildup_blocked",element=element,reason=reason)
        else:
            factor = max(0,ONE-mul(self.buildup_resistance,ONE_PERCENT))
            damage = max(mul(amount,MINIMUM_RATIO),mul(amount,factor))
            left = max(0,self.gauges[element]-damage)
            self.gauges[element] = 0 if left<READY_EPSILON else left
            row = dict(frame=frame,kind="buildup",element=element,amount_q32=damage,
                       remaining_ep_q32=self.gauges[element])
        self.events.append(row)
        if not reason and not self.gauges[element]:
            if element=="FIRE" and self.before_fire_break:
                self.before_fire_break(frame)
            self._begin(frame,element)
            if element=="FIRE" and self.after_fire_break:
                self.after_fire_break(frame)
        return row

    def apply_module_arts(self, frame, card, modifier_value_q32):
        icon = card["module_info"]["typeIcon"]
        if icon not in ("msc-d","ccr-d","bla-d"):
            raise ValueError("所选模组没有此项法伤转元素损伤特性")
        ratio = _raw_f32(_trait_values(card)["ep_damage_ratio"])
        return self.apply_buildup(frame,"FIRE" if icon=="bla-d" else "DARK",mul(_raw(modifier_value_q32),ratio))

    def hit_with_module_arts(self, frame, card, *, attack_q32, effective_arts_resistance_q32,source=None,
                             resistance_scale_q32=ONE):
        """The supplied RES includes FIRE, before shared skill debuffs and the final factor."""
        self._at(frame)
        if card["module_info"]["typeIcon"] not in ("msc-d","ccr-d","bla-d"):
            raise ValueError("该命中需要带有法伤转元素损伤特性的模组")
        if self.hp<=HP_EPSILON:
            self.events.append(dict(frame=frame,kind="arts_hit_skipped",reason="dead"))
            return None
        attack = _raw(attack_q32)
        self._source_calculate_damage(card)
        hit = self._arts_hit(attack,effective_arts_resistance_q32,ONE,resistance_scale_q32)
        self.hp = hit.hp_after_raw
        row=dict(frame=frame,kind="arts_hp",damage_q32=hit.damage_raw,hp_after_q32=self.hp)
        if source is not None:
            row["source"]=source
        self.events.append(row)
        self.apply_module_arts(frame,card,hit.damage_raw)
        return hit

    def _begin(self, frame, element):
        self.burst = element
        self.gauges = dict.fromkeys(self.gauges,0)
        self.duration = _raw_f32(self.rules[element]["enemyElementBreakDuration"])
        self.elapsed = self.ticks = 0
        key = "ep_break_"+element.lower()+"_enemy"
        buff = self.buffs[key]
        self.board = {r["key"]:_raw_f32(r["value"]) for r in buff["blackboard"]}
        self.events.append(dict(frame=frame,kind="burst_start",element=element,duration_q32=self.duration))
        if element=="DARK":
            impact, = [n["_buff"] for n in self.templates[key]["eventToActions"]["ON_BUFF_START"]
                       if n.get("_buff",{}).get("templateKey")=="periodic_element_damage"]
            if impact["waitFirstTriggerInterval"] or impact["firstTriggerInterval"]!=0:
                raise ValueError("凋亡首跳参数已变化")
            self.atk_ratio = self.board["atk_down"]
            self.period = self.weaken_due = self.board["interval"]
            self.tick_due = 0
        elif element=="ANGER":
            self.attack_speed_add = self.board["attack_speed"]
            self.period = self.tick_due = _raw_f32(buff["triggerInterval"])
        else:
            if element in ("WATER","FIRE"):
                mod, = buff["attributes"]["attributeModifiers"]
                if element=="WATER":
                    self.def_add += _raw_f32(mod["value"])
                else:
                    self.res_add = _raw_f32(mod["value"])
            elif element=="SANITY" and not self.palsy_immune:
                self.events.append(dict(frame=frame,kind="palsy_stack_request",count=self.board["palsy_stack_cnt"]//ONE))
                self.add_palsy(frame,self.board["palsy_stack_cnt"]//ONE,"SANITY")
            self.apply_elemental_hp(frame,self.board["ep_"+element.lower()+"_damage"],source=element)

    def tick(self, frame):
        self._at(frame)
        if self.last_tick is not None and frame!=self.last_tick+1:
            raise ValueError("请逐帧调用元素受击者更新，不能跳过或重复")
        self.last_tick = frame
        if self.hp>HP_EPSILON:
            for source, (scale, remaining) in tuple(self.gallus_marks.items()):
                remaining -= self.step
                if remaining <= 0:
                    del self.gallus_marks[source]
                    self.events.append(dict(kind="gallus_mark_expired",frame=frame,source=source))
                else:
                    self.gallus_marks[source] = scale, remaining
            for owner,(delta,remaining) in tuple(self.scorch_defense.items()):
                remaining-=self.step
                if remaining<=0:
                    del self.scorch_defense[owner]
                    self.events.append(dict(kind="scorch_defense_expired",frame=frame,owner=owner))
                else:
                    self.scorch_defense[owner]=(delta,remaining)
            for source,aura in self.qanik_auras.items():
                previous=aura.weak
                aura.tick(self.step,self.airborne)
                if aura.weak != previous:
                    self.events.append(dict(kind="qanik_fragility_applied" if aura.weak else "qanik_fragility_removed",
                                            frame=frame,source=source,reason="receiver_poll"))
            for owner,(delta,remaining) in tuple(self.scorched_earth.items()):
                remaining-=self.step
                if remaining<=0:
                    del self.scorched_earth[owner]
                    self.events.append(dict(kind="scorched_earth_expired",frame=frame,owner=owner))
                else:
                    self.scorched_earth[owner]=delta,remaining
        if self.logos_curse is not None and self.hp>HP_EPSILON:
            self.logos_curse_remaining -= self.step
            if self.logos_curse_remaining <= 0:
                self.logos_curse = None
                self.logos_curse_remaining = 0
        if self.burst is None or self.hp<=HP_EPSILON:
            return
        self.elapsed += self.step
        if self.elapsed>=self.duration:
            # The outer buff precedes its derived buffs in the ordinary container.
            # Its expiration clears them. Entity subsequently ticks the EP controller.
            self.events.append(dict(frame=frame,kind="burst_end",element=self.burst))
            self.burst = None
            self.gauges = dict.fromkeys(self.gauges,self.max_ep)
            self.res_add = self.atk_ratio = self.attack_speed_add = 0
            return
        if self.burst in ("DARK","ANGER"):
            self.tick_due -= self.step
            if self.tick_due<READY_EPSILON:
                self.tick_due += self.period
                self.ticks += 1
                damage = (self.board["damage"] if self.burst=="DARK" else
                          self.board["anger_damage_base"]+self.ticks*self.board["anger_damage_step"])
                self.apply_elemental_hp(frame,damage,source=self.burst)
            if self.burst=="DARK":
                self.weaken_due -= self.step
                if self.weaken_due<READY_EPSILON:
                    self.weaken_due += self.period
                    self.atk_ratio = min(0,self.atk_ratio+_divide(mul(-self.board["atk_down"],self.period),self.board["duration"]))
        recovered = mul(self.max_ep,_divide(self.elapsed,self.duration))
        self.gauges = dict.fromkeys(self.gauges,recovered)

    def snapshot(self):
        # Entity.get_minEpType starts at maxEP and replaces the candidate only
        # on strict less-than, in ascending enum order. Burst uses OnEpZero's
        # argument directly, independent of this selector-facing value.
        lowest = min(ELEMENTS_BY_ID,key=self.gauges.__getitem__)
        snapshot = dict(hp_q32=self.hp,max_ep_q32=self.max_ep,remaining_ep_q32=dict(self.gauges),
                    min_ep_type=lowest if self.gauges[lowest]<self.max_ep else None,
                    burst=self.burst,def_add_q32=self.def_add,arts_resistance_add_q32=self.res_add,
                    atk_ratio_q32=self.atk_ratio,attack_speed_add_q32=self.attack_speed_add)
        if self.logos_curse is not None:
            snapshot["arts_resistance_add_q32"] += self.logos_curse.resistance_add
            snapshot["logos_curse"] = dict(remaining_q32=self.logos_curse_remaining,
                                         arts_attack_add_q32=self.logos_curse.attack_add,
                                         resistance_add_q32=self.logos_curse.resistance_add)
        if self.palsy_stacks:
            snapshot["palsy_stacks"]=self.palsy_stacks
        if self.scorched_earth:
            snapshot["scorched_earth"]={k:dict(resistance_add_q32=delta,remaining_q32=life)
                                        for k,(delta,life) in self.scorched_earth.items()}
            snapshot["arts_resistance_add_q32"]+=sum(v[0] for v in self.scorched_earth.values())
        if self.scorch_defense:
            snapshot["def_add_q32"]+=sum(delta for delta,_ in self.scorch_defense.values())
        return snapshot


def run_enemy_elements(tables,card,event):
    """Dispatch hits to one enemy in native object-tick order or an explicit callback trace."""
    enemy = dict(event["enemy"])
    base_resistance = enemy.pop("base_arts_resistance_q32",None)
    if base_resistance is not None and type(base_resistance) is not int:
        raise ValueError("敌人基础法抗需要 Q32.32 整数")
    state = EnemyElements(tables,**enemy)
    state.airborne=event.get("receiver_airborne",False)
    if type(state.airborne) is not bool:
        raise ValueError("敌人初始空地状态须为布尔值")
    start,end = event["frame"],event["end_frame"]
    if type(start) is not int or type(end) is not int or not 0<=start<=end:
        raise ValueError("需要有效的起止帧")
    loadouts = {card["operator_id"]:card}
    for name,pick in event.get("source_loadouts",{}).items():
        if not isinstance(name,str) or not name or not isinstance(pick,dict):
            raise ValueError("每个元素损伤来源都需要非空名称和装载配置")
        if "operator_id" in pick:
            pick = dict(operator=pick["operator_id"],**{k:pick[k] for k in ("skill","module","skill_level","module_level")})
        source_card = resolve_caster_loadout(tables,**pick)
        if name in loadouts and loadouts[name]!=source_card:
            raise ValueError("元素损伤来源名称重复绑定到不同装载")
        loadouts[name] = source_card
    actions = {}
    nymph = None
    if "nymph" in event:
        from .nymph_soul import NymphReceiverSource
        nymph = NymphReceiverSource(tables,card,event["nymph"],start)
    ifrit_rules = {}
    scorch_rules = {}
    flame = None
    flame_owner_on_field = False
    flights = []

    def source_attack(source_card,hit,scale):
        nonlocal flame
        from .eyja_aura import EYJA,FlameBreath
        if flame is None:
            owner=next((loadout for loadout in loadouts.values() if loadout["operator_id"]==EYJA),None)
            flame=FlameBreath.from_card(tables,owner) if owner else FlameBreath(False,0)
        return flame.source_attack(tables,source_card,owner_on_field=flame_owner_on_field,
                                   other_atk_ratio=hit.get("other_atk_ratio_q32",0),scale=scale)
    native_order = "receiver_object_uid" in event
    receiver_uid = event.get("receiver_object_uid")
    if native_order and (type(receiver_uid) is not int or receiver_uid<=0):
        raise ValueError("敌人战斗实例号需要正整数")
    if not isinstance(event["hits"],(list,tuple)):
        raise ValueError("同帧损伤必须提供有序命中列表")
    for index,hit in enumerate(event["hits"]):
        phase = hit.get("phase")
        if native_order and phase=="coroutines":
            if "tick_owner_uid" in hit:
                raise ValueError("协程回调应按调度器顺序提供，不使用普通对象更新序号")
        elif native_order:
            owner = hit.get("tick_owner_uid")
            if type(owner) is not int or owner<=0 or owner==receiver_uid:
                raise ValueError("需提供实际运行本次命中回调、且与敌人不同的战斗实例号")
            derived = "before_receiver" if owner<receiver_uid else "after_receiver"
            if phase is not None and phase!=derived:
                raise ValueError("命中相位与战斗对象更新顺序冲突")
            phase = derived
        elif "tick_owner_uid" in hit or phase not in ("before_receiver","after_receiver","coroutines"):
            raise ValueError("需要敌人战斗实例号或明确的敌人更新相位")
        name = hit.get("source",None if event.get("source_loadouts") else card["operator_id"])
        if name not in loadouts:
            raise ValueError(f"元素损伤包 {index} 的来源未绑定 {name!r}")
        if "flight" in hit:
            if ("frame" in hit or hit["kind"]!="eben_delta_projectile" or phase=="coroutines"
                    or loadouts[name]["operator_id"]!="char_4046_ebnhlz"):
                raise ValueError("黑键飞行输入以普通对象更新计算到达，不需另填命中帧")
            from .mystic_damage import delta_projectile_flight
            trajectory=delta_projectile_flight(tables,loadouts[name],hit,end)
            flights.append(dict(packet_index=index,source=name,**trajectory))
            if trajectory["reach_frame"] is None:
                continue
            hit=dict(hit,frame=trajectory["reach_frame"])
        if type(hit["frame"]) is not int or not start<=hit["frame"]<=end:
            raise ValueError("元素受击帧须在回放区间内")
        actions.setdefault((hit["frame"],phase),[]).append((index,name,hit))
    if native_order:
        # A single object's synchronous callbacks retain their original action order.
        for (_,phase),group in actions.items():
            if phase!="coroutines":
                group.sort(key=lambda entry:entry[2]["tick_owner_uid"])
    for frame in range(start,end+1):
        for phase in ("before_receiver","after_receiver","coroutines"):
            if phase=="after_receiver":
                state.tick(frame)
                if nymph:
                    nymph.soul.tick(frame,state,nymph.attack,nymph.burst_factor)
                    nymph.sync_recovery(state,frame)
            for index,name,hit in actions.get((frame,phase),()):
                source_card = loadouts[name]
                first_event = len(state.events)
                kind = hit["kind"]
                previous_burst = state.burst
                if kind in ("module_arts","module_arts_hit","ifrit_delta_hit","ifrit_s3_pulse") and not source_card["module"]:
                    raise ValueError("模组法术伤害来源需要装备所选常规模组")
                if (kind in ("nymph_aura_contact","nymph_s3_mode","nymph_withdraw","nymph_redeploy",
                             "nymph_s2_hit","nymph_s3_hit","nymph_basic_hit","nymph_y_contact","nymph_sp_block","nymph_sp_tick")
                        and nymph is not None and name==card["operator_id"]):
                    nymph.handle(state,frame,hit,base_resistance)
                elif kind in ("eyja_aura_attach","eyja_aura_detach"):
                    from .eyja_aura import EYJA, FlameBreath
                    if source_card["operator_id"] != EYJA:
                        raise ValueError("炎息的在场变更需要艾雅法拉的实例")
                    flame = FlameBreath.from_card(tables,source_card)
                    flame_owner_on_field = kind == "eyja_aura_attach"
                elif kind in ("qanik_aura_contact","qanik_aura_retry"):
                    state.qanik_event(tables,source_card,name,frame,hit)
                elif kind=="receiver_motion":
                    state.airborne=hit["airborne"]
                    if type(state.airborne) is not bool:
                        raise ValueError("敌人变更后的空地状态须为布尔值")
                elif kind=="buildup":
                    state.apply_buildup(frame,hit["element"],hit["amount_q32"])
                elif kind=="module_arts":
                    state.apply_module_arts(frame,source_card,hit["modifier_value_q32"])
                elif kind=="ifrit_s2_scorch_defense" and source_card["operator_id"]=="char_134_ifrit" and source_card["skill"]==2:
                    from .caster_prefabs import caster_prefab_evidence
                    from .ifrit_scorch import scorch_definition,scorch_defense_rule
                    if name not in scorch_rules:
                        spec=caster_prefab_evidence(tables,source_card["operator_id"],2,skill_level=source_card["skill_level"],
                            module_id=source_card["module"],module_level=source_card["module_level"])
                        scorch_rules[name]=scorch_defense_rule(*scorch_definition(spec))
                    state.apply_scorch_defense(frame,name,scorch_rules[name])
                elif kind=="ordinary_physical_hit":
                    state.receive_ordinary(frame,source=name,source_card=source_card,damage_type="physical",
                        attack_q32=hit["attack_q32"],mitigation_q32=hit["base_defense_q32"])
                elif kind in ("ordinary_arts_hit","module_arts_hit"):
                    resistance = hit.get("effective_arts_resistance_q32")
                    if resistance is None:
                        if base_resistance is None:
                            raise ValueError("法伤命中需要敌人基础法抗或本包有效法抗")
                        resistance = max(0,base_resistance+state.res_add)
                    resistance_scale = hit.get("resistance_scale_q32",ONE)
                    if "source_atk_scale_q32" in hit:
                        if "attack_q32" in hit:
                            raise ValueError("攻击力读取和直接伤害包不能同时指定")
                        attack=source_attack(source_card,hit,hit["source_atk_scale_q32"])
                    else:
                        attack = hit["attack_q32"]
                    icon = (source_card["module_info"] or {}).get("typeIcon")
                    if kind=="module_arts_hit" or icon in ("msc-d","ccr-d","bla-d"):
                        state.hit_with_module_arts(frame,source_card,attack_q32=attack,
                            effective_arts_resistance_q32=resistance,resistance_scale_q32=resistance_scale)
                    else:
                        state.receive_ordinary(frame,source=name,source_card=source_card,damage_type="arts",
                            attack_q32=attack,mitigation_q32=resistance,resistance_scale_q32=resistance_scale)
                elif kind=="ifrit_s2_burn_tick" and source_card["operator_id"]=="char_134_ifrit" and source_card["skill"]==2:
                    from .caster_equipment_traits import blast_distance_factor
                    from .caster_prefabs import caster_prefab_evidence
                    from .ifrit_scorch import scorch_definition
                    source_present=hit.get("source_present",True)
                    on_field=hit.get("owner_on_field",source_present)
                    if type(source_present) is not bool or type(on_field) is not bool or on_field and not source_present:
                        raise ValueError("灼伤需要一致的来源引用和在场状态")
                    if not source_present:
                        state.events.append(dict(kind="scorch_burn_skipped",frame=frame,reason="source_reference_invalid",
                                                 packet_source=name,packet_index=index))
                        continue
                    is_delta=(source_card["module_info"] or {}).get("typeIcon")=="bla-d"
                    if is_delta and not on_field:
                        raise ValueError("伊芙利特撤退后 Δ 模组的输出附着状态仍待核实")
                    cache_key=(name,"burn_tick")
                    if cache_key not in ifrit_rules:
                        spec=caster_prefab_evidence(tables,source_card["operator_id"],2,skill_level=source_card["skill_level"],
                            module_id=source_card["module"],module_level=source_card["module_level"])
                        _,board=scorch_definition(spec)
                        ratio=spec["templates"]["burn"]["eventToActions"]["ON_BUFF_TRIGGER"][0]["_atkScaleVar"]
                        ifrit_rules[cache_key]=_raw_f32(board[ratio])
                    resistance=hit.get("arts_resistance_before_fire_q32",base_resistance)
                    if type(resistance) is not int:
                        raise ValueError("灼伤需要敌人基础法抗或本包灼燃前的法抗")
                    attack=source_attack(source_card,hit,ifrit_rules[cache_key])
                    factor=hit.get("resistance_scale_q32",ONE)
                    trait=_trait_values(source_card)
                    if is_delta:
                        state.hit_with_module_arts(frame,source_card,attack_q32=attack,
                            effective_arts_resistance_q32=resistance+state.res_add,resistance_scale_q32=factor,source=name)
                    else:
                        distance=blast_distance_factor(trait,hit["distance"]) if on_field and "max_dist" in trait else ONE
                        state.receive_ordinary(frame,source=name,source_card=source_card,damage_type="arts",attack_q32=attack,
                            mitigation_q32=resistance+state.res_add,output_scale_q32=distance,resistance_scale_q32=factor)
                elif kind in ("ifrit_s3_pulse","ifrit_delta_hit") and source_card["operator_id"]=="char_134_ifrit":
                    from .caster_prefabs import caster_prefab_evidence
                    from .ifrit_delta import ifrit_delta_hit,ifrit_delta_rules,ifrit_x_pulse,scorched_earth_debuff
                    from .caster_equipment_traits import blast_distance_factor
                    is_delta=source_card["module_info"]["typeIcon"]=="bla-d"
                    if ((kind=="ifrit_s3_pulse" and source_card["skill"]!=3) or
                            (kind=="ifrit_delta_hit" and not is_delta)):
                        raise ValueError("灼地脉冲需要三技能，Δ 命中需要相应模组")
                    if name not in ifrit_rules:
                        spec=caster_prefab_evidence(tables,source_card["operator_id"],source_card["skill"],
                            skill_level=source_card["skill_level"],module_id=source_card["module"],module_level=source_card["module_level"])
                        ifrit_rules[name]=ifrit_delta_rules(source_card,spec) if is_delta else scorched_earth_debuff(spec)
                    resistance=hit.get("arts_resistance_before_fire_q32",base_resistance)
                    if type(resistance) is not int:
                        raise ValueError("灼地需要敌人基础法抗或该包已观测的灼燃前法抗")
                    factor=hit.get("resistance_scale_q32",ONE)
                    if is_delta:
                        ability="channel_hit" if kind=="ifrit_s3_pulse" else hit["ability"]
                        ifrit_delta_hit(state,source_card,ifrit_rules[name],frame,ability,hit["actual_attack_q32"],
                                        resistance,resistance_scale_q32=factor,owner=name)
                    else:
                        output=blast_distance_factor(_trait_values(source_card),hit["distance"])
                        ifrit_x_pulse(state,source_card,ifrit_rules[name],frame,name,hit["actual_attack_q32"],
                                      resistance,output,factor)
                elif kind=="eben_delta_projectile" and source_card["operator_id"]=="char_4046_ebnhlz":
                    from .mystic_damage import delta_projectile_impact
                    resistance=hit.get("arts_resistance_before_fire_q32",base_resistance)
                    if type(resistance) is not int:
                        raise ValueError("黑键命中需要敌人基础法抗或本包灼燃修正前的法抗")
                    delta_projectile_impact(tables,source_card,state,frame,hit,resistance)
                elif kind=="elemental_hp":
                    state.apply_elemental_hp(frame,hit["amount_q32"],source_card=source_card)
                elif kind=="nymph_skill_arts" and source_card["operator_id"]=="char_4146_nymph" and source_card["skill"] in (1,2):
                    board = {b["key"]:b["value"] for b in source_card["skill_row"]["blackboard"]}
                    if source_card["skill"]==1:
                        amount = _raw(hit["modifier_value_q32"])
                        ratio = board["attack@ep_damage_ratio"]
                    else:
                        amount = abs(_raw(hit["hp_before_arts_q32"])-_raw(hit["hp_after_arts_q32"]))
                        ratio = board["ep_damage_ratio"]
                    state.apply_buildup(frame,"DARK",mul(amount,_raw_f32(ratio)))
                else:
                    raise ValueError("该配置没有此项受击事件")
                if nymph and previous_burst != "DARK" and state.burst == "DARK":
                    nymph.on_dark_start()
                if nymph:
                    nymph.sync_recovery(state,frame)
                for row in state.events[first_event:]:
                    row.update(packet_source=name,packet_index=index)
    result=dict(events=state.events,receiver=state.snapshot(),sources=tables.source,
                conditions=["ordinary_initialized_enemy","no_other_hp_or_element_modifiers",
                            "normal_break_recovery_speed","native_object_tick_order" if native_order
                            else "explicit_hit_and_target_update_order"])
    if flights:
        result["projectile_trajectories"]=flights
    if nymph:
        result["nymph_stacks"]=nymph.stacks
        if nymph.recovery is not None:
            result["nymph_recovery"]=dict(**nymph.recovery.gauge.state.snapshot(),events=nymph.recovery.events,
                                         on_field=nymph.soul.source_present)
    return result
