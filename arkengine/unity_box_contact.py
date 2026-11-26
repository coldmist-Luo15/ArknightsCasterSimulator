"""Exact V079 Box2D sensor narrow phase for borderline ordinary range contacts."""
import hashlib
from functools import lru_cache
from math import hypot
from struct import pack
from threading import Lock
from zipfile import ZipFile

from .attack_range import BOX_QUERY_RADIUS,OVERLAP_DISTANCE_TOLERANCE


UNITY_SHA256="67a42a4222dd35284d77e3b0986e6153a8e893dbc0413d96432b1bca0d80aa30"


def range_boxes(data):
    """V079 RangeData starts row-major and grows each rectangle right, then up."""
    cells={(r["row"],r["col"]) for r in data["grids"]}
    remaining=cells.copy()
    boxes=[]
    for row,col in sorted(cells):
        if (row,col) not in remaining:
            continue
        top,right=row,col
        while (row,right+1) in cells:
            right+=1
        while all((top+1,x) in cells for x in range(col,right+1)):
            top+=1
        remaining.difference_update((y,x) for y in range(row,top+1) for x in range(col,right+1))
        boxes.append(((row+top)*.5,(col+right)*.5,top-row+1,right-col+1))
    return tuple(boxes)


def centered_range_boxes(data,placement):
    boxes=range_boxes(data)
    shapes={(height,width) for _,_,height,width in boxes}
    if any(row or col for row,col,_,_ in boxes) or shapes!={(width,height) for height,width in shapes}:
        raise ValueError("只支持 V079 林和卡涅利安的居中对称光环碰撞体")
    return tuple((placement["row"],placement["col"],height,width) for _,_,height,width in boxes)


class V079BoxOverlap:
    def __init__(self,image):
        from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE,arm64_const as reg

        self.cpu=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
        self.reg=reg
        self.lock=Lock()
        self.shapes={}
        self.cpu.mem_map(0x200000,0x100000)
        self.cpu.mem_map(0x100000,0x1000)
        self.cpu.mem_map(0x139e000,0x1000)
        for start,size,offset,mask in (
            (0x568000,0x1000,0x30000,0x18),(0x56b000,0x1000,0x30000,0x18),
            (0x588000,0x4000,-0x30000,0x18),(0x58c000,0x2000,0,0),
            (0xfd7000,0x1000,0x30000,0x18),(0xf24000,0x1000,0,0),(0xf6c000,0x1000,0,0),
            (0x1300000,0x2000,-0x1000,0),(0x1309000,0x1000,-0x1000,0),(0x12d4000,0x1000,-0x1000,0),
        ):
            source=image[start+offset:start+offset+size]
            self.cpu.mem_map(start,size)
            self.cpu.mem_write(start,bytes(n^mask for n in source) if mask else source)
        self.cpu.mem_map(0xb6000,0x1000)
        self.cpu.hook_add(UC_HOOK_CODE,self._axis_sincos,begin=0xb6770,end=0xb6770)

    def _axis_sincos(self,cpu,_pc,_size,_context):
        reg=self.reg
        if cpu.reg_read(reg.UC_ARM64_REG_S0):
            raise ValueError("范围盒构造器要求标准轴向")
        cpu.mem_write(cpu.reg_read(reg.UC_ARM64_REG_X0),pack("<f",0))
        cpu.mem_write(cpu.reg_read(reg.UC_ARM64_REG_X1),pack("<f",1))
        cpu.reg_write(reg.UC_ARM64_REG_PC,cpu.reg_read(reg.UC_ARM64_REG_LR))

    def _call(self,address,*args):
        cpu,reg=self.cpu,self.reg
        cpu.reg_write(reg.UC_ARM64_REG_SP,0x2ff000)
        cpu.reg_write(reg.UC_ARM64_REG_LR,0x100000)
        for index,value in enumerate(args):
            cpu.reg_write(getattr(reg,f"UC_ARM64_REG_X{index}"),value)
        cpu.emu_start(address,0x100000,count=30000)
        if cpu.reg_read(reg.UC_ARM64_REG_PC)!=0x100000:
            raise ValueError("V079 碰撞例程未正常返回")
        return cpu.reg_read(reg.UC_ARM64_REG_X0)

    def touches(self,pos,radius,rect):
        row,col,height,width=rect
        with self.lock:
            shape=self.shapes.get((height,width))
            if shape is None:
                shape=0x210000+len(self.shapes)*0x200
                self.cpu.mem_write(0x221000,pack("<2f",0,0))
                self.cpu.mem_write(0x222000,pack("<2f",width,height))
                self.cpu.reg_write(self.reg.UC_ARM64_REG_S0,0)
                self._call(0x568074,shape,0,0x221000,0x222000)
                self._call(0x56bad8,shape,0x225000)
                self.shapes[height,width]=shape
            self.cpu.mem_write(0x223000,pack("<4f",col,row,0,1))
            self.cpu.mem_write(0x224000,pack("<4f",pos[1],pos[0],0,1))
            self.cpu.mem_write(0x220008,pack("<If2f",0,radius,0,0))
            return bool(self._call(0x588c88,shape+0x70,0,0x220000,0,0x223000,0x224000))


@lru_cache(maxsize=1)
def exact_overlap(package):
    try:
        import unicorn
    except ImportError:
        return None
    if not package.exists():
        return None
    with ZipFile(package) as archive:
        image=archive.read("lib/arm64-v8a/libunity.so")
    if hashlib.sha256(image).hexdigest()!=UNITY_SHA256:
        raise ValueError("接触计算需使用锁定 V079 Unity 玩家库")
    return V079BoxOverlap(image)


def range_sensor_contact(tables,pos,radius,boxes):
    limit=radius+BOX_QUERY_RADIUS+OVERLAP_DISTANCE_TOLERANCE
    near=[]
    for box in boxes:
        row,col,height,width=box
        distance=hypot(max(abs(pos[0]-row)-height*.5,0),max(abs(pos[1]-col)-width*.5,0))
        if distance<limit-1e-5:
            return True
        if distance<=limit+1e-5:
            near.append(box)
    if not near:
        return False
    package=tables.root.parent/"2026-11-18-core-cn2791/client.apk"
    exact=exact_overlap(package)
    if exact is None:
        return None
    tables.source["unity_box_sensor"]=dict(member="lib/arm64-v8a/libunity.so",sha256=UNITY_SHA256)
    return any(exact.touches(pos,radius,box) for box in near)
