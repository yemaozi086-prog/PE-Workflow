from scipy.integrate import trapezoid


def calculate_energy(clock_us, trace_amperes, dc_millivolts):
    """Constant-voltage energy on the shared time grid; no physical transforms.

    clock_us: strictly increasing time samples, microseconds (us).
    trace_amperes: instantaneous current in A, same length/grid as clock_us.
    dc_millivolts: constant voltage in mV.
    Returns {'measurement': {'joules': energy in J}}.
    E = V * integral(I dt).
    """
    seconds = clock_us * 0.000001
    volts = dc_millivolts * 0.001
    return {'measurement': {'joules': volts * trapezoid(trace_amperes, x=seconds)}}
