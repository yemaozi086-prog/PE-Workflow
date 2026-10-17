import re

import numpy as np


def summarize_across_workpoints_simple(all_summaries):
    """
    Merge single-modulation key parameter dictionaries across workpoints.

    This is for models where each workpoint has one voltage/current dictionary
    rather than multiple feasible modulation dictionaries.
    """
    if not all_summaries:
        return None

    def _to_finite_float(x):
        try:
            value = float(x)
        except Exception:
            return None
        return value if np.isfinite(value) else None

    pat_i_rms_s = re.compile(r"^i_rms_s[1-8]$")
    pat_i_rms_d = re.compile(r"^i_rms_d[1-8]$")
    pat_v_s = re.compile(r"^v_s[1-8]$")
    pat_v_d = re.compile(r"^v_d[1-8]$")

    current_keys = ["i_rms_p"]
    voltage_keys = [
        "v_rms_p", "v_rms_s",
        "v_peak_filter_in", "v_peak_filter_out",
    ]

    out = {
        "i_rms_s_max": -np.inf,
        "i_rms_d_max": -np.inf,
        "v_s_max": -np.inf,
        "v_d_max": -np.inf,
        "fs_min": np.inf,
    }

    for key in current_keys:
        out[f"{key}_max"] = -np.inf

    for idx in range(1, 6):
        out[f"i_rms_ind{idx}_max"] = -np.inf
        out[f"i_peak_ind{idx}_max"] = -np.inf
        out[f"v_peak_c{idx}_max"] = -np.inf

    for key in voltage_keys:
        out[f"{key}_max"] = -np.inf

    best_i_rms_p = -np.inf
    best_v_rms_p = None
    best_v_rms_s = None
    n_val = None
    inductance_group_vals = {idx: None for idx in range(1, 6)}
    capacitance_group_vals = {idx: None for idx in range(1, 6)}

    for summary in all_summaries:
        if not isinstance(summary, dict):
            continue

        if n_val is None:
            n_val = _to_finite_float(summary.get("n"))

        for idx in range(1, 6):
            if inductance_group_vals[idx] is None:
                inductance_group_vals[idx] = _to_finite_float(summary.get(f"inductance{idx}"))
            if capacitance_group_vals[idx] is None:
                capacitance_group_vals[idx] = _to_finite_float(summary.get(f"capacitance{idx}"))

        for key, value in summary.items():
            finite_value = _to_finite_float(value)
            if finite_value is None:
                continue

            if pat_i_rms_s.match(key):
                out["i_rms_s_max"] = max(out["i_rms_s_max"], finite_value)
            elif pat_i_rms_d.match(key):
                out["i_rms_d_max"] = max(out["i_rms_d_max"], finite_value)
            elif pat_v_s.match(key):
                out["v_s_max"] = max(out["v_s_max"], finite_value)
            elif pat_v_d.match(key):
                out["v_d_max"] = max(out["v_d_max"], finite_value)

        for key in current_keys:
            value = _to_finite_float(summary.get(key))
            if value is not None:
                out[f"{key}_max"] = max(out[f"{key}_max"], value)

        for idx in range(1, 6):
            i_rms_ind = _to_finite_float(summary.get(f"i_rms_ind{idx}"))
            if i_rms_ind is not None:
                out[f"i_rms_ind{idx}_max"] = max(out[f"i_rms_ind{idx}_max"], i_rms_ind)

            i_peak_ind = _to_finite_float(summary.get(f"i_peak_ind{idx}"))
            if i_peak_ind is not None:
                out[f"i_peak_ind{idx}_max"] = max(out[f"i_peak_ind{idx}_max"], i_peak_ind)

            v_peak_c = _to_finite_float(summary.get(f"v_peak_c{idx}"))
            if v_peak_c is not None:
                out[f"v_peak_c{idx}_max"] = max(out[f"v_peak_c{idx}_max"], v_peak_c)

        for key in voltage_keys:
            value = _to_finite_float(summary.get(key))
            if value is not None:
                out[f"{key}_max"] = max(out[f"{key}_max"], value)

        fs_value = _to_finite_float(summary.get("fs"))
        if fs_value is not None:
            out["fs_min"] = min(out["fs_min"], fs_value)

        i_rms_p = _to_finite_float(summary.get("i_rms_p"))
        if i_rms_p is not None and i_rms_p > best_i_rms_p:
            best_i_rms_p = i_rms_p
            best_v_rms_p = _to_finite_float(summary.get("v_rms_p"))
            best_v_rms_s = _to_finite_float(summary.get("v_rms_s"))

    any_valid = False
    for key in list(out.keys()):
        value = out[key]
        if value == np.inf or value == -np.inf:
            out[key] = None
        else:
            any_valid = True

    out["v_rms_p"] = best_v_rms_p if best_i_rms_p != -np.inf else None
    out["v_rms_s"] = best_v_rms_s if best_i_rms_p != -np.inf else None
    out["n"] = n_val

    for idx in range(1, 6):
        out[f"inductance{idx}"] = inductance_group_vals[idx]
        if inductance_group_vals[idx] is not None:
            any_valid = True

        out[f"capacitance{idx}"] = capacitance_group_vals[idx]
        if capacitance_group_vals[idx] is not None:
            any_valid = True

    return out if any_valid else None
