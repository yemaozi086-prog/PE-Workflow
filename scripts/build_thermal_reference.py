"""Independent development reference: original ENV + analytical loss multiplier.

Run with legacy Python and explicit tools root. No candidate or DSH code is used.
The coefficients are demo assumptions, awaiting research-group confirmation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tools-root',required=True,type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    original=json.loads((root/'docs/dbsrc-reference.json').read_text(encoding='utf-8'))
    for item in original['inventory']:
        if hashlib.sha256((args.tools_root/item['path']).read_bytes()).hexdigest()!=item['sha256']:raise ValueError('原始科学代码已改变，必须重新确认基准')
    sys.path.insert(0,str(args.tools_root))
    from topo_open_loop_param_opt.topo_open_loop_param_envs.topo_envs_main.DBSRC_env_main import DBSRC_Hardware_ParamTuning
    from topo_open_loop_param_opt.topo_open_loop_param_envs.component_loss_cal.capacitor_loss_cal.capacitor_loss_cal import calc_Cr_loss
    class Dispatcher:
        def __init__(self,factor):self.factor=factor
        def has_override_for(self,component,slot):return component=='capacitor' and slot=='loss_module'
        def get(self,component,slot):return lambda current:{k:float(v)*self.factor for k,v in calc_Cr_loss(current).items()}
    cases=[]
    for temperature in (25.,75.,125.):
        factor=1+.002*(temperature-25.)
        env=DBSRC_Hardware_ParamTuning({'_module_dispatcher':Dispatcher(factor)},{},{})
        result=env.step(np.array([-.1638,0,-.3891,-.7529]))
        assert result[1]!=-1
        metrics=env.metrics_calculator.calculate(env)
        output={'valid':1.,'reward':float(result[1]),**{k:float(metrics[k]) for k in ('efficiency','volume','cost')},'efficiency_matrix':env.efficiency_matrix.tolist(),'detail_matrix':env.detailed_params_matrix.tolist()}
        if temperature==25.:
            np.testing.assert_allclose(output['detail_matrix'],original['cases']['fresh_default']['detailed_params_matrix'],rtol=1e-12,atol=1e-12)
        cases.append({'temperature':temperature,'factor':factor,'expected':{'result':output}})
        print(temperature,output['reward'],output['efficiency'])
    document={'label':'平台自带手算示例与原 ENV 扩展参考，待课题组核对','formula':'P(T)=I_rms^2*0.001/(2*pi*f*C)*(1+alpha*(temperature-reference_temperature))','alpha':.002,'reference_temperature':25.,'reference_method':'Frozen original ENV; original capacitor loss multiplied by analytical temperature factor, independent of DSH candidate. Each temperature re-enumerates all workpoints and modulation choices.','cases':cases}
    (root/'backend/pe_workflow/science/dbsrc_thermal_reference.json').write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'docs/dbsrc-thermal-reference.json').write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
