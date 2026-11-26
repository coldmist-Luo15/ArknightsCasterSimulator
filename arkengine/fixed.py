"""Raw signed Q32.32 primitives for the frozen client's Torappu.FP leaf functions.

Native correspondence: Common.dll tokens 0x060002fa, 0x060002fd, 0x06000301,
0x0600031a. See tools/verify_native_fixed.py for binary differential checks.
Float32 conversion corresponds to method 220521 at 0x950ffe8. Other FP
operations and the complete choice of FP versus float in battle are unresolved.
"""
FRACTION_BITS = 32
ONE = 1 << FRACTION_BITS
MASK64 = (1 << 64)-1
SIGN64 = 1 << 63


def signed64(value: int) -> int:
    value &= MASK64
    return value-(1 << 64) if value & SIGN64 else value


def from_int(value: int) -> int:
    return signed64(value << FRACTION_BITS)


def add(left: int, right: int) -> int:
    return signed64(left+right)


def sub(left: int, right: int) -> int:
    return signed64(left-right)


def mul(left: int, right: int) -> int:
    # Arithmetic shift floors negative products; do not use floating point or
    # truncation towards zero. The native arithmetic wraps to the low 64 bits.
    return signed64((signed64(left)*signed64(right)) >> FRACTION_BITS)


def from_float32_bits(bits: int) -> int:
    """Convert IEEE-754 binary32 bits, matching the frozen ARM64 leaf function.

    FCVTZS truncates toward zero and saturates signed overflow. The surrounding
    native code additionally returns INT64_MIN when float32(value * 2**32) is
    positive infinity. NaNs become zero. No host float rounding is involved.
    """
    if not isinstance(bits, int) or not 0 <= bits <= 0xFFFFFFFF:
        raise ValueError('Expected an unsigned 32-bit bit pattern')
    negative = bool(bits >> 31)
    exponent = (bits >> 23) & 0xFF
    fraction = bits & 0x7FFFFF
    if exponent == 0xFF:
        return 0 if fraction else -SIGN64
    if exponent == 0:
        return 0  # Every binary32 subnormal is smaller than one Q32.32 unit.
    if not negative and exponent >= 223:
        return -SIGN64  # Multiplication by 2**32 overflows binary32 to +inf.
    significand = (1 << 23) | fraction
    shift = exponent-118
    magnitude = significand << shift if shift >= 0 else significand >> -shift
    return max(-SIGN64, -magnitude) if negative else min(SIGN64-1, magnitude)
