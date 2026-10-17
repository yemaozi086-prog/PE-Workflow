class InductorLoss:
    def __init__(self):
        # 铜线电阻率（Ω*mm^2/m）
        self.r_ohm = 0.0185

        # 磁芯铁损系数（Steinmetz-like）
        self.Cm = 6.7088e-7
        self.x = 1.4164
        self.y = 2.5705
        self.Ct0 = 2.3359
        self.Ct1 = 0.03215
        self.Ct2 = 0.0001768
        self.T = 100

        # 铜损修正系数（考虑附加损耗）
        self.k_cu = 1.5

    # ----------------- 通用工具 -----------------
    def _to_float(self, v):
        try:
            v = float(v)
        except Exception:
            return None

        # 不依赖 numpy
        if v != v:  # NaN
            return None
        if v == float("inf") or v == float("-inf"):
            return None
        return v

    def _get_fs(self, current_dict):
        """兼容 fs / fs_min"""
        fs = self._to_float(current_dict.get("fs", None))
        if fs is None:
            fs = self._to_float(current_dict.get("fs_min", None))
        return fs

    # ----------------- 单个电感计算 -----------------
    def calcu_cu_loss_one(self, i_rms, one_inductor_params):
        """
        铜损：P = I_rms^2 * R * k
        R = rho * l / A
        l: mm -> m
        """
        wire_length_L = self._to_float(one_inductor_params.get("wire_length_L", None))  # mm
        wire_area_L = self._to_float(one_inductor_params.get("wire_area_L", None))      # mm^2

        if wire_length_L is None or wire_area_L is None or wire_area_L <= 0:
            return None

        r_L = self.r_ohm * (wire_length_L * 1e-3) / wire_area_L
        # print(r_L)
        p_cu = (i_rms ** 2) * r_L * self.k_cu
        return p_cu

    def calcu_B_backward_one(self, inductance, i_peak, one_inductor_params):
        """
        反算磁通密度（单位 T）:
            B = L * I_peak / (N * Ae)
        约定:
            L 单位为 H，Ae 单位为 m^2
        """
        NL = self._to_float(one_inductor_params.get("NL", None))
        Ae_L = self._to_float(one_inductor_params.get("Ae_L", None))

        if NL is None or Ae_L is None or NL <= 0 or Ae_L <= 0:
            return None

        B = (inductance * i_peak) / (NL * Ae_L)
        return B

    def calcu_fe_loss_one(self, fs, B, T, one_inductor_params):
        """
        铁损：
            P_fe = Cm * (fs/kHz)^x * (B/mT)^y * Ct(T) * Ve * 1000
        其中 Ve 单位 m^3
        """
        Ve_L = self._to_float(one_inductor_params.get("Ve_L", None))
        if Ve_L is None or Ve_L <= 0:
            return None

        Ct = self.Ct0 - self.Ct1 * T + self.Ct2 * (T ** 2)
        p_fe = self.Cm * ((fs / 1000.0) ** self.x) * ((abs(B) * 1000.0) ** self.y) * Ct * Ve_L * 1000.0
        return p_fe

    # ----------------- 多电感总入口 -----------------
    def calculate_loss_B(self, current_dict, inductor_params_dict, strict=True, max_groups=5):
        """
        输入:
          current_dict 至少包含（按需）:
            inductance1..5
            i_rms_ind1..5
            i_peak_ind1..5
            fs 或 fs_min

          inductor_params_dict 推荐结构:
          {
            "inductors": {
              "inductor1": {...},
              "inductor2": {...},
              ...
            }
          }

        strict=True 时：
          - 若 current_dict 中存在 inductance{i}（表示该组需要设计）
            但 inductor_params_dict 中没有 inductor{i}，返回 None
          - 某组输入缺失/非法，也返回 None

        返回:
          {
            "inductors": {
              "inductor1": {"p_cu_L":..., "p_fe_L":..., "B_L":..., "p_inductor":...},
              ...
            },
            "p_cu_L_total": ...,
            "p_fe_L_total": ...,
            "p_inductor_total": ...
          }
        """
        # 读取 fs
        fs = self._get_fs(current_dict)
        if fs is None or fs <= 0:
            return None

        # 兼容两种输入：外套字典 / 单个字典
        if isinstance(inductor_params_dict, dict) and "inductors" in inductor_params_dict:
            group_dict = inductor_params_dict.get("inductors", {})
        else:
            # 兼容旧式：直接传一个单电感字典
            group_dict = {"inductor1": inductor_params_dict}

        out = {
            "inductors": {},
            "p_cu_L_total": 0.0,
            "p_fe_L_total": 0.0,
            "p_inductor_total": 0.0,
        }

        for i in range(1, max_groups + 1):
            kL = f"inductance{i}"
            kIrms = f"i_rms_ind{i}"
            kIpk = f"i_peak_ind{i}"
            kInd = f"inductor{i}"

            # 是否“需要”这组电感（由是否给了 inductance{i} 判断）
            need_this_group = (kL in current_dict) and (current_dict.get(kL) is not None)

            # 参数字典里是否有该组设计结果
            one_params = group_dict.get(kInd, None)

            if need_this_group and one_params is None:
                # 有对应电感值，但没有设计出来 -> 返回 None（你的要求）
                return None

            # 不需要且没有 -> 跳过
            if (not need_this_group) and one_params is None:
                continue

            # 拿输入量： current_dict
            inductance = self._to_float(current_dict.get(kL, None))

            i_rms = self._to_float(current_dict.get(kIrms, None))

            i_peak = self._to_float(current_dict.get(kIpk, None))

            # 严格模式：任何关键输入缺失都失败
            if inductance is None or i_rms is None or i_peak is None:
                if strict:
                    return None
                else:
                    continue

            # 计算单组损耗与磁密
            p_cu = self.calcu_cu_loss_one(i_rms, one_params)
            B = self.calcu_B_backward_one(inductance, i_peak, one_params)
            if p_cu is None or B is None:
                if strict:
                    return None
                else:
                    continue

            p_fe = self.calcu_fe_loss_one(fs, B, self.T, one_params)
            if p_fe is None:
                if strict:
                    return None
                else:
                    continue

            p_ind = p_cu + p_fe

            out["inductors"][kInd] = {
                "p_cu_L": p_cu,
                "p_fe_L": p_fe,
                "B_L": B,
                "p_inductor": p_ind,
            }

            out["p_cu_L_total"] += p_cu
            out["p_fe_L_total"] += p_fe
            out["p_inductor_total"] += p_ind

        # 一个都没算出来
        if len(out["inductors"]) == 0:
            return None

        return out


if __name__ == "__main__":
    # 示例：2个电感
    current_dict = {
        "fs": 100000,
        "inductance1": 10e-6,
        "i_rms_ind1": 15.0,
        "i_peak_ind1": 20.0,
        # "inductance2": 80e-6,
        # "i_rms_ind2": 10.0,
        # "i_peak_ind2": 16.0,
    }

    inductor_params_dict = {
        "inductors": {
            "inductor1": {
                "wire_length_L": 690.8,  # mm
                "wire_area_L": 1.963,       # mm^2
                "NL": 11,
                "Ae_L": 8.099999999999999e-05,           # m^2
                "Ve_L": 4.155e-06,           # m^3
            },
            # "inductor2": {
            #     "wire_length_L": 900.0,
            #     "wire_area_L": 1.8,
            #     "NL": 18,
            #     "Ae_L": 2.1e-4,
            #     "Ve_L": 2.6e-5,
            # }
        }
    }

    calc = InductorLoss()
    res = calc.calculate_loss_B(current_dict, inductor_params_dict, strict=True)
    print(res)
