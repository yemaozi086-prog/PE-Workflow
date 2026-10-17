from dataclasses import dataclass
from typing import Dict, List
import numpy as np


@dataclass
class RewardObjective:
    name: str
    min_value: float
    max_value: float
    weight: float
    direction: str  # "max" or "min"

    def __post_init__(self):
        if self.max_value <= self.min_value:
            raise ValueError(
                f"{self.name} 的 max_value 必须大于 min_value，"
                f"当前为 [{self.min_value}, {self.max_value}]"
            )

        if self.direction not in ["max", "min"]:
            raise ValueError(
                f"{self.name} 的 direction 只能是 'max' 或 'min'，"
                f"当前为 {self.direction}"
            )


class RewardCalculator:
    """
    通用奖励值计算器。

    输入:
        metrics: 性能指标字典，例如：
        {
            "efficiency": 0.96,
            "volume": 300000,
            "cost": 50
        }

    输出:
        reward_result:
        {
            "reward": ...,
            "normalized_terms": ...,
            "weighted_terms": ...,
            "metrics": ...
        }
    """

    def __init__(self, reward_objectives: List[RewardObjective], clip_range=(-1.0, 1.0)):
        self.reward_objectives = reward_objectives
        self.clip_min, self.clip_max = clip_range

    @staticmethod
    def normalize(value: float, min_value: float, max_value: float) -> float:
        """
        将指标线性归一化到 [-1, 1]。
        """
        return 2 * (value - min_value) / (max_value - min_value) - 1

    def calculate(self, metrics: Dict[str, float]) -> Dict:
        reward = 0.0
        normalized_terms = {}
        weighted_terms = {}

        for objective in self.reward_objectives:
            if objective.name not in metrics:
                raise KeyError(
                    f"奖励计算缺少性能指标: {objective.name}，"
                    f"当前 metrics 中包含: {list(metrics.keys())}"
                )

            metric_value = float(metrics[objective.name])

            normalized_value = self.normalize(
                value=metric_value,
                min_value=objective.min_value,
                max_value=objective.max_value
            )

            if objective.direction == "max":
                weighted_value = objective.weight * normalized_value
            else:
                weighted_value = -objective.weight * normalized_value

            normalized_terms[objective.name] = normalized_value
            weighted_terms[objective.name] = weighted_value

            reward += weighted_value

        reward = float(np.clip(reward, self.clip_min, self.clip_max))

        return {
            "reward": reward,
            "normalized_terms": normalized_terms,
            "weighted_terms": weighted_terms,
            "metrics": metrics,
        }