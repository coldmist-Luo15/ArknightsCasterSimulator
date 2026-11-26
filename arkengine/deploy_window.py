"""Bounded local search, not full-map victory or a source of deploy-to-shot timing.

One successful ordinary Eyja placement, first Default projectile, fixed muzzle,
valid tracked mount, base CN 78.0 speed-10 mover. A normal stop attempts its hit
in the object pass with no additional delay. Target has not switched. A previously
yielded ordinary crossbow cast reads its living source in the coroutine pass.
No other damage/control/source changes. All times are game logic frames.
"""
from itertools import groupby
import math

from arkengine.spell_start import CROSSBOW_DEFAULT_ATTACK_FAMILY, EnemySpellSample, SpellGate, single_change_window
from arkengine.trace_motion import ordinary_trace_step

PROFILE = 'eyja_default_vs_basic_crossbow'
DT = 143165440 / 2**32


def frame(n):
    if type(n) is not int or not 0 <= n <= 1000000:
        raise ValueError('Expected a bounded nonnegative integer game frame')
    return n


def vector(v):
    if not isinstance(v,(list,tuple)) or len(v)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>10000 for x in v):
        raise ValueError('Expected a finite three-axis mount or muzzle position')
    return tuple(v)


def search_deployments(s):
    """Enumerate an explicitly legal contiguous commit range. Track positions are
    piecewise constant absolute samples; the last sample holds through the read.
    No extrapolation before the first sample. Unknown effect/lag is never success.
    """
    required={'profile','legal_commit_range','gate_frame','muzzle','target_steps','first_move_lags','on_hit'}
    if set(s)!=required or s['profile']!=PROFILE:
        raise ValueError('Unsupported scenario or profile')
    lo,hi=map(frame,s['legal_commit_range']); gate=frame(s['gate_frame']); origin=vector(s['muzzle'])
    if hi<lo or hi-lo>=300 or gate-lo>1800:
        raise ValueError('Bounds: 1–300 commit frames; at most 1800 frames to the read')
    lags=s['first_move_lags']
    if lags is not None:
        if type(lags) is not list or not 1<=len(lags)<=16:
            raise ValueError('Provide 1–16 distinct first-move lags or null')
        lags=[frame(x) for x in lags]
        if len(set(lags))!=len(lags):raise ValueError('Duplicate first-move lag')
    effect=s['on_hit']
    if effect not in (None,'lethal','nonlethal','cancelled'):
        raise ValueError('Unsupported hit result')
    trajectory={};last=-1
    for key,pos in s['target_steps']:
        f=frame(key)
        if f<=last:raise ValueError('Mount samples must be strictly ordered')
        trajectory[f]=vector(pos);last=f
    samples={}; pos=None
    for f in range(lo,gate+1):
        if f==lo:pos=next((trajectory[k] for k in reversed(trajectory) if k<=f),None)
        pos=trajectory.get(f,pos);samples[f]=pos

    # Different (commit,lag) pairs can share a first move and therefore share a trace.
    inferred={}
    def arrival(first):
        if first>gate:return None,False
        if first not in inferred:
            p=origin;result=(None,False)
            for f in range(first,gate+1):
                if samples[f] is None:
                    result=(None,True);break
                step=ordinary_trace_step(p,samples[f],effective_dt=DT,round_position=True)
                p=step.position
                if step.reached:
                    result=(f,False);break
            inferred[first]=result
        return inferred[first]

    def classify(commit,lag):
        if commit>gate:return 'gate_passes',None
        if effect in ('nonlethal','cancelled'):return 'gate_passes',None
        if lag is None:return 'unknown_lag',None
        reached,missing=arrival(commit+lag)
        if missing:return 'unknown_target_sample',None
        if reached is None:return 'gate_passes',None
        if effect is None:return 'unknown_hit_effect',reached
        before=EnemySpellSample(True,True,False,True,False,CROSSBOW_DEFAULT_ATTACK_FAMILY,False)
        permission=single_change_window(before,change_frame=reached,check_frame=gate,
                                        owner_alive=False,change_first=True if reached==gate else None)
        return ('blocks_gate' if permission is SpellGate.OWNER_DEAD else 'gate_passes'),reached

    records=[]
    for commit in range(lo,hi+1):
        outcomes=[]
        for lag in (lags or [None]):
            result,reached=classify(commit,lag)
            outcomes.append(dict(lag=lag,result=result,reach_by_gate=reached))
        labels={x['result'] for x in outcomes}
        if labels=={'blocks_gate'}: label='blocks_for_all_lags'
        elif labels=={'gate_passes'}: label='cannot_block'
        elif any(x.startswith('unknown') for x in labels):label='unresolved'
        else:label='depends_on_lag'
        records.append(dict(commit=commit,status=label,by_lag=outcomes))
    windows=[]
    for label,group in groupby(records,key=lambda r:r['status']):
        rows=list(group);windows.append(dict(status=label,first=rows[0]['commit'],last=rows[-1]['commit'],width_frames=len(rows)))
    return dict(profile=PROFILE,clock='30 logic frames/s',objective='block this permission read, not survive or clear a map',
                conditional_on='legal successful deployment; supplied lags and hit effect; fixed target identity; object pass hit before deterministic coroutine read',
                windows=windows,details=records,stage_clear=None)
