"""Resolve scalar optimization addresses against declared workflow ports."""
from copy import deepcopy


def address(ports, name, *, allow_index=False):
    if name in ports:
        path = [name]
    else:
        path = name.split(".")
    port = ports[path[0]]
    if len(path) > 1:
        port = port.fields[path[1]]
    if len(path) == 3 and allow_index and port.type == "array":
        shape = port.value_type.shape if port.value_type else []
        index = int(path[2])
        if (len(shape) == 1 and isinstance(shape[0], int)
                and str(index) == path[2] and 0 <= index < shape[0]
                and port.value_type.dtype.startswith("float")):
            return [*path[:2], index]
    if len(path) <= 2 and port.type == "number":
        return path
    raise ValueError(f"Not a scalar optimization address: {name}")


def read_value(values, path):
    for key in path:
        values = values[key]
    return values


def candidate_inputs(base, paths, vector):
    values = deepcopy(base)
    for path, value in zip(paths, vector):
        parent = read_value(values, path[:-1])
        parent[path[-1]] = float(value)
    return values
