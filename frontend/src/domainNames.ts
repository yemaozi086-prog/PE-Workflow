import type {Module,WorkflowNode} from './types';

// Presentation names are independent of immutable scientific module contracts.
export const dbsrcNames:Record<string,string>={
  action:'设计变量反归一化与谐振频率计算',
  sizing:'器件选型电气应力汇总与滤波电容需求计算',
  select_transformer:'高频变压器磁芯与绕组选型',
  select_inductor:'谐振电感磁芯与绕组选型',
  select_switch:'IGBT 功率开关器件选型',
  select_capacitor:'谐振电容组容量与耐压选型',
  select_filter_input:'输入滤波电容组选型',
  select_filter_output:'输出滤波电容组选型',
  workpoints:'电压与负载工况网格生成',
  modulation:'EPS 移相解搜索与电压电流特征计算',
  loss_switch:'IGBT 导通与开关损耗计算',
  check_switch:'功率开关单管损耗约束校验',
  loss_transformer:'高频变压器铜损、铁损与磁通密度计算',
  check_transformer:'变压器损耗密度与磁通密度约束校验',
  loss_inductor:'谐振电感铜损、铁损与磁通密度计算',
  check_inductor:'谐振电感损耗密度与磁通密度约束校验',
  loss_capacitor:'谐振电容介质损耗计算',
  check_capacitor:'谐振电容组损耗约束校验',
  candidate_efficiency:'候选移相方案总损耗与效率计算',
  best:'最优可行移相方案筛选',
  metrics:'加权效率、器件体积与成本汇总',
  reward:'多目标加权奖励计算',
};

export function moduleDisplayName(m:Module):string {
  const key=m.id.startsWith('dbsrc_')?m.id.slice(6):'';
  return dbsrcNames[key] && (m.name===`DBSRC · ${key}` || m.name===m.id) ? dbsrcNames[key] : m.name;
}

export function nodeDisplayName(n:WorkflowNode,modules:Module[]):string {
  if(n.label)return n.label;
  if(n.kind==='batch'&&n.body?.id==='dbsrc_workpoint_body')return '电压与负载工况遍历';
  if(n.kind==='batch'&&n.body?.id==='dbsrc_candidate_body')return 'EPS 移相候选方案遍历';
  const module=modules.find(m=>m.id===n.module_id&&m.version===n.module_version);
  return module?moduleDisplayName(module):n.id;
}
