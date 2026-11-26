"""Finite DEFAULT-policy token-card resources on the 30-Hz clock.

This is the card side of deployment. A world adapter must check placement and
create the unit; attack/born events, refunds and attribute modifiers are separate.
"""
from dataclasses import dataclass

from arkengine.frames import Phase


@dataclass(frozen=True)
class TokenCardRules:
    cost: int
    cooldown_frames: int
    stack_limit: int
    deployed_limit: int

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in vars(self).values()):
            raise ValueError('Resolved card rules must be nonnegative integers')


class TokenCard:
    def __init__(self, rules, *, stock, host_alive=False):
        if type(stock) is not int or not 0 <= stock <= rules.stack_limit:
            raise ValueError('Initial stock is outside the resolved limit')
        self.rules = rules
        self.stock = stock
        self.host_alive = host_alive
        self.remaining_frames = 0
        self.active = set()
        self.timer_stopped = False
        self.last_tick = 0

    def tick(self, loop):
        if loop.phase != Phase.DECK or loop.frame != self.last_tick + 1:
            raise RuntimeError('Card timers require every logic frame in the DECK phase')
        self.last_tick = loop.frame
        if self.remaining_frames and not self.timer_stopped:
            self.remaining_frames -= 1
            if self.remaining_frames == 0:
                loop.emit('token_card_cooldown_finished')

    def manual_deploy(self, loop, *, unit_id, available_cost, check_placement, spawn):
        """Return the remaining cost; a rejected attempt leaves resources intact.

        Both world callbacks must be explicit and return bool. A successful
        spawn is the commit point for this component's card/resource changes.
        The adapter owns native world-side event order and must not infer Born
        or follow-up creation from this card event.
        """
        if loop.phase != Phase.INPUT or self.last_tick != loop.frame:
            raise RuntimeError('Manual deployment requires INPUT after the card tick')
        if not callable(check_placement) or not callable(spawn):
            raise ValueError('Placement and spawn adapters are required')
        if unit_id in self.active:
            raise ValueError('Duplicate deployed unit id')
        reason = None
        if not self.host_alive:
            reason = 'host_not_alive'
        elif self.remaining_frames:
            reason = 'redeployment_cooldown'
        elif self.stock == 0:
            reason = 'no_stock'
        elif len(self.active) >= self.rules.deployed_limit:
            reason = 'deployed_limit'
        elif available_cost < self.rules.cost:
            reason = 'insufficient_cost'
        else:
            allowed = check_placement()
            if type(allowed) is not bool:
                raise TypeError('Placement adapter must return bool')
            if not allowed:
                reason = 'placement_rejected'
        if reason:
            loop.emit('token_deploy_rejected', unit=unit_id, reason=reason,
                      remaining_frames=self.remaining_frames)
            return available_cost
        succeeded = spawn()
        if type(succeeded) is not bool:
            raise TypeError('Spawn adapter must return bool')
        if not succeeded:
            loop.emit('token_deploy_rejected', unit=unit_id, reason='spawn_failed')
            return available_cost
        self.stock -= 1
        self.active.add(unit_id)
        self.remaining_frames = self.rules.cooldown_frames
        loop.emit('token_deployed', unit=unit_id, stock=self.stock,
                  remaining_frames=self.remaining_frames, cost=self.rules.cost)
        return available_cost - self.rules.cost

    def recycle(self, loop, unit_id):
        # DEFAULT policy: removing a token does not reset the card timer or
        # replenish stock. Any withdraw-cost recovery is an external event.
        self.active.remove(unit_id)
        loop.emit('token_recycled', unit=unit_id, stock=self.stock,
                  remaining_frames=self.remaining_frames)

    def recharge(self, loop, count, *, replace=False):
        if type(count) is not int or count < 0:
            raise ValueError('Recharge count must be a nonnegative integer')
        requested = count if replace else self.stock + count
        self.stock = min(self.rules.stack_limit, requested)
        overflow = requested - self.stock
        loop.emit('token_stock_changed', stock=self.stock, overflow=overflow,
                  remaining_frames=self.remaining_frames)
        # The caller must handle Wang S3 overflow through its ability system;
        # this return value is not an automatic placement or damage event.
        return overflow
