import math


def capacitor_loss_alternate(drive, tank, upstream_ok):
    """Interface-only alternative for DBSRC EPS's single resonant capacitor.

    drive['frequency_khz']: switching frequency, kHz.
    drive['rms_ma']: capacitor RMS current, mA.
    drive['candidate_ok']: 1 for a real candidate; 0 for an inert rejected row.
    tank['capacitance_nf']: resonant capacitance, nF.
    upstream_ok: 1 only if preceding independent constraint passed.
    Returns {'ok': 0/1, 'loss_mw': loss in mW}; loss tangent is exactly
    the original model's default 0.001. Rejected rows return inert padding.
    """
    if drive['candidate_ok'] == 0 or upstream_ok == 0:
        return {'ok': 0, 'loss_mw': 0}
    frequency = drive['frequency_khz'] * 1000
    current = drive['rms_ma'] * 0.001
    capacitance = tank['capacitance_nf'] * 1e-9
    resistance = 0.001 / (2 * math.pi * frequency * capacitance)
    return {'ok': 1, 'loss_mw': current * current * resistance * 1000}
