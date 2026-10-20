"""Concrete protocol diagnostics derived only from explicit declared paths."""
def adapter_repair(candidate):
    module=(candidate or {}).get('module') or {}
    if not isinstance(module,dict):
        return {'invalid_previous_envelope':True,'required_payload':'kind=module with module as a JSON OBJECT, never a string; source must be module.source and outputs contains Port objects only.',
                'worker_action':'Rebuild the tool argument object using the frozen step contract; previous serialized text is diagnostic data, not an accepted module. Keep formula and code intent. Read exact guards and metadata. Declare required transforms; do not invent expected answers.',
                'array_logic':array_logic_repair()}
    if module.get('implementation')=='python_subset':
        return {'array_logic':array_logic_repair(), 'entrypoint_name':'compute','source_signature':'def compute(inputs):','entrypoint_field_note':'entrypoint is a function name, never a source signature. python_subset uses compute by protocol; omit its unused entrypoint field.','input_access':'For python_subset read inputs[bag][field]. Do not use compute(electrical, device); adapters are not used for python_subset calls. Alternatively choose python_function with an explicit entrypoint and adapter. Do not mix the two protocols.','required_output_dictionaries':{key:list((port.get('fields') or {}).keys()) for key,port in (module.get('outputs') or {}).items()},'worker_action':'python_subset returns the complete declared output dictionary map. Each output port name must be an outer return key; do not return its inner fields at the top level. Adapter mappings do not wrap python_subset outputs. Preserve the supplied formula; do not modify user examples.'}
    declared=module.get('inputs') or {}
    required={};observed={}
    for argument,mapping in ((module.get('adapter') or {}).get('arguments') or {}).items():
        path=mapping.get('from',mapping.get('from_path'))
        if isinstance(path,list) and path and path[0] in declared and path[0]!='inputs':
            required[argument]={'from':['inputs',*path]};observed[argument]=path
    return {'basis':'Only a missing protocol root is expanded in this diagnostic. Existing declared dictionary and field names are preserved. Candidate is not edited.',
            'required_input_mapping_prefix':['inputs'],
            'observed_argument_paths':observed,'required_argument_mappings':required,
            'worker_action':'Re-submit a complete Module. Correct these adapter paths exactly; use from, omit from_path and null mapping properties. Keep the valid supplied formula. The platform will verify independent examples.'}


def array_logic_repair():
    return {'scope':'Boolean ARRAY masks only; these are protocol identities, not new physical formulas.',
            'and':'((a * 1) * (b * 1)) > 0', 'or':'((a * 1) + (b * 1)) > 0', 'not':'a == False',
            'forbidden':['&','|','~','array-valued Python and/or','numpy.logical_and','numpy.where','numpy.nan'],
            'all_axis':'numpy.all/any do not accept axis. Use numpy.sum(mask * 1, axis=k) == exact_axis_length for all, or > 0 for any.',
            'reshape':'No inferred -1 or zero dimensions. Input arrays are already declared 1-D; otherwise use explicit positive dimensions, e.g. len(x).',
            'finite_inputs':'Ordinary numeric inputs are already finite-validated at the platform boundary. Do not invent finite_limit cutoffs, numpy.isfinite or unregistered functions.',
            'missing':'Never compute 0/0. For an output with explicit missing_when validity-column contract, compute finite values with safe positive denominator, then pe.masked_values(finite_values, validity). This yields NaN semantics encoded as JSON null only at invalid rows. Valid rows remain finite; do not return invented zero recommendations.',
            'grouping':'For general grouping preserve membership by actual group keys, not only cumulative first-row counts; interleaved rows of an existing group still belong to that same group. Preserve declared tie-breaking.'}
