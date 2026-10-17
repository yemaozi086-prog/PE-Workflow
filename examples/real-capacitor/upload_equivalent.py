"""研究人员上传实验样例：人为制作的接口变体，计算公式不变。

operating = {
    "switching": {"frequency_khz": 开关频率，kHz},
    "current_ma": 电容组有效值电流，mA
}
component = {
    "capacitance_nf": 同一组电容的等效电容，nF,
    "dissipation_factor": 损耗角正切，无量纲
}
返回单元素元组，内部 capacitor.dissipation_mw 为该组损耗，mW。
只适用于一组电容、频率及电容为正、电流和损耗角正切非负的情况。
"""
import math


def calculate_uploaded_loss(operating, component):
    frequency = operating["switching"]["frequency_khz"] * 1000
    current = operating["current_ma"] / 1000
    capacitance = component["capacitance_nf"] * 1e-9
    tangent = component["dissipation_factor"]
    resistance = tangent / (2 * math.pi * frequency * capacitance)
    watts = current * current * resistance
    return ({"capacitor": {"dissipation_mw": watts * 1000}},)
