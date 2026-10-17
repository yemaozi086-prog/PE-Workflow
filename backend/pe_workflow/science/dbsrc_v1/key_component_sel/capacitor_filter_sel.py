# 滤波电容参数表
# 单位：uF、mm^3、元

filter_capacitance_dict = {
    "2": {"volume": 5184, "cost": 2.8},
    "10": {"volume": 15360, "cost": 6.8},
    "20": {"volume": 33600, "cost": 15},
}


def select_capacitance(C_value):
    """
    根据所需电容值选择滤波电容组合。

    参数:
        C_value: 所需电容值，单位 F

    返回:
        selected_filter_capacitance:
        {
            "20": {"num": 2},
            "10": {"num": 1},
            "2": {"num": 0}
        }
    """
    C_value_uF = 1.5 * C_value * 1e6  # 给滤波电容 1.5 倍裕量，F -> uF

    selected_filter_capacitance = {}

    filter_capacitance_list = sorted(
        [int(c) for c in filter_capacitance_dict.keys()],
        reverse=True
    )

    for index, filter_capacitance in enumerate(filter_capacitance_list):
        is_last = index == len(filter_capacitance_list) - 1

        if is_last and C_value_uF > 0:
            filter_capacitance_num = int(C_value_uF / filter_capacitance)
            if C_value_uF % filter_capacitance != 0:
                filter_capacitance_num += 1
        else:
            filter_capacitance_num = int(C_value_uF / filter_capacitance)

        selected_filter_capacitance[str(filter_capacitance)] = {
            "num": filter_capacitance_num
        }

        C_value_uF -= filter_capacitance * filter_capacitance_num

    return selected_filter_capacitance


def calcu_capacitance_volume(selected_filter_capacitance):
    """
    根据电容组合计算总体积。
    单位：mm^3
    """
    volume = 0

    for filter_capacitance, params in selected_filter_capacitance.items():
        volume += (
            filter_capacitance_dict[filter_capacitance]["volume"]
            * params["num"]
        )

    return volume


def calcu_capacitance_cost(selected_filter_capacitance):
    """
    根据电容组合计算总成本。
    单位：元
    """
    cost = 0

    for filter_capacitance, params in selected_filter_capacitance.items():
        cost += (
            filter_capacitance_dict[filter_capacitance]["cost"]
            * params["num"]
        )

    return cost


def calcu_selected_capacitance_value(selected_filter_capacitance):
    """
    根据电容组合计算实际选出的总电容值。
    单位：uF
    """
    selected_capacitance_value = 0

    for filter_capacitance, params in selected_filter_capacitance.items():
        selected_capacitance_value += int(filter_capacitance) * params["num"]

    return selected_capacitance_value


def select_capacitance_with_metrics(C_value):
    """
    输入所需电容值后，直接输出电容组合、实际选取容量、总体积和总成本。

    参数:
        C_value: 所需电容值，单位 F

    返回:
        result:
        {
            "required_capacitance_uF": ...,
            "selected_capacitance_uF": ...,
            "selected_capacitance": ...,
            "volume": ...,
            "cost": ...
        }
    """
    required_capacitance_uF = 1.5 * C_value * 1e6

    selected_filter_capacitance = select_capacitance(C_value)

    selected_capacitance_uF = calcu_selected_capacitance_value(
        selected_filter_capacitance
    )

    volume = calcu_capacitance_volume(selected_filter_capacitance)
    cost = calcu_capacitance_cost(selected_filter_capacitance)

    result = {
        "required_capacitance_uF": required_capacitance_uF,
        "selected_capacitance_uF": selected_capacitance_uF,
        "selected_capacitance": selected_filter_capacitance,
        "volume": volume,
        "cost": cost,
    }

    return result


if __name__ == "__main__":
    # 注意：这里输入单位是 F
    # 例如 35e-6 表示 35 uF
    result = select_capacitance_with_metrics(35e-6)

    print(result)