import numpy as np


class ConverterMetricsCalculator:
    """
    变换器性能指标计算器。

    主要功能：
    1. 从环境对象中读取器件选型结果；
    2. 整理器件级体积、成本、损耗等指标；
    3. 计算变换器级总指标，如加权效率、总体积、总成本。
    """

    def __init__(self, weight_matrix=None):
        self.weight_matrix = weight_matrix

    @staticmethod
    def _safe_get(data_dict, key, default=0.0):
        """
        安全读取字典中的数值字段。
        如果字典为空、字段不存在或字段值为 None，则返回默认值。
        """
        if not isinstance(data_dict, dict):
            return default

        value = data_dict.get(key, default)

        if value is None:
            return default

        return float(value)

    @staticmethod
    def _sum_component_metric(component_metrics, metric_name):
        """
        汇总所有器件的某一类指标。

        例如：
        metric_name = "volume" 时，计算所有器件总体积；
        metric_name = "cost" 时，计算所有器件总成本。
        """
        total_value = 0.0

        for _, metrics in component_metrics.items():
            total_value += float(metrics.get(metric_name, 0.0))

        return total_value

    def calculate_weighted_efficiency(self, efficiency_matrix):
        """
        计算多工况加权平均效率。
        """
        efficiency_matrix = np.asarray(efficiency_matrix, dtype=float)

        if self.weight_matrix is None:
            return float(np.mean(efficiency_matrix))

        weight_matrix = np.asarray(self.weight_matrix, dtype=float)

        if efficiency_matrix.shape != weight_matrix.shape:
            return float(np.mean(efficiency_matrix))

        return float(
            np.sum(efficiency_matrix * weight_matrix)
            / np.sum(weight_matrix)
        )

    def build_component_metrics(self, env):
        """
        从环境对象中整理器件级指标。
        不同器件的字段来源可以不一样，但最终统一整理成：
        {
            "component_name": {
                "volume": ...,
                "cost": ...,
                "loss": ...
            }
        }
        """

        # 1. 滤波电容指标
        # 这里要求 filter_capacitance_Cin / Cout 已经由
        # select_capacitance_with_metrics() 生成，
        # 其中包含 volume 和 cost 字段。
        filter_capacitor_volume = (
            self._safe_get(env.filter_capacitance_Cin, "volume")
            + self._safe_get(env.filter_capacitance_Cout, "volume")
        )

        filter_capacitor_cost = (
            self._safe_get(env.filter_capacitance_Cin, "cost")
            + self._safe_get(env.filter_capacitance_Cout, "cost")
        )

        # 2. 变压器指标
        transformer_volume = self._safe_get(
            env.transformer_params_dict,
            "V_T"
        )

        transformer_cost = self._safe_get(
            env.transformer_params_dict,
            "cost_T"
        )

        # 3. 电感指标
        inductor_volume = self._safe_get(
            env.inductor_params_dict,
            "V_L_total"
        )

        inductor_cost = self._safe_get(
            env.inductor_params_dict,
            "cost_L_total"
        )

        # 4. 谐振电容指标
        resonant_capacitor_volume = self._safe_get(
            env.capacitor_params_dict,
            "volume"
        )

        resonant_capacitor_cost = self._safe_get(
            env.capacitor_params_dict,
            "cost"
        )

        # 5. 开关器件指标
        switch_cost = self._safe_get(
            env.switch,
            "cost"
        )

        component_metrics = {
            "filter_capacitor": {
                "volume": filter_capacitor_volume,
                "cost": filter_capacitor_cost,
            },
            "transformer": {
                "volume": transformer_volume,
                "cost": transformer_cost,
            },
            "inductor": {
                "volume": inductor_volume,
                "cost": inductor_cost,
            },
            "resonant_capacitor": {
                "volume": resonant_capacitor_volume,
                "cost": resonant_capacitor_cost,
            },
            "switch": {
                "volume": 0.0,
                "cost": switch_cost,
            },
        }

        return component_metrics

    def calculate(self, env):
        """
        计算变换器级性能指标。
        """

        component_metrics = self.build_component_metrics(env)

        average_efficiency = self.calculate_weighted_efficiency(
            env.efficiency_matrix
        )

        total_volume = self._sum_component_metric(
            component_metrics,
            "volume"
        )

        total_cost = self._sum_component_metric(
            component_metrics,
            "cost"
        )

        metrics = {
            "efficiency": average_efficiency,
            "volume": total_volume,
            "cost": total_cost,
            "component_metrics": component_metrics,
        }

        return metrics
