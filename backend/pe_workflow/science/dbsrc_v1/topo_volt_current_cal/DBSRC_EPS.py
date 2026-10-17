import numpy as np
import time
import math

import numpy as np
class DBSRC_CurrentModel_EPS:
    def __init__(self, Lr, Cr, fs, n):
        """
        Lr: 谐振电感
        Cr: 谐振电容
        fs: 开关频率
        n: 变比
        """
        self.Lr = Lr
        self.Cr = Cr
        self.fs = fs
        self.n = n

        # 只在初始化时计算一次的常量
        self.T = 1.0 / (fs * np.sqrt(Lr * Cr))
        self.cos025T = 1.0 / np.cos(0.25 * (1.0 / fs) / np.sqrt(Lr * Cr))
        self.k = 1.0 / np.sqrt(Lr / Cr) * self.cos025T

        # time_points 数组，只生成一次，后面复用
        self.time_points = np.linspace(0, 0.5, 1001)

    def solve_D2(self, EPS, mode, Vin, Vout, Pout, D1):
        """
        原边/副边内移相求解 D2（外移相比）的函数
        EPS=1：原边移相，EPS=2：副边移相；mode=1/2：不同的模式
        D1：内移相角，返回 D2：外移相角
        """
        beta = D1  # 内移相比
        Vp = Vin
        Vs = self.n * Vout
        if EPS == 1:  # 原边内移相
            if mode == 1:  # 模态1: 0.5*D1 < D2 < D1 < 0.5
                denominator = 4 * self.Cr * Vp * Vs * self.fs * self.cos025T * np.sin((0.25 - 0.5 * beta) * self.T)
                denominator = np.where(np.abs(denominator) < 1e-12, 1e-12, denominator)
                star = Pout / denominator
                # star = Pout / (4 * self.Cr * Vp * Vs * self.fs * self.cos025T * np.sin((0.25 - 0.5 * beta) * self.T))
                if -1 < star < 1:
                    alfa_solution = np.arcsin(star) / self.T + 0.5 * beta
                    if 0.5 * beta < alfa_solution < beta < 0.5:
                        return alfa_solution
                    else:
                        return None
                else:
                    return None
            elif mode == 2:  # 模态2: D1 < D2 < 0.5
                star = (Pout / (4 * self.Cr * Vp * Vs * self.fs) + 1.0) / (np.cos(0.5 * beta * self.T) * self.cos025T)
                if -1 < star < 1:
                    alfa_solution = - np.arccos(star) / self.T + 0.5 * beta + 0.25
                    if beta < alfa_solution < 0.5:
                        return alfa_solution
                    else:
                        return None
                else:
                    return None
            else:
                return None
        elif EPS == 2:  # 副边内移相
            if mode == 1:  # 模态1: -0.5*D1 < D2 < 0
                denominator = 4 * self.Cr * Vp * Vs * self.fs * self.cos025T * np.sin((0.25 - 0.5 * beta) * self.T)
                denominator = np.where(np.abs(denominator) < 1e-12, 1e-12, denominator)
                star = Pout / denominator
                # star = Pout / (4 * self.Cr * Vp * Vs * self.fs * self.cos025T * np.sin((0.25 - 0.5 * beta) * self.T))
                if -1 < star < 1:
                    alfa_solution = np.arcsin(star) / self.T - 0.5 * beta
                    if -0.5 * beta < alfa_solution < 0:
                        return alfa_solution
                    else:
                        return None
                else:
                    return None
            elif mode == 2:  # 模态2: 0 < D2 < 0.5 - D1
                star = (Pout / (4 * self.Cr * Vp * Vs * self.fs) + 1.0) / (np.cos(0.5 * beta * self.T) * self.cos025T)
                if -1 < star < 1:
                    alfa_solution = - np.arccos(star) / self.T - 0.5 * beta + 0.25
                    if 0 < alfa_solution < 0.5 - beta:
                        return alfa_solution
                    else:
                        return None
                else:
                    return None
            else:
                return None
        else:
            return None

    def calculate_D2(self, Vin, Vout, Pout):
        """
        遍历 D1 求解 D2：对每个 D1，先尝试模态1，无解再尝试模态2。
        将所有可行解以列表打包输出：
          [(D1, D2, EPS, mode), ...]
        若所有 D1 都无解，则返回 None。
        """
        results = []

        for D1 in np.arange(0.0, 0.5, 0.01):
            if Vin > self.n * Vout:
                # 原边内移相 EPS=1
                EPS = 1
                D2 = self.solve_D2(EPS, 1, Vin, Vout, Pout, D1)
                if D2 is not None:
                    results.append((D1, D2, EPS, 1))
                    continue

                D2 = self.solve_D2(EPS, 2, Vin, Vout, Pout, D1)
                if D2 is not None:
                    results.append((D1, D2, EPS, 2))
                    continue

            else:
                # 副边内移相 EPS=2
                EPS = 2
                D2 = self.solve_D2(EPS, 1, Vin, Vout, Pout, D1)
                if D2 is not None:
                    results.append((D1, D2, EPS, 1))
                    continue

                D2 = self.solve_D2(EPS, 2, Vin, Vout, Pout, D1)
                if D2 is not None:
                    results.append((D1, D2, EPS, 2))
                    continue

            # 走到这里说明该 D1 下两种 mode 都无解，直接跳过、不输出

        return results if len(results) > 0 else None

    def calcu_PE(self, EPS, mode, Vin, Vout, D1, D2):
        """
        计算功率，逻辑与原始相同。
        """
        beta = D1
        alfa = D2
        Vp = Vin
        Vs = self.n * Vout
        if EPS == 1:
            if mode == 1:
                return 4 * self.Cr * Vp * Vs * self.fs * self.cos025T * np.sin((0.25 - 0.5 * beta) * self.T) * np.sin((alfa - 0.5 * beta) * self.T)
            elif mode == 2:
                return 4 * self.Cr * Vp * Vs * self.fs * (np.cos(0.5 * beta * self.T) * np.cos((0.25 - alfa + 0.5 * beta) * self.T) * self.cos025T - 1.0)
        elif EPS == 2:
            if mode == 1:
                return 4 * self.Cr * Vp * Vs * self.fs * self.cos025T * np.sin((0.25 - 0.5 * beta) * self.T) * np.sin((alfa + 0.5 * beta) * self.T)
            elif mode == 2:
                return 4 * self.Cr * Vp * Vs * self.fs * (np.cos(0.5 * beta * self.T) * np.cos((0.25 - alfa - 0.5 * beta) * self.T) * self.cos025T - 1.0)

    def calculate_key_params(self, EPS, mode, Vin, Vout, beta, alfa):
        """
        向量化实现原 calculate_current 的逻辑。
        根据 (EPS, mode) 分别套用原代码的分段公式与开关管统计。
        beta=D1,alfa=D2
        """
        # 1) 准备：常数、时间数组、输出数组
        Vp = Vin
        Vs = self.n * Vout
        t_arr = self.time_points  # 由 __init__ 中预先定义: np.linspace(0, 0.5, 1001)
        iL = np.zeros_like(t_arr)

        # 为了找边界对应的下标，用一个小函数
        def idx_for_boundary(boundary):
            # 找到满足 t <= boundary 的最后一个下标
            idx_ = np.where(t_arr <= boundary)[0]
            if len(idx_):
                return idx_[-1]
            return 0

        # 2) 根据 (EPS, mode) 确定分段边界 + iL 分段公式 + 开关管正反向划分
        #    （完全仿照原 calculate_current 中的 if-else 结构）

        # 定义要统计的列表（最终用掩码方式收集）
        s1_forward_vals, s1_backward_vals = [], []
        s4_forward_vals, s4_backward_vals = [], []
        s5_forward_vals, s5_backward_vals = [], []
        s8_forward_vals, s8_backward_vals = [], []

        # 几个开关导通时电流
        i_sw1_on = 0.0
        i_sw4_on = 0.0
        i_sw5_on = 0.0
        i_sw8_on = 0.0

        # ---- EPS=1, mode=1 ----
        if EPS == 1 and mode == 1:
            # 2.1. 分段边界
            boundary1 = (beta - alfa)  # 第一个转折点
            boundary2 = (0.5 - alfa)  # 第二个转折点

            mask1 = (t_arr <= boundary1)
            mask2 = (t_arr > boundary1) & (t_arr <= boundary2)
            mask3 = (t_arr > boundary2)

            # 2.2. 分段电流公式（对应原 code: if t <=(beta - alfa) ... elif t<=(0.5 - alfa) ... else...）
            iL[mask1] = self.k * (-Vp * np.sin((0.25 - 0.5 * beta) * self.T) * np.cos((t_arr[mask1] + alfa - 0.5 * beta) * self.T) - Vs * np.sin((t_arr[mask1] - 0.25) * self.T))
            iL[mask2] = self.k * (Vp * np.sin((t_arr[mask2] - 0.25 + alfa - 0.5 * beta) * self.T) * np.cos(0.5 * beta * self.T) - Vs * np.sin((t_arr[mask2] - 0.25) * self.T))
            iL[mask3] = self.k * (Vp * np.sin((0.25 - 0.5 * beta) * self.T) * np.cos((t_arr[mask3] - 0.5 + alfa - 0.5 * beta) * self.T) - Vs * np.sin((t_arr[mask3] - 0.25) * self.T))

            # 2.3. 开关管正反向统计
            iL_abs = np.abs(iL)

            # s1:
            #   if t <= (0.5 - alfa):
            #       if iL>0 => forward, else => backward
            #   else:
            #       if iL<0 => forward, else => backward
            mask_s1_first = (t_arr <= boundary2)
            mask_s1_second = (t_arr > boundary2)
            forward_mask = (iL > 0)
            backward_mask = (iL < 0)

            s1_forward_vals = np.concatenate([iL_abs[mask_s1_first & forward_mask], iL_abs[mask_s1_second & backward_mask]])
            s1_backward_vals = np.concatenate([iL_abs[mask_s1_first & backward_mask], iL_abs[mask_s1_second & forward_mask]])

            # s4:
            #   if t <= (beta - alfa):
            #       if iL<0 => forward, else => backward
            #   else:
            #       if iL>0 => forward, else => backward
            mask_s4_first = (t_arr <= boundary1)
            mask_s4_second = (t_arr > boundary1)
            s4_forward_vals = np.concatenate([iL_abs[mask_s4_first & (iL < 0)], iL_abs[mask_s4_second & (iL > 0)]])
            s4_backward_vals = np.concatenate([iL_abs[mask_s4_first & (iL > 0)], iL_abs[mask_s4_second & (iL < 0)]])

            # s5: 整个区间，iL<0 => forward, else => backward
            s5_forward_vals = iL_abs[iL < 0]
            s5_backward_vals = iL_abs[iL > 0]

            # s8 = s5
            s8_forward_vals = s5_forward_vals
            s8_backward_vals = s5_backward_vals

            # 2.4. i_swX_on
            t0_index = 0
            t1_index = idx_for_boundary(boundary1)
            t2_index = idx_for_boundary(boundary2)

            i_sw1_on = - iL[t2_index]
            i_sw4_on = iL[t1_index]
            i_sw5_on = iL[t0_index]
            i_sw8_on = iL[t0_index]

        # ---- EPS=1, mode=2 ----
        elif EPS == 1 and mode == 2:
            # 分段边界
            boundary1 = (0.5 - alfa)
            boundary2 = (0.5 - alfa + beta)

            mask1 = (t_arr <= boundary1)
            mask2 = (t_arr > boundary1) & (t_arr <= boundary2)
            mask3 = (t_arr > boundary2)

            # 分段电流公式
            iL[mask1] = self.k * (Vp * np.cos(0.5 * beta * self.T) * np.sin((t_arr[mask1] - 0.25 + alfa - 0.5 * beta) * self.T) - Vs * np.sin((t_arr[mask1] - 0.25) * self.T))
            iL[mask2] = self.k * (Vp * np.cos((t_arr[mask2] - 0.5 + alfa - 0.5 * beta) * self.T) * np.sin((0.25 - 0.5 * beta) * self.T) - Vs * np.sin((t_arr[mask2] - 0.25) * self.T))
            iL[mask3] = self.k * (- Vp * np.cos(0.5 * beta * self.T) * np.sin((t_arr[mask3] - 0.75 + alfa - 0.5 * beta) * self.T) - Vs * np.sin((t_arr[mask3] - 0.25) * self.T))

            iL_abs = np.abs(iL)
            forward_mask = (iL > 0)
            backward_mask = (iL < 0)

            # s1:
            #   if t <= (0.5 - alfa):
            #       if iL>0 => forward else => backward
            #   else:
            #       if iL<0 => forward else => backward
            mask_s1_first = (t_arr <= boundary1)
            mask_s1_second = (t_arr > boundary1)
            s1_forward_vals = np.concatenate([iL_abs[mask_s1_first & forward_mask], iL_abs[mask_s1_second & backward_mask]])
            s1_backward_vals = np.concatenate([iL_abs[mask_s1_first & backward_mask], iL_abs[mask_s1_second & forward_mask]])

            # s4:
            #   if t <= (0.5 - alfa + beta):
            #       if iL>0 => forward else => backward
            #   else:
            #       if iL<0 => forward else => backward
            mask_s4_first = (t_arr <= boundary2)
            mask_s4_second = (t_arr > boundary2)
            s4_forward_vals = np.concatenate([iL_abs[mask_s4_first & forward_mask], iL_abs[mask_s4_second & backward_mask]])
            s4_backward_vals = np.concatenate([iL_abs[mask_s4_first & backward_mask], iL_abs[mask_s4_second & forward_mask]])

            # s5: 整个区间 iL<0 => forward, else => backward
            s5_forward_vals = iL_abs[iL < 0]
            s5_backward_vals = iL_abs[iL > 0]

            # s8 = s5
            s8_forward_vals = s5_forward_vals
            s8_backward_vals = s5_backward_vals

            # i_swX_on
            t0_index = 0
            t1_index = idx_for_boundary(boundary1)
            t2_index = idx_for_boundary(boundary2)

            i_sw1_on = - iL[t1_index]
            i_sw4_on = - iL[t2_index]
            i_sw5_on = iL[t0_index]
            i_sw8_on = iL[t0_index]

        # ---- EPS=2, mode=1 ----
        elif EPS == 2 and mode == 1:
            # 分段边界
            boundary1 = (0.5 - beta)
            boundary2 = (0.5 - beta - alfa)

            mask1 = (t_arr <= boundary1)
            mask2 = (t_arr > boundary1) & (t_arr <= boundary2)
            mask3 = (t_arr > boundary2)

            iL[mask1] = self.k * (Vp * np.sin((t_arr[mask1] - 0.25 + alfa + beta) * self.T) - Vs * np.sin((t_arr[mask1] - 0.25 + 0.5 * beta) * self.T) * np.cos(0.5 * beta * self.T))
            iL[mask2] = self.k * (Vp * np.sin((t_arr[mask2] - 0.25 + alfa + beta) * self.T) - Vs * np.sin((0.25 - 0.5 * beta) * self.T) * np.cos((t_arr[mask2] - 0.5 + 0.5 * beta) * self.T))
            iL[mask3] = self.k * (- Vp * np.sin((t_arr[mask3] - 0.75 + alfa + beta) * self.T) - Vs * np.sin((0.25 - 0.5 * beta) * self.T) * np.cos((t_arr[mask3] - 0.5 + 0.5 * beta) * self.T))

            iL_abs = np.abs(iL)
            forward_mask = (iL > 0)
            backward_mask = (iL < 0)

            # s1:
            #   if t <= (0.5 - alfa - beta):
            #       if iL>0 => forward else => backward
            #   else:
            #       if iL<0 => forward else => backward
            boundary_s1 = (0.5 - alfa - beta)
            mask_s1_first = (t_arr <= boundary_s1)
            mask_s1_second = (t_arr > boundary_s1)
            s1_forward_vals = np.concatenate([iL_abs[mask_s1_first & forward_mask], iL_abs[mask_s1_second & backward_mask]])
            s1_backward_vals = np.concatenate([iL_abs[mask_s1_first & backward_mask], iL_abs[mask_s1_second & forward_mask]])

            # s5:
            #   if t <= (0.5 - beta):
            #       if iL<0 => forward else => backward
            #   else:
            #       if iL>0 => forward else => backward
            mask_s5_first = (t_arr <= boundary1)
            mask_s5_second = (t_arr > boundary1)
            s5_forward_vals = np.concatenate([iL_abs[mask_s5_first & (iL < 0)], iL_abs[mask_s5_second & (iL > 0)]])
            s5_backward_vals = np.concatenate([iL_abs[mask_s5_first & (iL > 0)], iL_abs[mask_s5_second & (iL < 0)]])

            # s8: 整个区间 iL<0 => forward, else => backward
            s8_forward_vals = iL_abs[iL < 0]
            s8_backward_vals = iL_abs[iL > 0]

            # 原代码里: s4 = s1
            s4_forward_vals = s1_forward_vals
            s4_backward_vals = s1_backward_vals

            # i_swX_on
            t0_index = 0
            t1_index = idx_for_boundary(boundary1)
            t2_index = idx_for_boundary(boundary2)

            # 原代码:
            # i_sw1_on = -iL[t2], i_sw4_on = -iL[t2],
            # i_sw5_on = -iL[t1], i_sw8_on = iL[t0]
            i_sw1_on = -iL[t2_index]
            i_sw4_on = -iL[t2_index]
            i_sw5_on = -iL[t1_index]
            i_sw8_on = iL[t0_index]

        # ---- EPS=2, mode=2 ----
        elif EPS == 2 and mode == 2:
            # 分段边界
            boundary1 = (0.5 - alfa - beta)
            boundary2 = (0.5 - beta)

            mask1 = (t_arr <= boundary1)
            mask2 = (t_arr > boundary1) & (t_arr <= boundary2)
            mask3 = (t_arr > boundary2)

            iL[mask1] = self.k * (Vp * np.sin((t_arr[mask1] - 0.25 + alfa + beta) * self.T) - Vs * np.sin((t_arr[mask1] - 0.25 + 0.5 * beta) * self.T) * np.cos(0.5 * beta * self.T))
            iL[mask2] = self.k * (-Vp * np.sin((t_arr[mask2] - 0.75 + alfa + beta) * self.T) - Vs * np.sin((t_arr[mask2] - 0.25 + 0.5 * beta) * self.T) * np.cos(0.5 * beta * self.T))
            iL[mask3] = self.k * (-Vp * np.sin((t_arr[mask3] - 0.75 + alfa + beta) * self.T) - Vs * np.sin((0.25 - 0.5 * beta) * self.T) * np.cos((t_arr[mask3] - 0.5 + 0.5 * beta) * self.T))

            iL_abs = np.abs(iL)
            forward_mask = (iL > 0)
            backward_mask = (iL < 0)

            # s1:
            #   if t <= (0.5 - alfa - beta):
            #       if iL>0 => forward else => backward
            #   else:
            #       if iL<0 => forward else => backward
            mask_s1_first = (t_arr <= boundary1)
            mask_s1_second = (t_arr > boundary1)
            s1_forward_vals = np.concatenate([iL_abs[mask_s1_first & forward_mask], iL_abs[mask_s1_second & backward_mask]])
            s1_backward_vals = np.concatenate([iL_abs[mask_s1_first & backward_mask], iL_abs[mask_s1_second & forward_mask]])

            # s5:
            #   if t <= (0.5 - beta):
            #       if iL<0 => forward else => backward
            #   else:
            #       if iL>0 => forward else => backward
            mask_s5_first = (t_arr <= boundary2)
            mask_s5_second = (t_arr > boundary2)
            s5_forward_vals = np.concatenate([iL_abs[mask_s5_first & (iL < 0)], iL_abs[mask_s5_second & (iL > 0)]])
            s5_backward_vals = np.concatenate([iL_abs[mask_s5_first & (iL > 0)], iL_abs[mask_s5_second & (iL < 0)]])

            # s8: 整个区间 iL<0 => forward, else => backward
            s8_forward_vals = iL_abs[iL < 0]
            s8_backward_vals = iL_abs[iL > 0]

            # 原代码里: s4 = s1
            s4_forward_vals = s1_forward_vals
            s4_backward_vals = s1_backward_vals

            # i_swX_on
            t0_index = 0
            t1_index = idx_for_boundary(boundary1)
            t2_index = idx_for_boundary(boundary2)

            # 原代码: i_sw1_on = -iL[t1], i_sw4_on = -iL[t1], i_sw5_on = -iL[t2], i_sw8_on = iL[t0]
            i_sw1_on = - iL[t1_index]
            i_sw4_on = - iL[t1_index]
            i_sw5_on = - iL[t2_index]
            i_sw8_on = iL[t0_index]

        else:
            # 若遇到EPS, mode不在原逻辑中的情况，直接返回空结果
            return None

        # 3) 计算结果（平均值、占空比、最大/有效值等）
        s1_forward_len = len(s1_forward_vals)
        s1_backward_len = len(s1_backward_vals)
        s4_forward_len = len(s4_forward_vals)
        s4_backward_len = len(s4_backward_vals)
        s5_forward_len = len(s5_forward_vals)
        s5_backward_len = len(s5_backward_vals)
        s8_forward_len = len(s8_forward_vals)
        s8_backward_len = len(s8_backward_vals)

        i_mean_s1_forward = np.mean(s1_forward_vals) if s1_forward_len else 0
        i_mean_s1_backward = np.mean(s1_backward_vals) if s1_backward_len else 0
        i_mean_s4_forward = np.mean(s4_forward_vals) if s4_forward_len else 0
        i_mean_s4_backward = np.mean(s4_backward_vals) if s4_backward_len else 0
        i_mean_s5_forward = np.mean(s5_forward_vals) if s5_forward_len else 0
        i_mean_s5_backward = np.mean(s5_backward_vals) if s5_backward_len else 0
        i_mean_s8_forward = np.mean(s8_forward_vals) if s8_forward_len else 0
        i_mean_s8_backward = np.mean(s8_backward_vals) if s8_backward_len else 0

        total_points = len(t_arr)
        s1_forward_ratio = s1_forward_len / total_points
        s4_forward_ratio = s4_forward_len / total_points
        s5_forward_ratio = s5_forward_len / total_points
        s8_forward_ratio = s8_forward_len / total_points

        ip_max = max(abs(i_sw1_on), abs(i_sw4_on), abs(i_sw5_on), abs(i_sw8_on))
        ip_rms = np.sqrt(np.mean(iL ** 2))

        # 4) 打包返回字典，与原代码字段一致
        key_params_dict = {
            'i_rms_p': ip_rms,  # 变压器原边电流有效值
            'v_rms_p': Vin,  # 变压器原边电压
            'v_rms_s': Vout,  # 变压器副边电压

            'inductance1': self.Lr,
            'i_rms_ind1': ip_rms,    #电感电流有效值
            'i_peak_ind1': ip_max,   #电感电流峰值

            'i_rms_c1': ip_rms,     #电容电流有效值

            'i_rms_s1': ip_rms / math.sqrt(2),
            'i_rms_s2': ip_rms / math.sqrt(2),
            'i_rms_s3': ip_rms / math.sqrt(2),
            'i_rms_s4': ip_rms / math.sqrt(2),
            'i_rms_s5': self.n * ip_rms / math.sqrt(2),
            'i_rms_s6': self.n * ip_rms / math.sqrt(2),
            'i_rms_s7': self.n * ip_rms / math.sqrt(2),
            'i_rms_s8': self.n * ip_rms / math.sqrt(2),          # 开关管1~8的电流有效值
            'i_on_s1': i_sw1_on,
            'i_on_s2': i_sw1_on,
            'i_on_s3': i_sw4_on,
            'i_on_s4': i_sw4_on,
            'i_on_s5': - (self.n * i_sw5_on),
            'i_on_s6': - (self.n * i_sw5_on),
            'i_on_s7': - (self.n * i_sw8_on),
            'i_on_s8': - (self.n * i_sw8_on),              # 开关管1~8的开通电流值
            'i_off_s1': -i_sw1_on,
            'i_off_s2': -i_sw1_on,
            'i_off_s3': -i_sw4_on,
            'i_off_s4': -i_sw4_on,
            'i_off_s5': self.n * i_sw5_on,
            'i_off_s6': self.n * i_sw5_on,
            'i_off_s7': self.n * i_sw8_on,
            'i_off_s8': self.n * i_sw8_on,                 # 开关管1~8的关断电流值

            'i_mean_s1_forward': i_mean_s1_forward,
            'i_mean_s1_backward': i_mean_s1_backward,
            's1_forward_ratio': s1_forward_ratio,
            'i_mean_s2_forward': i_mean_s1_forward,
            'i_mean_s2_backward': i_mean_s1_backward,
            's2_forward_ratio': s1_forward_ratio,

            'i_mean_s3_forward': i_mean_s4_forward,
            'i_mean_s3_backward': i_mean_s4_backward,
            's3_forward_ratio': s4_forward_ratio,
            'i_mean_s4_forward': i_mean_s4_forward,
            'i_mean_s4_backward': i_mean_s4_backward,
            's4_forward_ratio': s4_forward_ratio,

            'i_mean_s5_forward': self.n * i_mean_s5_forward,
            'i_mean_s5_backward': self.n * i_mean_s5_backward,
            's5_forward_ratio': s5_forward_ratio,
            'i_mean_s6_forward': self.n * i_mean_s5_forward,
            'i_mean_s6_backward': self.n * i_mean_s5_backward,
            's6_forward_ratio': s5_forward_ratio,

            'i_mean_s7_forward': self.n * i_mean_s8_forward,
            'i_mean_s7_backward': self.n * i_mean_s8_backward,
            's7_forward_ratio': s8_forward_ratio,
            'i_mean_s8_forward': self.n * i_mean_s8_forward,
            'i_mean_s8_backward': self.n * i_mean_s8_backward,
            's8_forward_ratio': s8_forward_ratio,                      # 开关管1~8的正反向电流平均值及正向电流比率

            "v_s1": Vin,
            'v_s2': Vin,
            "v_s3": Vin,
            'v_s4': Vin,       # 原边开关管电压
            "v_s5": Vout,
            "v_s6": Vout,
            "v_s7": Vout,
            "v_s8": Vout,      # 副边开关管电压

            "v_peak_filter_in": Vin,    # 输入侧滤波电容电压
            "v_peak_filter_out": Vout,   # 输出侧滤波电容电压

            'capacitance1': self.Cr,
            'v_peak_c1': 1.5 * max(Vin, Vout),   # 粗略计算谐振电容的电压

            'fs': self.fs,
            'n': self.n
        }

        return key_params_dict

