"""单组公式提取自旧 calc_Cr_loss；只处理契约已校验的有效输入。

这是平台迁移实现，非逐字复制。完整原函数另存于 reference/。
fs_hz: Hz; capacitance_f: F; i_rms_a: A; tan_delta: 无量纲。
返回一组电容的损耗和所选单组的总损耗，单位 W。
"""
import math


def capacitor_group_loss(fs_hz, capacitance_f, i_rms_a, tan_delta):
    resistance = tan_delta / (2 * math.pi * fs_hz * capacitance_f)
    loss = i_rms_a * i_rms_a * resistance
    return {"cap1_loss": loss, "p_cap_total": loss}
