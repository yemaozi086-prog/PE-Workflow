"""Offline deterministic grading; never supplies answers to the generator."""
import math
import re


def equivalent(actual,expected,tolerance=1e-9):
    if isinstance(expected,dict):return isinstance(actual,dict) and actual.keys()==expected.keys() and all(equivalent(actual[k],v,tolerance) for k,v in expected.items())
    if isinstance(expected,list):return isinstance(actual,list) and len(actual)==len(expected) and all(equivalent(a,b,tolerance) for a,b in zip(actual,expected))
    if isinstance(expected,(int,float)) and not isinstance(expected,bool):return isinstance(actual,(int,float)) and not isinstance(actual,bool) and math.isfinite(actual) and abs(actual-expected)<=tolerance
    return actual==expected


def grade(case,outcome):
    errors=[];calls=outcome.get('generation_count',0)
    if not 1<=calls<=4:errors.append('真实调用数须为 1–4，不能用零调用或追加轮次通过')
    if outcome.get('batch',1)!=1:errors.append('本轮禁止追加修正额度')
    if case['kind'] in ('ambiguity','transform_gap'):
        if outcome['status'] not in ('needs_clarification','blocked'):errors.append('关键语义缺失时应追问或停止')
        text=' '.join(outcome.get('questions',{}).values())+' '+outcome.get('explanation','')
        if case['kind']=='transform_gap':
            if not re.search(r'重采样|转置|resampl|transpose|采样网格|插值',text,re.I):errors.append('未指出显式变换的前提或验证依据缺口')
        elif not (re.search(r'返回|输出|return|output|raw_group|raw_total',text,re.I) and re.search(r'单位|倍|系数|unit|factor|1000',text,re.I)):errors.append('未指出返回单位或倍率的关键缺口')
        if outcome.get('published',True) or not outcome.get('module_keys_unchanged',False):errors.append('语义未确认时不得发布')
    else:
        if outcome['status']!='applied':errors.append('完整任务未发布成功')
        if not outcome.get('source_matches_patch'):errors.append('发布源码必须与 DSH 补丁原文一致')
        if not outcome.get('original_ports_unchanged'):errors.append('原契约必须保持不变')
        if not equivalent(outcome.get('outputs'),case['expected']):errors.append('完整工作流结果与独立参考不符')
        expected_cases=case.get('evaluation_cases',[]);actual_cases=outcome.get('case_results',[])
        if len(actual_cases)!=len(expected_cases):errors.append('完整工况数不匹配')
        elif any(not a.get('passed') or not equivalent(a.get('outputs'),e['expected']) for a,e in zip(actual_cases,expected_cases)):errors.append('至少一个完整工况参考不符')
    passed=not errors
    return {'passed':passed,'first_generation_passed':passed and calls==1,'diagnostics':errors,
            'generation_count':calls,'status':outcome['status']}
