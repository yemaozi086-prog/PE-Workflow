from typing import Dict, List, Tuple
import numpy as np

from pe_workflow.science.dbsrc_v1.topo_var_sel_demon.ActionPreprocessor import (
    ActionSpec,
    ActionPreprocessor,
)


def _get_range(env_context: dict, key: str, default_range: List[float]) -> Tuple[float, float]:
    value_range = env_context.get(key, default_range)

    if len(value_range) != 2:
        raise ValueError(
            f"{key} 必须是 [min, max] 格式，当前为: {value_range}"
        )

    min_value, max_value = value_range

    if max_value < min_value:
        raise ValueError(
            f"{key} 的上限不能小于下限，当前为: {value_range}"
        )

    return min_value, max_value


def _add_action_or_fixed_param(
    action_specs: list,
    fixed_params: dict,
    name: str,
    min_value: float,
    max_value: float,
    scale: float,
):
    """
    如果上下限不同，则作为设计变量加入 action_specs；
    如果上下限相同，则作为固定参数加入 fixed_params。
    """
    if max_value == min_value:
        fixed_params[name] = min_value * scale
    else:
        action_specs.append(
            ActionSpec(
                name=name,
                min_value=min_value,
                max_value=max_value,
                scale=scale,
            )
        )


def dbsrc_postprocess(params: Dict[str, float]) -> Dict[str, float]:
    """
    DBSRC 后处理。

    当前默认逻辑：
    frequency = frequency_offset + resonant_frequency

    注意：
    这里的 frequency_offset 可以是优化变量，也可以是固定参数。
    """
    inductance = params["inductance"]
    capacitance = params["capacitance"]

    resonant_frequency = 1 / (2 * np.pi * np.sqrt(inductance * capacitance))

    params["resonant_frequency"] = resonant_frequency
    params["frequency"] = params["frequency_offset"] + resonant_frequency

    return params


def build_dbsrc_action_preprocessor(env_context: dict) -> ActionPreprocessor:
    n_min, n_max = _get_range(env_context, "n_range", [0.6, 1.8])
    L_min, L_max = _get_range(env_context, "inductance_range", [10, 40])
    C_min, C_max = _get_range(env_context, "capacitance_range", [100, 300])
    f_min, f_max = _get_range(env_context, "frequency_range", [10, 50])

    action_specs = []
    fixed_params = {}

    _add_action_or_fixed_param(
        action_specs=action_specs,
        fixed_params=fixed_params,
        name="n",
        min_value=n_min,
        max_value=n_max,
        scale=1.0,
    )

    _add_action_or_fixed_param(
        action_specs=action_specs,
        fixed_params=fixed_params,
        name="inductance",
        min_value=L_min,
        max_value=L_max,
        scale=1e-6,
    )

    _add_action_or_fixed_param(
        action_specs=action_specs,
        fixed_params=fixed_params,
        name="capacitance",
        min_value=C_min,
        max_value=C_max,
        scale=1e-9,
    )

    _add_action_or_fixed_param(
        action_specs=action_specs,
        fixed_params=fixed_params,
        name="frequency_offset",
        min_value=f_min,
        max_value=f_max,
        scale=1e3,
    )

    return ActionPreprocessor(
        action_specs=action_specs,
        fixed_params=fixed_params,
        postprocess=dbsrc_postprocess,
    )
