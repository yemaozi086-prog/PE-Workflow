import math


def calc_capacitor_pending(drive, device):
    """Single-group resonant-capacitor loss supplied with incomplete return documentation.

    drive["F_kHz"]: switching frequency, kHz, not resonance frequency.
    drive["Ieff_mA"]: capacitor group 1 RMS current, mA.
    device["capacity_nF"]: total parallel capacitance of this group, nF.
    device["loss_tangent"]: dimensionless dielectric loss tangent.

    Returns {"raw_group": ..., "raw_total": ...} for the same single group.
    IMPORTANT: the return unit and meaning of the final factor 1000 have NOT
    been confirmed by the model provider. Ask for that confirmation before
    registration. Do not treat a plausible interpretation of the arithmetic
    as an authoritative declaration of output units or physical corrections.
    """
    frequency_hz = drive["F_kHz"] * 1000.0
    current_a = drive["Ieff_mA"] * 0.001
    capacitance_f = device["capacity_nF"] * 0.000000001
    resistance = device["loss_tangent"] / (2.0 * math.pi * frequency_hz * capacitance_f)
    raw_value = current_a * current_a * resistance * 1000.0
    return {"raw_group": raw_value, "raw_total": raw_value}
