import numpy as np
from scipy.integrate import trapezoid


def energy_on_new_grid(record, dc_voltage, new_grid_ms):
    """Energy after an explicit resampling operation.

    record['clock_ms']: original shared time samples in ms.
    record['current_mA']: original instantaneous current in mA.
    dc_voltage: constant voltage, V.
    new_grid_ms: requested target time grid, ms. The provider has not specified
    this grid or approved extrapolation/boundary conditions and verification cases.
    Returns {'energy_mJ': energy in mJ}. No default grid is declared.
    """
    sampled = np.interp(new_grid_ms, record['clock_ms'], record['current_mA'])
    joules = dc_voltage * trapezoid(sampled * 0.001, x=new_grid_ms * 0.001)
    return {'energy_mJ': joules * 1000.0}
