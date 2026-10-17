from pe_workflow.science.dbsrc_v1.reward_cal.RewardCalculator import (
    RewardObjective,
    RewardCalculator,
)


def _build_objectives_from_config(objective_configs):
    reward_objectives = []

    for item in objective_configs:
        reward_objectives.append(
            RewardObjective(
                name=item["name"],
                min_value=item["min_value"],
                max_value=item["max_value"],
                weight=item["weight"],
                direction=item["direction"],
            )
        )

    return reward_objectives


def get_default_converter_reward_objectives(env_context: dict):
    """
    变换器默认奖励目标。

    默认考虑三个目标：
    1. efficiency：效率，越大越好；
    2. volume：体积，越小越好；
    3. cost：成本，越小越好。

    各拓扑如果没有特殊需求，可以共用该默认奖励目标。
    """
    return [
        {
            "name": "efficiency",
            "min_value": env_context.get("min_efficiency", 0.75),
            "max_value": env_context.get("max_efficiency", 1.00),
            "weight": env_context.get("weight_efficiency", 0.7),
            "direction": "max",
        },
        {
            "name": "volume",
            "min_value": env_context.get("min_volume", 100000),
            "max_value": env_context.get("max_volume", 900000),
            "weight": env_context.get("weight_volume", 0.2),
            "direction": "min",
        },
        {
            "name": "cost",
            "min_value": env_context.get("min_cost", 0),
            "max_value": env_context.get("max_cost", 374.4),
            "weight": env_context.get("weight_cost", 0.1),
            "direction": "min",
        },
    ]


def build_converter_reward_calculator(env_context: dict) -> RewardCalculator:
    """
    构建通用变换器奖励计算器。

    优先级：
    1. 如果 env_context 中传入 reward_objectives，则完全使用用户自定义目标；
    2. 如果没有传入 reward_objectives，则使用默认效率、体积、成本三目标；
    3. 如果同时传入 extra_reward_objectives，则在默认三目标基础上追加额外目标。
    """
    objective_configs = env_context.get("reward_objectives")

    if objective_configs is None:
        objective_configs = get_default_converter_reward_objectives(env_context)

        extra_objectives = env_context.get("extra_reward_objectives", [])
        objective_configs = objective_configs + extra_objectives

    reward_objectives = _build_objectives_from_config(objective_configs)

    return RewardCalculator(
        reward_objectives=reward_objectives,
        clip_range=env_context.get("reward_clip_range", (-1.0, 1.0)),
    )
