import type {Port, Workflow} from './types';

export function numericPaths(ports:Record<string,Port>, allowIndex=false) {
  const paths:string[]=[];
  for(const [name,port] of Object.entries(ports)){
    if(port.type==='number') paths.push(name);
    if(port.type!=='flat_dict') continue;
    for(const [field,definition] of Object.entries(port.fields||{})){
      const path=name+'.'+field;
      if(definition.type==='number') paths.push(path);
      const valueType=definition.value_type;
      if(allowIndex&&definition.type==='array'&&valueType?.dtype.startsWith('float')&&
        valueType.shape.length===1&&typeof valueType.shape[0]==='number'){
        for(let index=0;index<Math.min(valueType.shape[0],32);index++) paths.push(path+'.'+index);
      }
    }
  }
  return paths;
}

export function isDbsrc(workflow:Workflow) {
  const action=workflow.inputs.action?.fields?.values;
  return workflow.nodes.some(node=>node.module_id==='dbsrc_action')&&action?.type==='array'&&
    action.value_type?.shape.length===1&&action.value_type.shape[0]===4&&
    workflow.output_ports.result?.fields?.reward?.type==='number';
}

export function deConfig(workflow:Workflow, deepAgent=false) {
  const common={seed:42,tol:0.01,atol:0,polish:false};
  if(isDbsrc(workflow)){
    const range=[-1,1];
    return deepAgent?{
      ...common,bounds:{'action.values.0':range,'action.values.2':range},
      fixed_inputs:{'action.values.1':0.2,'action.values.3':-0.5},
      objective:'result.reward',direction:'max',maxiter:50,popsize:15,tol:1e-6,polish:true,timeout_seconds:21600,
    }:{
      ...common,bounds:Object.fromEntries([0,1,2,3].map(i=>['action.values.'+i,range])),
      fixed_inputs:{},objective:'result.reward',direction:'max',maxiter:1,popsize:5,timeout_seconds:1200,
    };
  }
  return {...common,bounds:{},fixed_inputs:{},objective:numericPaths(workflow.output_ports)[0]||'',
    direction:'min',maxiter:5,popsize:5,timeout_seconds:60};
}
