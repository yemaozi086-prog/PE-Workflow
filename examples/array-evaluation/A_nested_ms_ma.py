from scipy.integrate import trapezoid


def integrate_uploaded(packet, bus):
    """Constant-voltage energy, no resampling.

    packet['clock_ms']: shared strictly increasing time samples in ms.
    packet['channel']['current_mA']: instantaneous current on that same grid, mA.
    bus: constant voltage in V.
    Returns ({'energy_mJ': energy in mJ},). E = V * integral(I dt).
    """
    seconds = packet['clock_ms'] / 1000.0
    amperes = packet['channel']['current_mA'] / 1000.0
    energy = bus * trapezoid(amperes, x=seconds)
    return ({'energy_mJ': energy * 1000.0},)
