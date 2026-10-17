"""Read-only audit of the original trusted DBSRC program, never uploaded code.

Run with the legacy environment's Python (gymnasium/pandas are reference-only).
Writes evidence only under this project's docs; never edits the source tree.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tools-root', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'docs/dbsrc-reference.json')
    args = parser.parse_args()
    tools = args.tools_root.resolve()
    prefix = 'topo_open_loop_param_opt.topo_open_loop_param_envs'
    entry = prefix + '.topo_envs_main.DBSRC_env_main'
    # Follow static local imports, retaining each original file's byte hash.
    pending, seen, inventory = [entry], set(), []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = tools.joinpath(*name.split('.')).with_suffix('.py')
        if not path.is_file():
            continue
        raw = path.read_bytes()
        tree = ast.parse(raw.decode('utf-8-sig'))
        imports, loops = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
                if node.module.startswith(prefix):
                    pending.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(x.name for x in node.names)
            elif isinstance(node, (ast.For, ast.While)):
                loops.append({'line': node.lineno, 'kind': type(node).__name__, 'header': ast.unparse(node).splitlines()[0]})
        inventory.append({'path': path.relative_to(tools).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'imports': sorted(set(imports)), 'loops': loops})
    sys.path.insert(0, str(tools))
    import numpy as np
    from topo_open_loop_param_opt.topo_open_loop_param_envs.topo_envs_main.DBSRC_env_main import DBSRC_Hardware_ParamTuning

    def plain(value):
        if isinstance(value, np.ndarray):
            return plain(value.tolist())
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {str(k): plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(v) for v in value]
        return value

    def run(env, action):
        start = time.perf_counter()
        result = env.step(np.array(action))
        metrics = env.metrics_calculator.calculate(env) if result[1] != -1 else None
        return plain({'action': action, 'reward': result[1], 'elapsed_seconds': time.perf_counter()-start,
                      'info': result[4], 'metrics': metrics, 'efficiency_matrix': env.efficiency_matrix,
                      'detailed_params_matrix': env.detailed_params_matrix,
                      'filter_required_F': [env.Cin_value, env.Cout_value],
                      'selected': {'transformer': env.transformer_params_dict, 'inductor': env.inductor_params_dict,
                                   'switch': env.switch, 'capacitor': env.capacitor_params_dict,
                                   'filter_input': env.filter_capacitance_Cin, 'filter_output': env.filter_capacitance_Cout}})

    action = [-.1638, 0, -.3891, -.7529]
    baseline = run(DBSRC_Hardware_ParamTuning({}, {}, {}), action)
    strict = run(DBSRC_Hardware_ParamTuning({'p_s_limit': 0}, {}, {}), action)
    # Controlled state contamination probe: change the constraint, retain old matrices.
    reused = DBSRC_Hardware_ParamTuning({}, {}, {})
    run(reused, action)
    reused.SwitchLossConstraintChecker.p_limit_single = 0
    reused_strict = run(reused, action)
    data = {'kind': 'legacy_reference_not_migrated_workflow', 'python': sys.version,
            'source_root': str(tools), 'reference_scope': 'default EPS / fresh ENV / single action',
            'tolerance_proposal': {'rtol': 1e-9, 'atol': 1e-10},
            'inventory': sorted(inventory, key=lambda x: x['path']),
            'cases': {'fresh_default': baseline, 'fresh_zero_switch_limit': strict,
                      'reused_zero_switch_limit': reused_strict},
            'state_probe': {'constraint_changed_for_probe_only': True,
                            'fresh_rejected': strict['reward'] == -1,
                            'reused_matrix_unchanged': reused_strict['efficiency_matrix'] == baseline['efficiency_matrix'],
                            'reused_reward': reused_strict['reward']}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'evidence': str(args.output), 'files': len(inventory), 'baseline_reward': baseline['reward'],
                      'baseline_metrics': baseline['metrics'], 'state_probe': data['state_probe']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
