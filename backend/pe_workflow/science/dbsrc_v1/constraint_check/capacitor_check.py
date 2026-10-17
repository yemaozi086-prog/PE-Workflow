class CapacitorLossConstraintChecker:
    def __init__(self, p_cap_limit: float = 50.0):
        self.p_cap_limit = float(p_cap_limit)

    def verify_constraints(self, loss_dict: dict) -> bool:
        cap_losses = []
        for k, v in loss_dict.items():
            if k.startswith("cap") and k.endswith("_loss"):
                try:
                    cap_losses.append(float(v))
                except (TypeError, ValueError):
                    return False

        return max(cap_losses) <= self.p_cap_limit
