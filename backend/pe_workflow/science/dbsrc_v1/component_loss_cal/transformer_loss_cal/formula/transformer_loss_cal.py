
class TransformerLoss:
    def __init__(self):
        # 铜线的电阻率（Ω*mm^2/m）
        self.r_ohm = 0.0185
        # 磁芯铁损相关系数
        self.Cm = 6.7088e-7
        self.x = 1.4164
        self.y = 2.5705
        self.Ct0 = 2.3359
        self.Ct1 = 0.03215
        self.Ct2 = 0.0001768
        self.T= 100

    # --- 小工具1：铜损 ---
    def calcu_cu_loss(self, current_dict, tp):
        wire_length_p = tp['wire_length_p']
        wire_length_s = tp['wire_length_s']
        wire_area_p = tp['wire_area_p']
        wire_area_s = tp['wire_area_s']
        n = current_dict['n']

        ip_rms = current_dict['i_rms_p']
        # 长度 mm -> m
        r_p = self.r_ohm * (wire_length_p * 1e-3) / wire_area_p
        r_s = self.r_ohm * (wire_length_s * 1e-3) / wire_area_s
        p_cu = ip_rms ** 2 * r_p + (ip_rms * n) ** 2 * r_s
        # 乘以 1.5 的修正系数
        return p_cu * 1.5

    # --- 小工具2：反求 B ---
    def calcu_B_backward(self, current_dict, tp):
        Ds = 0.5  # 副边侧占空比
        Ns = tp['Ns']
        Ae = tp['Ae_T']   # m^2
        fs = current_dict['fs']
        Vout = current_dict['v_rms_s']
        # 返回单位 T
        return (Ds * Vout) / (Ns * fs * Ae) / 2

    # --- 小工具3：铁损 ---
    def calcu_fe_loss(self, current_dict, B, T, tp):
        """
        frequency: Hz（内部换算成 kHz）
        B: T (内部换算成 mT)
        """
        Ve = tp['Ve_T']  # m^3
        fs = current_dict['fs']
        return self.Cm * ((fs / 1000) ** self.x) *((B * 1000) ** self.y) *(self.Ct0 - self.Ct1 * T + self.Ct2 * (T ** 2)) * Ve * 1000


    def calculate_loss_B(self, current_dict, transformer_params_dict):
        """
        Part2：根据 Part1 输出的 transformer_params_dict + 工况，计算：
            - 铜损 p_cu
            - 磁通密度 B
            - 铁损 p_fe

        transformer_params_dict 需包含字段：
            n, wire_length_p, wire_length_s, wire_area_p, wire_area_s, Ns, Ae, Ve

        返回：
            {'p_cu': ..., 'p_fe': ..., 'B': ...}
        """

        p_cu = self.calcu_cu_loss(current_dict, transformer_params_dict)
        B = self.calcu_B_backward(current_dict, transformer_params_dict)
        p_fe = self.calcu_fe_loss(current_dict, B, self.T, transformer_params_dict)
        p_transformer = p_fe + p_cu

        return {
            'p_cu_T': p_cu,
            'p_fe_T': p_fe,
            'B_T': B,
            'p_transformer': p_transformer,
        }


if __name__ == "__main__":
    # 简单自测：构造一个假的 transformer_params_dict
    # 实际使用时应由 Part1 的 TransformerSelector 输出
    sample_params = {
        'wire_length_p': 1694.7199999999998,   # mm
        'wire_length_s': 2792.08,    # mm
        'wire_area_p': 2.512,        # mm^2
        'wire_area_s': 2.355,        # mm^2
        'Ns': 17,
        'Ae_T': 3.54e-4,                # m^2
        'Ve_T': 3.2214e-5,                # m^3
    }
    current_dict = {
        'fs': 90000,     # Hz
        'v_rms_s': 450,     # V
        'i_rms_p': 20,      # A
        'n': 11 / 12,
    }
    calc = TransformerLoss()
    res = calc.calculate_loss_B(
        current_dict=current_dict,
        transformer_params_dict=sample_params,
    )
    print("Part2 计算结果：")
    print(res)
