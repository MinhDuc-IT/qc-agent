"""The generated-tests worker uses the same registry/backend contract as tool workers."""
import xml.etree.ElementTree as ET
from pathlib import Path
from .pytest import PytestUnitAdapter


class GeneratedPytestAdapter(PytestUnitAdapter):
    worker_id='python-pytest-generated'

    def supports(self,task,project):
        return bool(task.parameters.get('generated_cases')) and super().supports(task,project)

    def build_argv(self,task,project):
        return ('python','-m','pytest',*task.scope['paths'],'-q','--import-mode=importlib','--junitxml=.qc-generated-results.xml')

    def parse_result(self,task,exit_code,output,cwd=None):
        result=super().parse_result(task,exit_code,output,cwd)
        outcomes={}
        try:
            report=ET.parse(Path(cwd)/'.qc-generated-results.xml')
            for test in report.iter('testcase'):
                stem=test.get('classname','').split('.')[-1]
                if test.get('name')!='test_case':continue
                status='blocked' if test.find('error') is not None or test.find('skipped') is not None else 'defect' if test.find('failure') is not None else 'pass'
                outcomes.setdefault(stem,[]).append(status)
        except (OSError,ET.ParseError):pass
        cases=[]
        for case in task.parameters['generated_cases']:
            states=outcomes.get(Path(case['path']).stem,[])
            status=states[0] if len(states)==1 else 'blocked'
            cases.append({'case_id':case['case_id'],'status':status,'source':'generated','worker_id':self.worker_id})
        protocol_failure=exit_code not in (0,1) or (exit_code==1 and not any(c['status']=='defect' for c in cases))
        if protocol_failure:
            for case in cases:case['status']='blocked'
        blocked=protocol_failure or any(c['status']=='blocked' for c in cases)
        result.execution_status='failed' if blocked else 'completed'
        result.verdict='unknown' if blocked else 'fail' if any(c['status']=='defect' for c in cases) else 'pass'
        result.summary={'results':cases,'source':'generated','reason':'Collection/fixture/runtime failure or missing test result' if blocked else ''}
        return result
