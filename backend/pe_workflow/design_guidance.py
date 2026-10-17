"""Concrete protocol diagnostics derived only from explicit declared paths."""
def adapter_repair(candidate):
    module=(candidate or {}).get('module') or {}
    if module.get('implementation')=='python_subset':
        return {'entrypoint_name':'compute','source_signature':'def compute(inputs):','entrypoint_field_note':'entrypoint is a function name, never a source signature. python_subset uses compute by protocol; omit its unused entrypoint field.','input_access':'For python_subset read inputs[bag][field]. Do not use compute(electrical, device); adapters are not used for python_subset calls. Alternatively choose python_function with an explicit entrypoint and adapter. Do not mix the two protocols.','required_output_dictionaries':{key:list((port.get('fields') or {}).keys()) for key,port in (module.get('outputs') or {}).items()},'worker_action':'python_subset returns the complete declared output dictionary map. Each output port name must be an outer return key; do not return its inner fields at the top level. Adapter mappings do not wrap python_subset outputs. Preserve the supplied formula; do not modify user examples.'}
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
