from dataclasses import dataclass, asdict
from typing import List, Optional


@dataclass
class SwitchDevice:
    """功率开关器件的基本参数"""
    name: str          # 器件型号
    V_rating: float    # 器件额定电压 (V)
    I_rating: float    # 器件额定电流 (A, 连续或RMS)
    V_ce: float        # 器件的正向导通压降 (V)
    VF: float          # 器件的反向导通压降 (V)
    C_oss: float       # 输出端寄生电容  (pF)
    E_on: float        # 开通能量    (uJ)
    E_off: float       # 关断能量    (uJ)
    R_JC: float        # 结壳热阻    (℃/W)
    tech: str          # 器件类型，如 'IGBT' 等
    notes: str = ""    # 封装

    def to_dict(self):
        return asdict(self)


# ====== 1) 事先准备好的器件“字典”（数据库） ======
# 注意：这里的型号和数值只作为示例，请按你的项目替换为真实器件
DEVICE_DB: List[SwitchDevice] = [
    SwitchDevice("SGTQ50V65UFCR3S7", 650, 50, 2.05, 1.76, 268, 830, 380, 0.5, "IGBT", "TO-263-7L"),
    SwitchDevice("IKW40N65RH5", 650, 40, 1.94, 1.94, 265, 181.7, 221.4, 0.63, "IGBT", "TO-247"),
    SwitchDevice("IKW40N120CS7", 1200, 40, 2.00, 1.60, 120, 3850, 3650, 0.45, "IGBT", "TO-247"),
    SwitchDevice("IHW30N160R5", 1600, 30, 2.40, 2.60, 42, 0, 1270, 0.62, "IGBT", "TO-247"),
]

# ====== 2) 选型核心函数 ======
def select_switch(
    v_max: float,
    i_rms: float,
    tech_preference: Optional[List[str]] = None
) -> Optional[SwitchDevice]:
    """
    根据器件两端最大电压 v_max、器件 RMS 电流 i_rms 进行开关管选型。

    参数:
        v_max:  器件两端可能出现的最大电压 (V)
        i_rms:  器件中流过的最大电流有效值 (A)
        safety_v: 电压安全系数，如1.2表示额定电压 >= 1.2 * v_max
        safety_i: 电流安全系数，如1.5表示额定电流 >= 1.2 * i_rms
        tech_preference: 器件技术偏好列表，如 ["SiC MOSFET", "IGBT"]，可为 None

    返回:
        最合适的 SwitchDevice 对象，如果没有合适器件则返回 None。
    """
    safety_v: float = 1.2
    safety_i: float = 1.2
    required_V = v_max * safety_v
    required_I = i_rms * safety_i

    # print(f"设计要求：V_max = {v_max:.1f} V, I_rms = {i_rms:.1f} A")
    # print(f"安全系数后：V_required = {required_V:.1f} V, "
    #       f"I_required = {required_I:.1f} A")

    # 1) 过滤出满足额定电压 & 额定电流要求的器件
    candidates = []
    for dev in DEVICE_DB:
        if dev.V_rating >= required_V and dev.I_rating >= required_I:
            candidates.append(dev)

    if not candidates:
        # print("⚠️ 当前器件库中没有满足电压和电流要求的器件，请扩充数据库或降低要求。")
        return None

    # 2) 如果有技术偏好，优先只在偏好列表中选择
    if tech_preference:
        preferred_candidates = [
            d for d in candidates if d.tech in tech_preference
        ]
        if preferred_candidates:
            candidates = preferred_candidates

    # 3) 按“额定容量”从小到大排序，选出“刚刚好够用”的那颗
    # 这里用 V_rating * I_rating 粗略代表器件“大小/成本”
    candidates.sort(key=lambda d: d.V_rating * d.I_rating)

    best = candidates[0]
    # print("\n候选器件（按额定容量从小到大排序）：")
    # for d in candidates:
    #     score = d.V_rating * d.I_rating
    #     print(f"  - {d.name:25s}  {d.tech:10s}  "
    #           f"{d.V_rating:4.0f} V, {d.I_rating:5.1f} A, 评分≈{score:.0f}")

    # print(f"  型号: {best.name}")


    return best.name


# ====== 3) 简单示例 ======
if __name__ == "__main__":
    best = select_switch(400, 10)
    if best is not None:
        print(best)
    else:
        print("未找到合适器件")
