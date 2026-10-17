class SwitchLossConstraintChecker:
    def __init__(self, p_limit_single: float = 80.0, max_switches: int = 8):
        """
        :param p_limit_single: 单个器件允许的最大总损耗 [W]
        :param max_switches:   最多检查的开关管数量（默认 s1~s8）
        """
        self.p_limit_single = float(p_limit_single)
        self.max_switches = int(max_switches)

    def verify_constraints(self, p_s_dict: dict) -> bool:
        """
        适配新格式：
        p_s_dict = {
            "per_switch": {
                "s1": {"p_total": ...},
                "s2": {"p_total": ...},
                ...
            },
            "summary": {...}
        }

        逻辑：
        - 提取 s1~s8 中存在的 p_total
        - 取最大值
        - 最大值 > 限值 -> False，否则 True
        - 若没有任何有效开关管损耗数据 -> False
        """
        per_switch = p_s_dict.get("per_switch", {})
        if not isinstance(per_switch, dict):
            return False

        single_losses = []
        for i in range(1, self.max_switches + 1):
            skey = f"s{i}"
            item = per_switch.get(skey)

            if isinstance(item, dict) and ("p_total" in item):
                try:
                    single_losses.append(float(item["p_total"]))
                except (TypeError, ValueError):
                    pass

        if not single_losses:
            return False

        max_single_loss = max(single_losses)
        return max_single_loss <= self.p_limit_single


if __name__ == "__main__":
    # 新格式示例
    p_s_dict = {
        "per_switch": {
            "s1": {"p_total": 30.0},
            "s2": {"p_total": 55.2},
            "s4": {"p_total": 79.9},
        },
        "summary": {
            "p_s_total": 165.1
        }
    }

    checker = SwitchLossConstraintChecker(p_limit_single=80.0)
    ok = checker.verify_constraints(p_s_dict)
    print("功率器件损耗是否满足约束：", ok)
