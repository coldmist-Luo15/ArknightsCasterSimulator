"""Seeded V079 BattleRandomWrapper's normal battle stream (Torappu.LegacyRandom)."""
from .baked_mount import single
from .fixed import ONE, mul
from math import isfinite


def _int32(value):
    return ((value+0x80000000)&0xffffffff)-0x80000000


class BattleRandom:
    """One shared stream per scene. Sampling extra effects advances subsequent rolls."""
    maximum=0x7fffffff

    def __init__(self,seed):
        if type(seed) is not int or not -(1<<31)<=seed<1<<31:
            raise ValueError("原版普通战斗种子需要 32 位有符号整数")
        self.values=[0]*56
        previous=_int32(161803398-_int32(abs(seed)))
        self.values[55]=previous
        current=1
        for i in range(1,55):
            self.values[(21*i)%55]=current
            previous,current=current,self._subtract(previous,current)
        for _ in range(4):
            for i in range(1,56):
                self.values[i]=self._subtract(self.values[i],self.values[1+(i+30)%55])
        self.first,self.second=0,31
        self.draws=0

    @classmethod
    def _subtract(cls,left,right):
        difference=_int32(left-right)
        return _int32(difference+cls.maximum) if difference<0 else difference

    def next_double(self):
        self.first=self.first%55+1
        self.second=self.second%55+1
        sample=self._subtract(self.values[self.first],self.values[self.second])
        self.values[self.first]=sample
        self.draws+=1
        return sample*(1./self.maximum)

    def value(self):
        return single(self.next_double())

    def advance_spawn_offsets(self,count):
        # Enemy.Init samples both route axes even when their configured spans are zero.
        for _ in range(2*count):
            self.next_double()

    def next_index(self,count):
        if type(count) is not int or not 0<count<=self.maximum:
            raise ValueError("普通候选数量需要正整数")
        return int(self.next_double()*count)

    def range_int(self,lower,upper):
        """LegacyRandom.Next(min,max) short-circuits spans of zero or one."""
        if (type(lower) is not int or type(upper) is not int or not -self.maximum-1<=lower<=upper<=self.maximum
                or upper-lower>self.maximum):
            raise ValueError("种子抽取的整数范围无效")
        return lower if upper-lower<2 else lower+self.next_index(upper-lower)

    def select_targets(self,candidates,count):
        """RandomSelector.OnPostFilter -> partial Shuffle and truncate the native-order list."""
        if type(count) is not int or count<0:
            raise ValueError("随机索敌数量需要非负整数")
        ordered=list(candidates)
        take=min(count,len(ordered))
        for index in range(take):
            other=self.range_int(index,len(ordered))
            ordered[index],ordered[other]=ordered[other],ordered[index]
        return ordered[:take]

    def range_fp(self,lower,upper):
        return lower+mul(upper-lower,int(self.value()*ONE))

    def dice(self,probability):
        if type(probability) not in (int,float) or not isfinite(probability):
            raise ValueError("概率必须是有限实数")
        probability=single(probability)
        epsilon=single(1e-5)
        return probability>epsilon and self.value()<=single(probability+epsilon)
