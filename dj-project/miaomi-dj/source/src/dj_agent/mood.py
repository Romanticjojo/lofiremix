"""Explicit editorial party preset, not a claim to observe a real crowd."""
import numpy as np


def party_target(index, count):
    p = index/max(count-1, 1)
    energy = float(np.interp(p, [0, .33, .46, .78, 1], [.38, .85, .45, .95, .48]))
    stage = '暖场' if p < .2 else '升温' if p < .4 else '呼吸' if p < .55 else '再起高潮' if p < .86 else '余韵'
    return energy, stage
