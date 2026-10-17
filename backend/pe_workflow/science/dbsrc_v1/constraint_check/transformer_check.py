class TransformerConstraintChecker:
    def __init__(
        self,
        constraint_condition: float = 1000,
        b_limit: float | None = None,
        loss_density_limit: float | None = None,
    ):
        if loss_density_limit is not None:
            constraint_condition = loss_density_limit
        self.constraint_condition = float(constraint_condition)
        self.b_s = 0.39
        self.b_limit = float(b_limit) if b_limit is not None else self.b_s * 0.6

    def verify_constraints(self, transformer_params_dict, loss_results):
        """
        Part3：根据 Part2 的损耗 + B，验证：
            - 体积损耗密度是否超过 self.loss_density_limit（kW/m^3）
            - 磁密 B 是否超过 self.b_limit

        transformer_params_dict 需包含：
            volume（mm^3）

        loss_results 需包含：
            p_cu, p_fe, B

        返回：
            ok: bool
            detail: dict（包含损耗密度、B 等信息）
        """
        p_cu = loss_results['p_cu_T']
        p_fe = loss_results['p_fe_T']
        B = loss_results['B_T']

        V_mm3 = transformer_params_dict["V_T"]
        V_m3 = V_mm3 * 1e-9
        loss_density_kw_per_m3 = ((p_cu + p_fe) / 1000) / V_m3

        return loss_density_kw_per_m3 <= self.constraint_condition and B <= self.b_limit


if __name__ == "__main__":
    sample_params = {"V_T": 1.0e6}
    sample_losses = {"p_cu_T": 50.0, "p_fe_T": 20.0, "B_T": 0.2}

    checker = TransformerConstraintChecker()
    ok = checker.verify_constraints(sample_params, sample_losses)
    print("Part3 约束是否满足：", ok)
