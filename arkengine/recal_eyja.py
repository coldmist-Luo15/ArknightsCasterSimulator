"""Eyjafjalla S3 on the shared holographic-stage clock, including ordinary homing shots."""
from dataclasses import dataclass
from math import hypot

from .baked_mount import single
from .caster_equipment_traits import _trait_values
from .caster_model import _assets
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .core_caster_skills import eyja_deployment, volcano_selection
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .eyja_volcano import _origins
from .marcil_deploy import _birth
from .mostima_aura import set_period
from .mystic_timing import fixed_wait_frames
from .attack_range import range_cells
from .fixed import ONE
from .skill_recovery import deploy_skill, _divide
from .trace_motion import ordinary_trace_step, paracurve_horizontal_step
from .volley_damage import _raw_f32


@dataclass
class _Flight:
    volcano: bool
    target: object
    position: tuple
    offset: tuple
    born: int
    cached_attack: int
    error: float = 0.
    first_possible: int | None = None


class EyjaCombat:
    """Call objects before coroutines on each tick; press is an after-tick input."""
    def __init__(self,battle,actor):
        self.b,self.actor,self.card=battle,actor,actor.card
        if self.card['operator_id']!='char_180_amgoat' or self.card['skill']!=3:
            raise ValueError('全息共享火山入口只接受艾雅法拉三技能')
        self.ev=_assets(battle.tables,self.card)
        self.bb={r['key']:r['value'] for r in self.ev['skill_blackboard']}
        self.host=dict(actor.place,tile_z=actor.place.get('tile_z',-.4))
        self.face='Back' if actor.direction=='UP' else 'Front'
        self.normal=default_attack(battle.tables,actor.uid)
        self.base_cells=range_cells(self.card['base_range'],self.host,battle.rows,battle.cols)
        self.skill_cells=range_cells(self.card['skill_range'],self.host,battle.rows,battle.cols)
        talent=eyja_deployment(battle.tables,self.card,dict(elite_or_boss_in_range=any(
            target.level in ('ELITE','BOSS') for target in battle.targets(actor,self.base_cells))),battle.rng)
        self.speed_bonus=talent['attack_speed']
        self.gauge,_=deploy_skill(battle.tables,self.card,actor.deployed_frame)
        self.recover_from=actor.deployed_frame+1
        sp=self.gauge.state
        sp.points=min(sp.maximum,sp.points+talent['sp'])
        trait=_trait_values(self.card)
        self.pen=_raw_f32(trait.get('magic_resist_penetrate_fixed',0))
        self.hit_sp=int(trait.get('sp',0))
        self.born_end=actor.deployed_frame+_birth(battle.tables,self.card,self.ev,self.face)['born_exit_update']
        self.default_sign={'LEFT':-1,'RIGHT':1}.get(actor.direction)
        if self.default_sign is None:
            self.default_sign=battle.tables._prefab_data['default_birth_skin',actor.uid]['default_horizontal_face_sign']
        self.sign=self.default_sign
        self.last_face=self.face
        self.poll=self.born_end+1
        self.mode=self.expiry=self.press_frame=None
        self.coroutines_search=False
        self.closed=False
        self.release=None
        self.locked=[]
        self.flights=[]
        self.shot_clock=None
        self.attack_cooldown=None
        self.noted=set()
        self.movers={}
        for volcano,index,script in ((False,0,'AdvancedMovement'),(True,1,'ParacurveMovement')):
            key=default_attack(battle.tables,actor.uid,index)['fields']['_projectileKey']
            speeds={p['fields']['_speed'] for prefab in self.ev['projectiles'][key].values()
                    for p in prefab['components'] if p['script']==script}
            self.movers[volcano],=speeds
        self._log('eyja_deployment_talent',sp_roll=talent['sp'],sp=sp.points,attack_speed=talent['attack_speed'])

    def _log(self,kind,**fields):
        self.b.log(kind,operator_id=self.actor.uid,**fields)

    def _active(self):
        return not self.closed and self.mode is not None and self.mode<=self.b.frame<=self.expiry

    def _interval(self,volcano):
        panel=self.card['panel_attributes']
        return single(single(panel['baseAttackTime']+(self.bb['base_attack_time'] if volcano else 0))*100/
                      (self.b.attack_speed(self.actor)+self.speed_bonus))

    def _enemy(self,t):
        def note(rule):
            if (rule,t.id) not in self.noted:
                self.noted.add((rule,t.id))
                self._log('rule_applied',rule=rule,target=t.id)
        return dict(id=t.id,prefab=t.prefab,row=t.row,col=t.col,tile_z=0,airborne=t.airborne,
                    receiver_mount_rule=note)

    def _search(self,volcano):
        f=self.b.frame
        rows=self.b.targets(self.actor,self.skill_cells if volcano else self.base_cells)
        if not rows:
            self.poll=f+3
            self.coroutines_search=False
            self.attack_cooldown=None
            if not volcano:
                self.last_face=self.face
            return
        interval=self._interval(volcano)
        period=fixed_wait_frames(single(interval/self.b.dt))
        if volcano:
            ordered=sorted(rows,key=lambda e:(e.birth,e.order))
            selection=volcano_selection([e.id for e in ordered],int(self.bb['attack@max_target']),self.b.rng)
            entities={e.id:e for e in ordered}
            self.locked=[entities[k] for k in selection['targets']]
            self.release=f+period
            self.poll=self.release+1
            self.coroutines_search=True
            self._log('volcano_attack',targets=selection['targets'],trigger=selection['trigger_target'],
                      eligible=[e.id for e in ordered],release_frame=self.release,interval_ticks=period)
        else:
            target=rows[0]
            dx,dy=target.col-self.actor.col,target.row-self.actor.row
            if abs(dx)>.2:
                self.sign=1 if dx>0 else -1
            if abs(dy)>abs(dx)+.25*hypot(dx,dy):
                self.last_face='Back' if dy>0 else 'Front'
            attack=ranged_shot_release(self.b.tables,self.ev,self.normal['fields'],attack_frame=f,
                                       attack_interval=interval,facing=self.last_face)
            self.release=attack['release_frame']
            self.shot_clock=attack['clock']
            self.locked=[target]
            self.poll=f+period
            self.coroutines_search=False
            self.attack_cooldown=(period,self.release)
            self._log('eyja_ordinary_attack',target=target.id,release_frame=self.release)

    def _launch(self,volcano):
        f=self.b.frame
        targets=[t for t in self.locked if t.alive]
        self.release=None
        self.locked=[]
        if not targets:
            self._search(volcano)
            return
        for t in targets:
            enemy=self._enemy(t)
            if volcano:
                place=self.host
                if self.actor.direction in ('LEFT','RIGHT'):
                    place=dict(place,direction='RIGHT' if self.sign==1 else 'LEFT')
                origin,error,mount=_origins(self.b.tables,self.card,self.ev,place,enemy,f-self.press_frame)
            else:
                origin,mount=stationary_cast_mounts(self.b.tables,self.actor.uid,self.shot_clock,
                    self.host,enemy,self.last_face,caster_face_sign=self.sign)
                error=0.
            offset=(mount[0]-t.col,mount[1]-t.row,mount[2])
            cached=self.b.attack(self.actor,self.bb['atk'] if volcano else 0)
            self.flights.append(_Flight(volcano,t,origin,offset,f,cached,error))
            self._log('volcano_projectile' if volcano else 'eyja_ordinary_projectile',target=t.id)

    def _advance(self,shot):
        t=shot.target
        if not t.alive:
            return False
        goal=(t.col+shot.offset[0],t.row+shot.offset[1],shot.offset[2])
        speed=self.movers[shot.volcano]
        if shot.volcano:
            before=hypot(goal[0]-shot.position[0],goal[1]-shot.position[1])
            shot.position,_=paracurve_horizontal_step(shot.position,goal,speed,self.b.dt,True)
            gap=hypot(goal[0]-shot.position[0],goal[1]-shot.position[1])
            shot.error=.0001 if before+shot.error<single(speed*self.b.dt)-1e-5 else shot.error+.0002
            if gap-shot.error<=.100001 and shot.first_possible is None:
                shot.first_possible=self.b.frame
            if gap+shot.error>=.099999:
                return True
            if shot.first_possible is not None and shot.first_possible!=self.b.frame:
                self._log('rule_applied',rule='latest_confirmed_volcano_arrival',target=t.id,
                    earliest=shot.first_possible,latest=self.b.frame)
        else:
            step=ordinary_trace_step(shot.position,goal,effective_dt=self.b.dt,round_position=True,speed=speed)
            shot.position=step.position
            if not step.reached:
                return True
        bonus=self.bb['atk'] if self._active() and self.mode<self.b.frame else 0
        attack=shot.cached_attack if self.closed else self.b.attack(self.actor,bonus)
        self.b.arts(self.actor,t,attack,pen_raw=self.pen,
                    kind='volcano_hit' if shot.volcano else 'eyja_ordinary_hit')
        if not self.closed and self.b.frame>=self.recover_from and t.level in ('ELITE','BOSS'):
            sp=self.gauge.state
            sp.points=min(sp.maximum,sp.points+self.hit_sp)
        return False

    def objects(self):
        f=self.b.frame
        if not self.closed:
            rate=self.b.sp_rate(self.actor)
            set_period(self.gauge.state,_divide(ONE,_raw_f32(rate)) if rate>0 else None,
                       stopped=f<self.recover_from)
            if self.expiry is not None and f==self.expiry+1:
                self.mode=self.expiry=None
                self.release=None
                self.locked=[]
                self.poll=f
                self.coroutines_search=False
                self._log('volcano_mode_ended')
            if f>=self.recover_from:
                self.gauge.state.advance(self.gauge.step)
            if self.mode is None and self.attack_cooldown and self.poll>=f:
                old,release=self.attack_cooldown
                period=fixed_wait_frames(single(self._interval(False)/self.b.dt))
                if old!=period:
                    remaining=self.poll-(f-1)
                    self.poll=max(release+1,f-1+(remaining*period+old-1)//old)
                    self.attack_cooldown=(period,release)
            if self.poll==f and not self.coroutines_search and (self.expiry is None or f<self.expiry):
                self._search(self._active())
        self.flights=[shot for shot in self.flights if shot.born>=f or self._advance(shot)]

    def coroutines(self):
        f=self.b.frame
        if self.closed or self.expiry is not None and f>=self.expiry:
            return
        if self.mode==f:
            self._log('volcano_mode')
        if f==self.poll and self.coroutines_search:
            self._search(True)
        if self.release==f:
            self._launch(self._active())

    def press(self):
        f=self.b.frame
        if self.closed or self.mode is not None or f<self.born_end:
            self._log('skill_button_rejected',reason='inactive_or_busy')
            return False
        cast=resolve_cast_start(self.b.tables,self.ev,frame=f,phase='after_tick',direction=self.actor.direction,
            facing=self.face,owner_state='attack' if self.release is not None else 'idle',sp=self.gauge.state.points)
        if cast['cast_frame'] is None or cast['spell_frame'] is None:
            self._log('skill_button_rejected',reason=cast['status'],sp=self.gauge.state.points)
            return False
        self.gauge.state.points=cast['sp_after']
        self.press_frame=f
        self.b.skill_started(self.actor,cast["cast_frame"])
        self.mode=cast['spell_frame']
        self.expiry=f+fixed_wait_frames(self.card['skill_row']['duration']/self.b.dt)
        self.recover_from=self.expiry+2
        self.release=None
        self.attack_cooldown=None
        self.locked=[]
        self.poll=self.mode+1
        self.coroutines_search=False
        self._log('skill_pressed',skill=3,mode_frame=self.mode,expire_frame=self.expiry)
        return True

    def close(self):
        self.closed=True
        self.release=self.poll=self.mode=self.expiry=None
        self.locked=[]
