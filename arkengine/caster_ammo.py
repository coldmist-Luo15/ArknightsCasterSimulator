"""V079 counters for the selected caster's currently active ammunition skill."""


class CasterAmmoCounter:
    """Consume accepted main-ability callbacks in their actual order, including owner updates."""

    def __init__(self,evidence,*,stored_on_attach=0):
        control,=(c["fields"] for c in evidence["skill"]["components"] if not c["path"] and c["script"]=="CastSkillWithLimitTimes")
        parts=[c for c in evidence["character"]["components"] if c["active"] and c["enabled"] and
               c["script"] in ("AbilityEventCounter","MultiChargeAbilityEventCounter") and c["path"].endswith("/Body/Attack")]
        if not parts or not control["_fetchFromMainAttack"] or not control["_finishSkillWithProgress"] or control["_useExtraModeProgress"]:
            raise ValueError("当前技能使用不同的子弹进度来源")
        main=parts[0]
        f=main["fields"]
        if (any(c["script"]!=main["script"] or c["fields"]!=f for c in parts) or f["_useAnotherBBKey"] or
                not f["_resetAfterEnd"] or any(f[k] for k in ("_resetImmediatelyWhenProgressEnd","_resetWhenAttackFinished",
                "_ignoreTriggerOnce","_overrideRecoverCountLimit","_forceAbilityWaitForPeriodWhenFinalCastEnd","_persistRemainingCountToSharedBlackboard"))):
            raise ValueError("当前所选进度计数器包含未覆盖的重置、回弹或继承选项")
        self.skill=evidence["skill_id"]
        self.multi=main["script"]=="MultiChargeAbilityEventCounter"
        self.count_event={4:"spell_on",5:"spell_end"}[f["_countEvent"]]
        self.per=f["_expendPerTrigger"]
        limit,=(v["value"] for v in evidence["skill_blackboard"] if v["key"]=="attack@trigger_time")
        if (int(limit)!=limit or limit<=0 or self.per<=0 or type(stored_on_attach) is not int or stored_on_attach<0 or
                (not self.multi and stored_on_attach)):
            raise ValueError("初始弹药、蓄能层数或每次开销无效")
        self.maximum=self.remaining=int(limit)
        self.limited_recoveries=0
        self.reserved=min(self.maximum,stored_on_attach*self.per)
        self.counted=False
        self.ready_to_reset=False
        self.discarded=False
        self.stop_frame=None
        self.frame=-1
        self.events=[]

    def _at(self,frame):
        if type(frame) is not int or frame<0 or frame<self.frame:
            raise ValueError("限次能力回调需要单调不减的逻辑帧")
        self.frame=frame

    def ability_event(self,frame,kind):
        self._at(frame)
        if kind not in ("cast_end","detached","spell_on","spell_end","charge_spell") or (kind=="charge_spell" and not self.multi):
            raise ValueError("需要所选主攻击能力的实际施法或蓄能事件")
        if self.stop_frame is not None:
            return
        if kind in ("cast_end","detached"):
            self.counted=False
            if not self.remaining:
                self.ready_to_reset=True
                self.events.append(dict(kind="ammo_ready_to_reset",frame=frame,cause=kind))
        elif kind=="charge_spell":
            if not self.discarded:
                self.reserved=min(self.remaining,self.reserved+self.per)
                self.events.append(dict(kind="reserve_charge_ammo",frame=frame,reserved=self.reserved,remaining=self.remaining))
        elif kind==self.count_event and not self.counted and not self.discarded:
            spent=self.reserved if self.multi else self.per
            self.remaining=max(0,self.remaining-spent)
            self.reserved=0
            self.counted=True
            self.events.append(dict(kind="ammo_counted",frame=frame,event=kind,spent=spent,remaining=self.remaining))

    def recover(self,frame,amount,*,skip_limit_check=False):
        self._at(frame)
        if type(amount) is not int or amount<0 or type(skip_limit_check) is not bool:
            raise ValueError("回弹请求需要非负整数数量及有效的累计限额开关")
        if self.stop_frame is not None or self.ready_to_reset:
            return 0
        actual=min(amount,self.maximum-self.remaining)
        if not skip_limit_check:
            actual=min(actual,self.maximum-self.limited_recoveries)
            self.limited_recoveries+=actual
        self.remaining+=actual
        self.events.append(dict(kind="ammo_recovered",frame=frame,requested=amount,actual=actual,remaining=self.remaining))
        return actual

    def discard(self,frame):
        """The owner's IsDiscardable and delivery gates already accepted the player's input."""
        self._at(frame)
        if self.stop_frame is None and not self.discarded:
            self.remaining=self.reserved=0
            self.discarded=self.ready_to_reset=True
            self.events.append(dict(kind="ammo_discarded",frame=frame))

    def owner_tick(self,frame):
        self._at(frame)
        if self.stop_frame is not None or self.remaining or not self.ready_to_reset:
            return False
        self.stop_frame=frame
        self.events.append(dict(kind="stop_primary_ability_and_reset_cooldown",frame=frame))
        return True

    def snapshot(self):
        return dict(skill_id=self.skill,maximum=self.maximum,remaining=self.remaining,reserved=self.reserved,
                    count_event=self.count_event,ready_to_reset=self.ready_to_reset,stop_primary_frame=self.stop_frame,
                    through_frame=self.frame,events=list(self.events))


def run_ammo_counter(evidence,event):
    counter=CasterAmmoCounter(evidence,stored_on_attach=event.get("stored_on_attach",0))
    counter._at(event["frame"])
    for row in event["callbacks"]:
        frame,kind=row["frame"],row["kind"]
        if kind=="owner_tick":
            counter.owner_tick(frame)
        elif kind=="recover":
            counter.recover(frame,row["amount"],skip_limit_check=row.get("skip_limit_check",False))
        elif kind=="discard":
            counter.discard(frame)
        else:
            counter.ability_event(frame,kind)
    return counter.snapshot()
