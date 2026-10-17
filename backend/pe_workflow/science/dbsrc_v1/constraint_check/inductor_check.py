class InductorConstraintChecker:
    def __init__(
        self,
        constraint_condition: float = 1000,
        b_limit: float | None = None,
        loss_density_limit: float | None = None,
    ):
        if loss_density_limit is not None:
            constraint_condition = loss_density_limit
        self.constraint_condition = float(constraint_condition)
        self.loss_density_limit = self.constraint_condition
        self.b_s = 0.39
        self.b_limit = float(b_limit) if b_limit is not None else self.b_s * 0.6

    def _to_float(self, x):
        try:
            v = float(x)
        except Exception:
            return None
        if v != v or v == float("inf") or v == float("-inf"):
            return None
        return v

    def verify_constraints(self, inductor_params_dict, loss_results):
        if isinstance(inductor_params_dict, dict) and "inductors" in inductor_params_dict:
            params_groups = inductor_params_dict.get("inductors", {})
        elif isinstance(inductor_params_dict, dict):
            params_groups = {"inductor1": inductor_params_dict}
        else:
            return False

        if isinstance(loss_results, dict) and "inductors" in loss_results:
            loss_groups = loss_results.get("inductors", {})
        elif isinstance(loss_results, dict):
            loss_groups = {"inductor1": loss_results}
        else:
            return False

        if not params_groups:
            return False

        for key, one_param in params_groups.items():
            one_loss = loss_groups.get(key, None)
            if one_loss is None:
                return False

            V_mm3 = self._to_float(one_param.get("V_L", None))
            p_cu = self._to_float(one_loss.get("p_cu_L", None))
            p_fe = self._to_float(one_loss.get("p_fe_L", None))
            B = self._to_float(one_loss.get("B_L", None))

            if V_mm3 is None or V_mm3 <= 0:
                return False
            if p_cu is None or p_fe is None or B is None:
                return False

            V_m3 = V_mm3 * 1e-9
            loss_density_kw_per_m3 = ((p_cu + p_fe) / 1000.0) / V_m3

            if loss_density_kw_per_m3 > self.loss_density_limit:
                return False
            if B > self.b_limit:
                return False

        return True
