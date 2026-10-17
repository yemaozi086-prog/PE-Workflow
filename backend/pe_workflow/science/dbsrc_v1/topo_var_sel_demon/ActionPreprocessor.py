from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
import numpy as np


@dataclass
class ActionSpec:
    name: str
    min_value: float
    max_value: float
    scale: float = 1.0

    def __post_init__(self):
        if self.max_value <= self.min_value:
            raise ValueError(
                f"{self.name} 的 max_value 必须大于 min_value，"
                f"当前为 [{self.min_value}, {self.max_value}]"
            )


class ActionPreprocessor:
    def __init__(
        self,
        action_specs: List[ActionSpec],
        fixed_params: Optional[Dict[str, float]] = None,
        postprocess: Optional[Callable[[Dict[str, float]], Dict[str, float]]] = None,
        clip_action: bool = True,
    ):
        self.action_specs = action_specs
        self.fixed_params = fixed_params or {}
        self.postprocess = postprocess
        self.clip_action = clip_action

    @property
    def action_dim(self) -> int:
        return len(self.action_specs)

    @staticmethod
    def map_from_minus1_to_1(x: float, min_value: float, max_value: float) -> float:
        return (max_value + min_value) / 2 + (max_value - min_value) / 2 * x

    def transform(self, action: np.ndarray) -> Dict[str, float]:
        action = np.asarray(action, dtype=float).reshape(-1)

        if len(action) != self.action_dim:
            raise ValueError(
                f"动作维度不匹配：当前动作维度为 {len(action)}，"
                f"但配置中定义了 {self.action_dim} 个动作变量。"
            )

        if self.clip_action:
            action = np.clip(action, -1.0, 1.0)

        # 先放入固定参数
        params = dict(self.fixed_params)

        # 再放入由 PPO 动作映射得到的优化变量
        for value, spec in zip(action, self.action_specs):
            real_value = self.map_from_minus1_to_1(
                value,
                spec.min_value,
                spec.max_value
            )
            params[spec.name] = real_value * spec.scale

        if self.postprocess is not None:
            params = self.postprocess(params)

        return params