import math


def calc_capacitor_alt(excitation, capacitor):
    """Single-group resonant-capacitor dielectric loss; interface adaptation only.

    excitation["switching"]["F_kHz"]: switching frequency in kHz, not resonance frequency.
    excitation["Ieff_mA"]: RMS current through capacitor group 1, in mA.
    capacitor["capacity_nF"]: total parallel capacitance of this group, in nF.
    capacitor["loss_tangent"]: dimensionless dielectric loss tangent.

    Returns a tuple:
      item 0: {"group_mW": single-group loss in mW};
      item 1: total capacitor loss in W. For this single-group case total equals group loss.
    Formula: R = tan_delta / (2*pi*f*C), P = I_rms**2 * R.
    No multi-group model, temperature correction or empirical coefficient is included.
    """
    frequency_hz = excitation["switching"]["F_kHz"] * 1000.0
    current_a = excitation["Ieff_mA"] * 0.001
    capacitance_f = capacitor["capacity_nF"] * 0.000000001
    resistance_ohm = capacitor["loss_tangent"] / (2.0 * math.pi * frequency_hz * capacitance_f)
    power_w = current_a * current_a * resistance_ohm
    return ({"group_mW": power_w * 1000.0}, power_w)
