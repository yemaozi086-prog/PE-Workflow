from abc import abstractmethod
from dataclasses import dataclass
from typing import Dict, Tuple, Union, Type

import numpy as np
from scipy.interpolate import interp1d


@dataclass
class IGBTParameters:
    # 驱动电阻与开关能量相关系数
    rg_on_table: np.ndarray
    e_rg_on_values: np.ndarray
    rg_off_table: np.ndarray
    e_rg_off_values: np.ndarray
    # 结温与开关能量相关系数
    tj_table: np.ndarray
    e_tj_on_values: np.ndarray
    e_tj_off_values: np.ndarray
    # 电流瞬时值与开关能量相关系数
    i_table: np.ndarray
    e_i_on_values: np.ndarray
    e_i_off_values: np.ndarray
    # 电压瞬时值与开关能量相关系数
    v_table: np.ndarray
    e_v_on_values: np.ndarray
    e_v_off_values: np.ndarray
    # Vce相关参数
    conduction_i_table: np.ndarray
    conduction_v_table: np.ndarray
    # VF相关参数
    diode_i_table: np.ndarray
    diode_v_table: np.ndarray

    RG_ON: float  # 导通电阻
    RG_OFF: float  # 关断电阻
    TJ: float  # 结温
    Coss: float  # 结电容
    Td: float  # 死区时间
    loss_constraint: float  # 损耗约束
    cost: float  # 成本
    type: str  # 开关器件类型


class IGBT:
    def __init__(self, params: IGBTParameters):
        self.params = params

    @abstractmethod
    def VF(self, i):
        raise NotImplementedError

    @abstractmethod
    def Vce_sat(self, i):
        raise NotImplementedError

    @abstractmethod
    def E_on(self, i, v):
        raise NotImplementedError

    @abstractmethod
    def E_off(self, i, v):
        raise NotImplementedError

    def calcu_cost(self, num_switches: int):
        n = max(0, int(num_switches))
        return n * self.params.cost

    def calc_single_cond_loss(self, current_dict: Dict, idx: int):
        """
        返回: (p_cond 或 None, cond_mode 或 None)
        cond_mode:
          - "forward_backward_ratio"  : 用 forward/backward/ratio 公式
          - "mean_only"               : 用 i_mean_sX 简化公式
          - None                      : 本管导通不可算
        """
        k_fwd = f"i_mean_s{idx}_forward"
        k_bwd = f"i_mean_s{idx}_backward"
        k_ratio = f"s{idx}_forward_ratio"
        k_mean = f"i_mean_s{idx}"

        # 优先：forward/backward/ratio
        if (k_fwd in current_dict) and (k_bwd in current_dict) and (k_ratio in current_dict):
            i_f = abs(float(current_dict[k_fwd]))
            i_b = abs(float(current_dict[k_bwd]))
            ratio = abs(float(current_dict[k_ratio]))
            ratio = max(0.0, min(1.0, ratio))  # 限制到[0,1]

            p_cond = 0.5 * (
                    i_f * self.Vce_sat(i_f) * ratio
                    + i_b * self.VF(i_b) * (1.0 - ratio)
            )
            return p_cond, "forward_backward_ratio"

        # 其次：i_mean_sX
        if k_mean in current_dict:
            i_m = abs(float(current_dict[k_mean]))
            p_cond = i_m * self.Vce_sat(i_m)
            return p_cond, "mean_only"

        return None, None

    def calc_single_sw_loss(self, current_dict: Dict, idx: int, fs: float):
        """
        返回: (p_on 或 None, p_off 或 None)
        若本管开关参数不全，则返回 (None, None)
        """
        k_on = f"i_on_s{idx}"
        k_off = f"i_off_s{idx}"
        k_v = f"v_s{idx}"

        if not ((k_on in current_dict) and (k_off in current_dict) and (k_v in current_dict)):
            return None, None

        i_on = float(current_dict[k_on])
        i_off = float(current_dict[k_off])
        v_s = float(current_dict[k_v])

        zvs_th = -(2 * self.params.Coss * (0.5 * v_s) / self.params.Td)

        p_on = 0.0
        if not (i_on < zvs_th):
            p_on = fs * self.E_on(abs(i_on), v_s)

        p_off = 0.0
        if not (i_off < zvs_th):
            p_off = fs * self.E_off(abs(i_off), v_s)

        return p_on, p_off

    def verify_switch_constraints(self, p_s1, p_s4, p_s5, p_s8):
        single_switch_loss_max = max(p_s1, p_s4, p_s5, p_s8)
        return single_switch_loss_max <= self.params.loss_constraint


class IKW40N65RH5(IGBT):
    def __init__(self):
        params = IGBTParameters(
            rg_on_table=np.array([4.8, 10.1, 15, 24, 40.6, 61.2]),
            e_rg_on_values=np.array([0.1065, 0.1473, 0.1817, 0.241, 0.3465, 0.4831]),
            rg_off_table=np.array([4.8, 10.1, 15, 24, 40.6, 61.2]),
            e_rg_off_values=np.array([0.21, 0.22, 0.2214, 0.23225, 0.29, 0.3831]),
            tj_table=np.array([25, 100, 150, 175]),
            e_tj_on_values=np.array([0.1596, 0.1686, 0.1817, 0.188]),
            e_tj_off_values=np.array([0.12, 0.175, 0.2214, 0.2485]),
            i_table=np.array([0, 5, 10, 20, 60, 80, 100, 120]),
            e_i_on_values=np.array([0, 0.0435, 0.0738, 0.1817, 1.0646, 1.6054, 2.0876, 2.5896]),
            e_i_off_values=np.array([0, 0.071475, 0.1182, 0.2214, 0.96, 1.46, 2.063, 2.873]),
            v_table=np.array([0, 200, 300, 400, 500]),
            e_v_on_values=np.array([0, 0.1028, 0.1301, 0.1817, 0.1936]),
            e_v_off_values=np.array([0, 0.1497, 0.1874, 0.2214, 0.2412]),
            conduction_i_table=np.array([2.866, 5.066, 7.58, 11.14, 15.364, 20.06, 25.156, 29.6, 35.24, 41.22, 47.26]),
            conduction_v_table=np.array([0.833827, 0.968206, 1.08086, 1.21235, 1.33975, 1.46293, 1.58706, 1.69372, 1.81346, 1.93925, 2.05867]),
            diode_i_table=np.array([None]),
            diode_v_table=np.array([None]),
            RG_ON=15,
            RG_OFF=15,
            TJ=150,
            Coss=265e-12,
            Td=200e-9,
            loss_constraint=80,
            cost=9.2,
            type="IGBT",
        )
        super().__init__(params)

        self.e_rg_on_interp = interp1d(self.params.rg_on_table, self.params.e_rg_on_values, fill_value="extrapolate")
        self.e_rg_off_interp = interp1d(self.params.rg_off_table, self.params.e_rg_off_values, fill_value="extrapolate")
        self.e_tj_on_interp = interp1d(self.params.tj_table, self.params.e_tj_on_values, fill_value="extrapolate")
        self.e_tj_off_interp = interp1d(self.params.tj_table, self.params.e_tj_off_values, fill_value="extrapolate")
        self.e_i_on_interp = interp1d(self.params.i_table, self.params.e_i_on_values, fill_value="extrapolate")
        self.e_i_off_interp = interp1d(self.params.i_table, self.params.e_i_off_values, fill_value="extrapolate")
        self.e_v_on_interp = interp1d(self.params.v_table, self.params.e_v_on_values, fill_value="extrapolate")
        self.e_v_off_interp = interp1d(self.params.v_table, self.params.e_v_off_values, fill_value="extrapolate")
        self.conduction_interp = interp1d(self.params.conduction_i_table, self.params.conduction_v_table, fill_value="extrapolate")

    def VF(self, i):
        return self.Vce_sat(i)

    def Vce_sat(self, i):
        return self.conduction_interp(i)

    def E_on(self, i, v):
        e_rg_on = self.e_rg_on_interp(self.params.RG_ON)
        e_tj_on = self.e_tj_on_interp(self.params.TJ)
        e_i_on = self.e_i_on_interp(i)
        e_v_on = self.e_v_on_interp(v)
        denominator = self.e_i_on_interp(20)
        if denominator == 0:
            denominator = 1e-6
        turn_on_energy = max(e_rg_on * e_tj_on * e_i_on * e_v_on / (denominator**3) * 1e-3, 0)
        return turn_on_energy

    def E_off(self, i, v):
        e_rg_off = self.e_rg_off_interp(self.params.RG_OFF)
        e_tj_off = self.e_tj_off_interp(self.params.TJ)
        e_i_off = self.e_i_off_interp(i)
        e_v_off = self.e_v_off_interp(v)
        denominator = self.e_i_off_interp(20)
        if denominator == 0:
            denominator = 1e-6
        turn_off_energy = max(e_rg_off * e_tj_off * e_i_off * e_v_off / (denominator**3) * 1e-3, 0)
        return turn_off_energy


class SGTQ50V65UFCR3S7(IGBT):
    def __init__(self):
        params = IGBTParameters(
            rg_on_table=np.array([None]),
            e_rg_on_values=np.array([None]),
            rg_off_table=np.array([None]),
            e_rg_off_values=np.array([None]),
            tj_table=np.array([None]),
            e_tj_on_values=np.array([None]),
            e_tj_off_values=np.array([None]),
            i_table=np.array([0, 10, 20, 30, 40, 50]),
            e_i_on_values=np.array([0.00, 36.86, 97.20, 188.30, 276.50, 341.10]),
            e_i_off_values=np.array([0.00, 80.51, 147.90, 247.90, 341.80, 447.20]),
            v_table=np.array([None]),
            e_v_on_values=np.array([None]),
            e_v_off_values=np.array([None]),
            conduction_i_table=np.array([0, 1, 2, 4, 6, 10, 20, 30, 40, 50]),
            conduction_v_table=np.array([0.00, 0.844033, 0.903333, 1.003, 1.09433, 1.25967, 1.66767, 2.08467, 2.53667, 3.008]),
            diode_i_table=np.array([0, 5, 10, 30, 60, 80, 120, 160, 200]),
            diode_v_table=np.array([0.00, 1.038, 1.21233, 1.67267, 2.20133, 2.522, 3.13367, 3.76367, 4.44567]),
            RG_ON=15,
            RG_OFF=15,
            TJ=125,
            Coss=265e-12,
            Td=200e-9,
            loss_constraint=80,
            cost=9.2,
            type="IGBT",
        )
        super().__init__(params)

        self.e_i_on_interp = interp1d(self.params.i_table, self.params.e_i_on_values, fill_value="extrapolate")
        self.e_i_off_interp = interp1d(self.params.i_table, self.params.e_i_off_values, fill_value="extrapolate")
        self.conduction_interp = interp1d(self.params.conduction_i_table, self.params.conduction_v_table, fill_value="extrapolate")
        self.diode_interp = interp1d(self.params.diode_i_table, self.params.diode_v_table, fill_value="extrapolate")

        # 生成电流查表范围：0 ~ 100A，步进0.01A
        self.i_lookup = np.arange(0, 100.0 + 0.01, 0.01)

        # 查表数组
        self.conduction_table = self.conduction_interp(self.i_lookup)
        self.diode_table = self.diode_interp(self.i_lookup)
        self.e_on_table = self.e_i_on_interp(self.i_lookup) * 1e-6
        self.e_off_table = self.e_i_off_interp(self.i_lookup) * 1e-6

    def _clamp_index(self, i):
        if i <= 0:
            return 0
        if i >= 100:
            return len(self.i_lookup) - 1
        idx = int(round(i * 100))  # 步进 0.01A
        return idx

    def VF(self, i):
        idx = self._clamp_index(i)
        return self.diode_table[idx]

    def Vce_sat(self, i):
        idx = self._clamp_index(i)
        return self.conduction_table[idx]

    def E_on(self, i, v):
        idx = self._clamp_index(i)
        return self.e_on_table[idx] * (v / 400.0)

    def E_off(self, i, v):
        idx = self._clamp_index(i)
        return self.e_off_table[idx] * (v / 400.0)


class IKW40N120CS7(IGBT):
    def __init__(self):
        params = IGBTParameters(
            rg_on_table=np.array([None]),
            e_rg_on_values=np.array([None]),
            rg_off_table=np.array([None]),
            e_rg_off_values=np.array([None]),
            tj_table=np.array([None]),
            e_tj_on_values=np.array([None]),
            e_tj_off_values=np.array([None]),
            i_table=np.array([0, 5, 10, 20, 40, 60, 80]),
            e_i_on_values=np.array([0, 400, 850, 1800, 3850, 6100, 8500]),
            e_i_off_values=np.array([0, 380, 800, 1700, 3650, 5700, 7900]),
            v_table=np.array([None]),
            e_v_on_values=np.array([None]),
            e_v_off_values=np.array([None]),
            conduction_i_table=np.array([0, 5, 10, 20, 40, 60, 80]),
            conduction_v_table=np.array([0, 0.95, 1.15, 1.50, 2.00, 2.50, 3.00]),
            diode_i_table=np.array([0, 5, 10, 20, 40, 60, 80]),
            diode_v_table=np.array([0, 0.90, 1.05, 1.25, 1.60, 1.95, 2.30]),
            RG_ON=4,
            RG_OFF=4,
            TJ=175,
            Coss=120e-12,
            Td=200e-9,
            loss_constraint=80,
            cost=9.2,
            type="IGBT",
        )
        super().__init__(params)
        self.e_i_on_interp = interp1d(self.params.i_table, self.params.e_i_on_values, fill_value="extrapolate")
        self.e_i_off_interp = interp1d(self.params.i_table, self.params.e_i_off_values, fill_value="extrapolate")
        self.conduction_interp = interp1d(self.params.conduction_i_table, self.params.conduction_v_table, fill_value="extrapolate")
        self.diode_interp = interp1d(self.params.diode_i_table, self.params.diode_v_table, fill_value="extrapolate")
        self.i_lookup = np.arange(0, 100.0 + 0.01, 0.01)
        self.conduction_table = self.conduction_interp(self.i_lookup)
        self.diode_table = self.diode_interp(self.i_lookup)
        self.e_on_table = self.e_i_on_interp(self.i_lookup) * 1e-6
        self.e_off_table = self.e_i_off_interp(self.i_lookup) * 1e-6

    def _clamp_index(self, i):
        if i <= 0:
            return 0
        if i >= 100:
            return len(self.i_lookup) - 1
        return int(round(i * 100))

    def VF(self, i):
        return self.diode_table[self._clamp_index(i)]

    def Vce_sat(self, i):
        return self.conduction_table[self._clamp_index(i)]

    def E_on(self, i, v):
        return self.e_on_table[self._clamp_index(i)] * (v / 600.0)

    def E_off(self, i, v):
        return self.e_off_table[self._clamp_index(i)] * (v / 600.0)


class IHW30N160R5(IGBT):
    def __init__(self):
        params = IGBTParameters(
            rg_on_table=np.array([None]),
            e_rg_on_values=np.array([None]),
            rg_off_table=np.array([None]),
            e_rg_off_values=np.array([None]),
            tj_table=np.array([None]),
            e_tj_on_values=np.array([None]),
            e_tj_off_values=np.array([None]),
            i_table=np.array([0, 5, 10, 20, 30, 45, 60]),
            e_i_on_values=np.array([0, 0, 0, 0, 0, 0, 0]),
            e_i_off_values=np.array([0, 180, 380, 820, 1270, 1950, 2700]),
            v_table=np.array([None]),
            e_v_on_values=np.array([None]),
            e_v_off_values=np.array([None]),
            conduction_i_table=np.array([0, 5, 10, 20, 30, 45, 60]),
            conduction_v_table=np.array([0, 1.10, 1.35, 1.85, 2.40, 3.05, 3.70]),
            diode_i_table=np.array([0, 5, 10, 20, 30, 45, 60]),
            diode_v_table=np.array([0, 1.10, 1.40, 2.00, 2.60, 3.30, 4.00]),
            RG_ON=10,
            RG_OFF=10,
            TJ=175,
            Coss=42e-12,
            Td=200e-9,
            loss_constraint=80,
            cost=9.2,
            type="IGBT",
        )
        super().__init__(params)
        self.e_i_on_interp = interp1d(self.params.i_table, self.params.e_i_on_values, fill_value="extrapolate")
        self.e_i_off_interp = interp1d(self.params.i_table, self.params.e_i_off_values, fill_value="extrapolate")
        self.conduction_interp = interp1d(self.params.conduction_i_table, self.params.conduction_v_table, fill_value="extrapolate")
        self.diode_interp = interp1d(self.params.diode_i_table, self.params.diode_v_table, fill_value="extrapolate")
        self.i_lookup = np.arange(0, 100.0 + 0.01, 0.01)
        self.conduction_table = self.conduction_interp(self.i_lookup)
        self.diode_table = self.diode_interp(self.i_lookup)
        self.e_on_table = self.e_i_on_interp(self.i_lookup) * 1e-6
        self.e_off_table = self.e_i_off_interp(self.i_lookup) * 1e-6

    def _clamp_index(self, i):
        if i <= 0:
            return 0
        if i >= 100:
            return len(self.i_lookup) - 1
        return int(round(i * 100))

    def VF(self, i):
        return self.diode_table[self._clamp_index(i)]

    def Vce_sat(self, i):
        return self.conduction_table[self._clamp_index(i)]

    def E_on(self, i, v):
        return self.e_on_table[self._clamp_index(i)] * (v / 600.0)

    def E_off(self, i, v):
        return self.e_off_table[self._clamp_index(i)] * (v / 600.0)


# ============================================================
# ✅ 新增：对外统一调用接口（主程序不再写 globals()[switch]）
# ============================================================

# 1) 型号字符串 -> 类 的映射（你新增器件，就在这里加一行）
SWITCH_REGISTRY: Dict[str, Type[IGBT]] = {
    "IKW40N65RH5": IKW40N65RH5,
    "SGTQ50V65UFCR3S7": SGTQ50V65UFCR3S7,
    "IKW40N120CS7": IKW40N120CS7,
    "IHW30N160R5": IHW30N160R5,
}

# 2) 可选：缓存实例（批量寻优时减少重复构造）
_SWITCH_INSTANCE_CACHE: Dict[str, IGBT] = {}


def get_switch(device: Union[str, IGBT, Type[IGBT]], use_cache: bool = True) -> IGBT:
    """
    device 可以是：
      - 型号字符串： "SGTQ50V65UFCR3S7"
      - 已实例化对象： SGTQ50V65UFCR3S7()
      - 类： SGTQ50V65UFCR3S7
    """
    if isinstance(device, IGBT):
        return device

    if isinstance(device, str):
        if device not in SWITCH_REGISTRY:
            raise ValueError(f"Unknown device model '{device}'. Available: {list(SWITCH_REGISTRY.keys())}")

        if use_cache and device in _SWITCH_INSTANCE_CACHE:
            return _SWITCH_INSTANCE_CACHE[device]

        inst = SWITCH_REGISTRY[device]()
        if use_cache:
            _SWITCH_INSTANCE_CACHE[device] = inst
        return inst

    if isinstance(device, type) and issubclass(device, IGBT):
        return device()

    raise TypeError("device must be a model name (str), an IGBT instance, or an IGBT class.")


def calc_switch_loss(
    device_model: str,
    current_dict: Dict,
    max_switches: int = 8
) -> Dict:
    """
    新格式输出：
    - per_switch: 各管损耗（s1~s8）
    - skipped: 出现但无法计算的管子及原因
    - summary: 总损耗、已计算器件数、cost
    """
    sw = get_switch(device_model, use_cache=True)

    if "fs" not in current_dict:
        raise KeyError("current_dict 中缺少固定开关频率键 'fs'。")
    fs = float(current_dict["fs"])

    out = {
        "device_model": device_model,
        "fs": fs,
        "per_switch": {},
        "skipped": {},
        "summary": {
            "p_cond_s": 0.0,
            "p_on_s": 0.0,
            "p_off_s": 0.0,
            "p_s_total": 0.0,
            "num_calculated": 0,
            "cost_s": 0.0,
        }
    }

    for idx in range(1, max_switches + 1):
        skey = f"s{idx}"

        cond_detail_keys = [
            f"i_mean_s{idx}_forward",
            f"i_mean_s{idx}_backward",
            f"s{idx}_forward_ratio",
        ]
        cond_simple_key = f"i_mean_s{idx}"
        sw_keys = [f"i_on_s{idx}", f"i_off_s{idx}", f"v_s{idx}"]

        # 判断该组是否“出现过”
        has_any = any(k in current_dict for k in (cond_detail_keys + [cond_simple_key] + sw_keys))
        if not has_any:
            continue

        # 导通损耗（两种模式自动切换）
        p_cond, cond_mode = sw.calc_single_cond_loss(current_dict, idx)

        # 开关损耗（参数齐全才算）
        p_on, p_off = sw.calc_single_sw_loss(current_dict, idx, fs)

        # 若两类都不可算 -> skipped
        if (p_cond is None) and (p_on is None) and (p_off is None):
            out["skipped"][skey] = {
                "reason": "missing both conduction and switching required keys"
            }
            continue

        # 不可算项置0
        p_cond_val = 0.0 if p_cond is None else float(p_cond)
        p_on_val = 0.0 if p_on is None else float(p_on)
        p_off_val = 0.0 if p_off is None else float(p_off)

        p_total = p_cond_val + p_on_val + p_off_val

        out["per_switch"][skey] = {
            "p_cond": p_cond_val,
            "p_on": p_on_val,
            "p_off": p_off_val,
            "p_total": p_total,
            "cond_mode": cond_mode,   # "forward_backward_ratio" / "mean_only" / None
        }

        out["summary"]["p_cond_s"] += p_cond_val
        out["summary"]["p_on_s"] += p_on_val
        out["summary"]["p_off_s"] += p_off_val
        out["summary"]["p_s_total"] += p_total
        out["summary"]["num_calculated"] += 1

    out["summary"]["cost_s"] = sw.calcu_cost(out["summary"]["num_calculated"])
    return out


# 示例使用
if __name__ == "__main__":
    # 这里只演示接口调用方式；你实际工程里 current_dict 来自 current model
    current_dict= {
        # conduction 用到：
        "i_mean_s1_forward": 10.0, "i_mean_s1_backward": 2.0, "s1_forward_ratio": 0.8,
        "i_mean_s4_forward": 10.0, "i_mean_s4_backward": 2.0, "s4_forward_ratio": 0.8,
        "i_mean_s5_forward": 10.0, "i_mean_s5_backward": 2.0, "s5_forward_ratio": 0.8,
        "i_mean_s8_forward": 10.0, "i_mean_s8_backward": 2.0, "s8_forward_ratio": 0.8,

        # switching 用到：
        "i_on_s1": -5.0,
        "i_on_s4": -5.0,
        "i_on_s5":  5.0,
        "i_on_s8":  5.0,
        "i_off_s1": 5.0,
        "i_off_s4": 5.0,
        "i_off_s5": -5.0,
        "i_off_s8": -5.0,
        "v_s1": 390,
        "v_s5": 400,
        "n": 11/12,
        "fs": 90000,
    }

    p_s_dict = calc_switch_loss(device_model="SGTQ50V65UFCR3S7", current_dict=current_dict)
    print(p_s_dict)
    print(f"p_on  = {p_s_dict['summary']['p_on_s']:.6f} W")
    print(f"p_off = {p_s_dict['summary']['p_off_s']:.6f} W")
    print(f"p_cond= {p_s_dict['summary']['p_cond_s']:.6f} W")
