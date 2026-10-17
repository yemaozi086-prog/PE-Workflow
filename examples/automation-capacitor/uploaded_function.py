import math

def evaluate_capacitor(point, part):
    frequency_hz = point["freq_MHz"] * 1000000.0
    capacitance_f = part["values"]["c_uF"] * 0.000001
    current_a = point["irms_A"]
    resistance_ohm = part["df"] / (2.0 * math.pi * frequency_hz * capacitance_f)
    power_w = current_a * current_a * resistance_ohm
    return ({"watts_per_group": power_w}, {"total_mW": power_w * 1000.0})
