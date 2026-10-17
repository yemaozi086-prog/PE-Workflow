import math
from typing import Dict, Any


def calc_Cr_loss(
    current_dict: Dict[str, Any],
    tanx: float = 0.001,
    max_groups: int = 5
) -> Dict[str, float]:
    """
    返回格式(扁平字典):
    {
        "p_cap_total": float,
        "cap1_loss": float,   # 仅包含已成功计算的组
        "cap2_loss": float,
        ...
    }
    """
    result: Dict[str, float] = {}

    # fs 非法时直接返回 total_loss=0
    try:
        fs = float(current_dict.get("fs", 0.0))
    except (TypeError, ValueError):
        fs = 0.0

    if fs <= 0:
        return {"total_loss": 0.0}

    total_loss = 0.0

    for i in range(1, max_groups + 1):
        c_key = f"capacitance{i}"
        i_key = f"i_rms_c{i}"

        # 不存在该组 -> 跳过
        if c_key not in current_dict or i_key not in current_dict:
            continue

        try:
            C = float(current_dict[c_key])
            i_rms = float(current_dict[i_key])
        except (TypeError, ValueError):
            continue

        if C <= 0 or i_rms < 0:
            continue

        R = tanx / (2 * math.pi * fs * C)
        P = i_rms * i_rms * R

        result[f"cap{i}_loss"] = P
        total_loss += P

    result["p_cap_total"] = total_loss
    return result


if __name__ == "__main__":
    current_dict = {
        "fs": 70000,
        "capacitance1": 200e-9,
        "i_rms_c1": 10,
        "capacitance2": 100e-9,
        "i_rms_c2": 8
    }

    out = calc_Cr_loss(current_dict)
    print(out)
