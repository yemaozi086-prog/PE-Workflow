from dataclasses import dataclass
from typing import List, Optional
import math
from typing import Any, Dict, Optional


# ===================== 1) 谐振电容数据结构定义 =====================

@dataclass
class ResonantCapDevice:
    """谐振电容器件的基本参数"""
    name: str           # 型号
    C_nom: float        # 单只额定电容值 (F)
    V_rating: float     # 额定电压 (V)
    dielectric: str     # 介质，如 'PP'
    notes: str = ""     # 电容类型


# ===================== 2) 谐振电容库 =====================
# “PP”材质为聚丙烯
RES_CAP_DB: List[ResonantCapDevice] = [
    ResonantCapDevice("CFC2J102JD1IJ5073100",    1e-9,    630,  "PP", "薄膜电容"),
    ResonantCapDevice("B22J152JN1B0120100055EOZ",    1.5e-9,    630,  "PP", "薄膜电容"),
    ResonantCapDevice("MPB222J2J0501",    2.2e-9,    630,  "PP", "薄膜电容"),
    ResonantCapDevice("C822J272J30C000",    2.7e-9,    630,  "PP", "薄膜电容"),
    ResonantCapDevice("W312J332JJ6L00A00C",    3.3e-9,    630,  "PP", "薄膜电容"),
    ResonantCapDevice("PN472J2J0701",    4.7e-9,    630,  "PP", "薄膜电容"),
    ResonantCapDevice("W312J562JJ6L00A00C",    5.6e-9,    630,  "PP", "薄膜电容"),
    ResonantCapDevice("MPP682J2J1306096LC",   6.8e-9,   630,  "PP", "薄膜电容"),
    ResonantCapDevice("MPBH822J2J0701",   8.2e-9,    630, "PP", "薄膜电容"),
    ResonantCapDevice("KP103J2J1001",  10e-9,   630, "PP", "薄膜电容"),
    ResonantCapDevice("W312J123JJ6L00A00C", 12e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP153J2J1001", 15e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP183J2J1001", 18e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("KP223J2J1001", 22e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MMK273J630V82CB0198", 27e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP333J2J1001", 33e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP393J2J1001", 39e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP473J2J1502", 47e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP563J2J1501", 56e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MPB683J2J0701", 68e-9, 630, "PP", "薄膜电容"),

    ResonantCapDevice("KP224J2J1502", 220e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MPP334J2J15AJ22800", 330e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("KP564J2J1503", 560e-9, 630, "PP", "薄膜电容"),
    ResonantCapDevice("MPP125J2J1910208LC", 1.2e-6, 630, "PP", "薄膜电容"),
    ResonantCapDevice("228CB0117", 6.8e-6, 300, "PP", "薄膜电容"),
    ResonantCapDevice("MPBH126K2E2701", 12e-6, 250, "PP", "薄膜电容"),
    ResonantCapDevice("228CB0120", 15e-6, 250, "PP", "薄膜电容"),
    ResonantCapDevice("228CB0121", 20e-6, 250, "PP", "薄膜电容"),
    ResonantCapDevice("228CB0076", 25e-6, 800, "PP", "薄膜电容"),
    ResonantCapDevice("228CB0116", 30e-6, 300, "PP", "薄膜电容"),

    ResonantCapDevice("C82122J3AC2R6LC", 1.2e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMK152J3AC2AJ206G0", 1.5e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMK222J3AB3KN206G0", 2.2e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMK332J3AB3KN206G0", 3.3e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP392J3A1001", 3.9e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMK472J3AC2KN206G0", 4.7e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MPBH562J3A0701", 5.6e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP682J3A1001", 6.8e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP822J3A1001", 8.2e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MPBH103J3A0701", 10e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP123J3A1001", 12e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP153J3A1001", 15e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP183J3A1501", 18e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("KP223J1000VP15", 22e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP273J3A1001", 27e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("KP333J1000VP15", 33e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("C82393JAD8RLC", 39e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("KP473J1000VP15", 47e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("MMKP563J3A1502", 56e-9, 1000, "PP", "薄膜电容"),
    ResonantCapDevice("KP683J1000VP15", 68e-9, 1000, "PP", "薄膜电容"),
]


# ===================== 3) 谐振电容选型函数 =====================

def select_resonant_caps(
    v_max: float,
    C_target: float,
    max_parallel: int = 14,
    min_parallel: int = 3,   # ★ 新增最小并联数量限制
) -> Optional[dict]:
    """
    根据最大工作电压 v_max 和目标谐振电容 C_target 选型。
    本版本：只要总电容最接近目标值即可，允许略大或略小，
    并限制并联数量在 [min_parallel, max_parallel] 之间。

    参数:
        v_max:        电容两端可能出现的最大电压 (V)
        C_target:     目标谐振电容 (F)
        max_parallel: 允许的最大并联数量
        min_parallel: 允许的最小并联数量

    返回:
        {
            "device_name": 型号字符串,
            "device_obj":  ResonantCapDevice 对象,
            "C_nom":       单只电容值 (F),
            "n_parallel":  并联只数,
            "C_total":     并联后的总电容 (F),
            "C_target":    目标电容 (F),
            "over_ratio":  相对目标值的偏差比例（正：偏大，负：偏小）
        }
        如果没有合适方案，返回 None。
    """
    if C_target <= 0:
        # print("目标电容必须大于 0。")
        return None
    if min_parallel > max_parallel:
        # print("min_parallel 不能大于 max_parallel。")
        return None

    safety_v: float = 1.2
    required_V = v_max * safety_v

    # 1) 先筛出耐压满足要求的器件
    candidates = [c for c in RES_CAP_DB if c.V_rating >= required_V]

    if not candidates:
        # print("电容库中没有满足耐压要求的谐振电容，请增加更高耐压规格。")
        return None

    # 2) 对每种电容，考虑 n_floor 和 n_ceil 两种并联数量，选“最接近目标值”的方案
    best_plan = None
    best_score = None

    for cap in candidates:
        # floor：总电容可能略小于目标；
        # 原来是 max(1, ...)，现在改成至少 min_parallel
        n_floor = max(min_parallel, int(C_target // cap.C_nom))

        # ceil：总电容略大或等于目标
        if C_target % cap.C_nom == 0:
            n_ceil = n_floor
        else:
            n_ceil = n_floor + 1

        n_candidates = set()

        # 加入 floor 方案
        if min_parallel <= n_floor <= max_parallel:
            n_candidates.add(n_floor)

        # 加入 ceil 方案
        if min_parallel <= n_ceil <= max_parallel:
            n_candidates.add(n_ceil)

        if not n_candidates:
            # 该型号要么容量太小，要么需要的并联数量超出 [min_parallel, max_parallel]
            continue

        for n in n_candidates:
            C_total = n * cap.C_nom
            diff = C_total - C_target          # 正：偏大；负：偏小
            abs_diff = abs(diff)
            over_ratio = diff / C_target       # 相对偏差

            # 评分：优先“总电容最接近”，其次“并联数量更少”
            score = abs_diff + 0.001 * C_target * n

            if (best_score is None) or (score < best_score):
                best_score = score
                best_plan = {
                    "device_name": cap.name,
                    "C_nom": cap.C_nom,
                    "n_parallel": n,
                    "C_total": C_total,
                    "C_target": C_target,
                    "over_ratio": over_ratio,
                }

    if best_plan is None:
        # print("虽然有满足耐压的电容，但需要的并联数量都不在允许范围内。")
        return None

    # 3) 打印结果
    # print(best_plan)
    return best_plan


def _to_positive_float(x: Any):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def select_resonant_caps_from_dict(
    input_dict: Dict[str, Any],
    max_groups: int = 5,
    max_parallel: int = 14,
    min_parallel: int = 3,
) -> Optional[Dict[str, dict]]:
    """
    规则：
    - 若出现 capacitancei 但没有 v_peak_ci -> 立即返回 None
    - 其他正常组执行选型
    - 少于5组不会报错（只处理存在的组）
    """
    results: Dict[str, dict] = {}

    for i in range(1, max_groups + 1):
        c_key = f"capacitance{i}"
        v_key = f"v_peak_c{i}"

        c_raw = input_dict.get(c_key, None)
        v_raw = input_dict.get(v_key, None)

        # 关键修改：有 capacitancei 但没有 v_peak_capi -> 直接返回 None
        if c_raw is not None and v_raw is None:
            return None

        # 两个都没有：跳过
        if c_raw is None and v_raw is None:
            continue

        # （可选）如果你也想反向严格：有 v_peak 但没 capacitance，也返回 None
        # if c_raw is None and v_raw is not None:
        #     return None

        c_target = _to_positive_float(c_raw)
        v_peak = _to_positive_float(v_raw)
        if c_target is None or v_peak is None:
            return None  # 参数非法时你现在希望更严格，也直接返回 None

        plan = select_resonant_caps(
            v_max=v_peak,
            C_target=c_target,
            max_parallel=max_parallel,
            min_parallel=min_parallel
        )
        if plan is None:
            return None  # 无可行方案也按严格模式返回 None

        results[f"cap{i}"] = {
            "input": {c_key: c_raw, v_key: v_raw},
            "selection": plan
        }

    return results

# ===================== 4) 示例 =====================

if __name__ == "__main__":
    # 示例：最大电压 400 V，目标谐振电容 10 nF，最少 3 只并联
    # select_resonant_caps(400, 10e-9)
    # ====== 示例2 ======
    params_bad = {
        "capacitance1": 10e-9,
        "v_peak_c1": 400,
        "capacitance2": 47e-9,
        "v_peak_c2": 500
    }

    ret_bad = select_resonant_caps_from_dict(
        input_dict=params_bad,
    )

    print(ret_bad)  # None
