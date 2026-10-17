import re

import numpy as np


def batch_calculate_key_params(current_model, Vin, Vout, results):
    """
    Batch calculate key voltage/current parameters for all modulation results.

    The current model only needs to provide calculate_key_params. This helper
    adapts the existing tuple formats used by EPS/TPS models and packs the
    modulation metadata together with the calculated key_params.
    """
    if not results:
        return None

    packed = []
    for result in results:
        modulation, kwargs = _normalize_modulation_result(result)
        if modulation is None:
            continue

        key_params = current_model.calculate_key_params(Vin=Vin, Vout=Vout, **kwargs)
        if key_params is None:
            continue

        item = dict(modulation)
        item["key_params"] = key_params
        packed.append(item)

    return packed if packed else None


def summarize_packed_key_params(packed_list):
    """
    Summarize multiple modulation-point key_params under the same workpoint.

    Current stress fields are minimized across feasible modulation choices, while
    common voltage/component fields are kept from one representative key_params.
    """
    if not packed_list:
        return None

    items = [
        item for item in packed_list
        if isinstance(item, dict) and isinstance(item.get("key_params"), dict)
    ]
    if not items:
        return None

    kps = [item["key_params"] for item in items]

    def to_finite_float(x):
        if isinstance(x, np.ndarray):
            if x.size == 1:
                x = float(x.reshape(-1)[0])
            else:
                return None
        try:
            x = float(x)
        except Exception:
            return None
        return x if np.isfinite(x) else None

    def min_over_kps(key):
        vals = []
        for kp in kps:
            value = to_finite_float(kp.get(key))
            if value is not None:
                vals.append(value)
        return min(vals) if vals else None

    def min_of_group_max(pattern):
        group_max = []
        for kp in kps:
            values = []
            for key, value in kp.items():
                if pattern.match(key):
                    finite_value = to_finite_float(value)
                    if finite_value is not None:
                        values.append(finite_value)
            if values:
                group_max.append(max(values))
        return min(group_max) if group_max else None

    summary = {}

    i_rms_s = min_of_group_max(re.compile(r"^i_rms_s([1-8])$"))
    if i_rms_s is not None:
        summary["i_rms_s"] = i_rms_s

    i_rms_d = min_of_group_max(re.compile(r"^i_rms_d([1-8])$"))
    if i_rms_d is not None:
        summary["i_rms_d"] = i_rms_d

    i_rms_p = min_over_kps("i_rms_p")
    if i_rms_p is not None:
        summary["i_rms_p"] = i_rms_p

    for idx in range(1, 6):
        for key in (f"i_rms_ind{idx}", f"i_peak_ind{idx}", f"v_peak_c{idx}"):
            value = min_over_kps(key)
            if value is not None:
                summary[key] = value

    kp0 = kps[0]
    common_keys = [
        "v_rms_p", "v_rms_s",
        "v_peak_filter_in", "v_peak_filter_out",
        "fs", "n",
    ]
    for key in common_keys:
        if key in kp0:
            summary[key] = kp0[key]

    for key, value in kp0.items():
        if re.match(r"^v_s[1-8]$", key) or re.match(r"^v_d[1-8]$", key):
            summary[key] = value

    for idx in range(1, 6):
        for key in (f"inductance{idx}", f"capacitance{idx}"):
            if key in kp0:
                summary[key] = kp0[key]

    return summary if summary else None


def summarize_across_workpoints_simple(all_summaries):
    """
    Merge one-workpoint summaries into device-selection stress values.
    """
    if not all_summaries:
        return None

    def to_finite_float(x):
        try:
            value = float(x)
        except Exception:
            return None
        return value if np.isfinite(value) else None

    out = {}

    current_keys = ["i_rms_s", "i_rms_d", "i_rms_p"]
    voltage_keys = [
        "v_rms_p", "v_rms_s",
        "v_peak_filter_in", "v_peak_filter_out",
    ]

    for key in current_keys + voltage_keys:
        out[key + "_max"] = -np.inf
    out["fs_min"] = np.inf
    out["v_s_max"] = -np.inf
    out["v_d_max"] = -np.inf

    for idx in range(1, 6):
        out[f"i_rms_ind{idx}_max"] = -np.inf
        out[f"i_peak_ind{idx}_max"] = -np.inf
        out[f"v_peak_c{idx}_max"] = -np.inf

    best_i_rms_p = -np.inf
    best_v_rms_p = None
    best_v_rms_s = None
    n_val = None
    inductance_vals = {idx: None for idx in range(1, 6)}
    capacitance_vals = {idx: None for idx in range(1, 6)}

    for summary in all_summaries:
        if summary is None:
            continue

        if n_val is None:
            n_val = to_finite_float(summary.get("n"))

        for idx in range(1, 6):
            if inductance_vals[idx] is None:
                inductance_vals[idx] = to_finite_float(summary.get(f"inductance{idx}"))
            if capacitance_vals[idx] is None:
                capacitance_vals[idx] = to_finite_float(summary.get(f"capacitance{idx}"))

        for key in current_keys + voltage_keys:
            value = to_finite_float(summary.get(key))
            if value is None:
                continue
            out_key = key + "_max"
            if value > out[out_key]:
                out[out_key] = value

        i_rms_p = to_finite_float(summary.get("i_rms_p"))
        if i_rms_p is not None and i_rms_p > best_i_rms_p:
            best_i_rms_p = i_rms_p
            best_v_rms_p = to_finite_float(summary.get("v_rms_p"))
            best_v_rms_s = to_finite_float(summary.get("v_rms_s"))

        for idx in range(1, 6):
            for key_base in ("i_rms_ind", "i_peak_ind", "v_peak_c"):
                key = f"{key_base}{idx}"
                value = to_finite_float(summary.get(key))
                if value is None:
                    continue
                out_key = key + "_max"
                if value > out[out_key]:
                    out[out_key] = value

        for key, value in summary.items():
            finite_value = to_finite_float(value)
            if finite_value is None:
                continue
            if re.match(r"^v_s[1-8]$", key):
                out["v_s_max"] = max(out["v_s_max"], finite_value)
            elif re.match(r"^v_d[1-8]$", key):
                out["v_d_max"] = max(out["v_d_max"], finite_value)

        fs_value = to_finite_float(summary.get("fs"))
        if fs_value is not None and fs_value < out["fs_min"]:
            out["fs_min"] = fs_value

    if best_v_rms_p is not None:
        out["v_rms_p"] = best_v_rms_p
        out["v_rms_p_at_i_rms_p_max"] = best_v_rms_p
    if best_v_rms_s is not None:
        out["v_rms_s"] = best_v_rms_s
        out["v_rms_s_at_i_rms_p_max"] = best_v_rms_s
    if n_val is not None:
        out["n"] = n_val

    for idx in range(1, 6):
        if inductance_vals[idx] is not None:
            out[f"inductance{idx}"] = inductance_vals[idx]
        if capacitance_vals[idx] is not None:
            out[f"capacitance{idx}"] = capacitance_vals[idx]

    cleaned = {}
    for key, value in out.items():
        if isinstance(value, float) and (value == np.inf or value == -np.inf):
            continue
        cleaned[key] = value

    return cleaned if cleaned else None


def _normalize_modulation_result(result):
    if isinstance(result, dict):
        modulation = dict(result)
        required_keys = {"D1", "D2", "EPS", "mode"}
        if not required_keys.issubset(modulation):
            return None, None
        kwargs = {
            "beta": modulation["D1"],
            "alfa": modulation["D2"],
            "EPS": int(modulation["EPS"]),
            "mode": int(modulation["mode"]),
        }
        return modulation, kwargs

    if not isinstance(result, (tuple, list)):
        return None, None

    if len(result) == 4:
        d1, d2, eps, mode = result
        modulation = {
            "D1": float(d1),
            "D2": float(d2) if np.isscalar(d2) else d2,
            "EPS": int(eps),
            "mode": int(mode),
        }
        kwargs = {
            "beta": d1,
            "alfa": d2,
            "EPS": int(eps),
            "mode": int(mode),
        }
        return modulation, kwargs

    return None, None
