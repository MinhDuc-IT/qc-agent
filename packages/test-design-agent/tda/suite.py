"""Routes reviewed test data to HTTP, unit, Playwright, k6 and AI grading runners."""
import json
import time
from pathlib import Path

try:
    from .runner import HttpSuiteExecutor, INVALID_JSON, DEFAULT_AUTH, equal, contains, json_pointer
    from .runner_native import unit_case, browser_case, load_case
except ImportError:
    from tda_runner import HttpSuiteExecutor, INVALID_JSON, DEFAULT_AUTH, equal, contains, json_pointer
    from tda_native_runner import unit_case, browser_case, load_case


class SuiteExecutor(HttpSuiteExecutor):
    def __init__(self, workspace=None, cancellation=None, auth_token=DEFAULT_AUTH, judge=None, k6_binary=None, browser_channel='chrome', unit_images=None):
        super().__init__(cancellation,auth_token)
        self.workspace=Path(workspace).resolve() if workspace else None
        self.judge,self.k6_binary,self.browser_channel=judge,k6_binary,browser_channel
        self.unit_images=unit_images or {}

    def execute(self,cases,base_url=None,allow_mutations=False):
        report=super().execute(cases,base_url or 'http://127.0.0.1:1',allow_mutations)
        report['runner']='multi-runner-v3'
        report['runners']=sorted({case.get('runner','http') for case in cases})
        report['limitations']=['Native runners use bounded workloads; available runtimes and fixtures are required.']
        return report

    def _run(self,identifier):
        if identifier in self.results:return self.results[identifier]
        if identifier not in self.cases or identifier in self.visiting:
            return {'case_id':identifier,'status':'invalid','reason':'Unknown or cyclic dependency'}
        case=self.cases[identifier]
        runner=case.get('runner','http')
        if runner=='http':return super()._run(identifier)
        self.visiting.add(identifier)
        result={'case_id':identifier,'status':'environment_error','steps':[],'cleanup_errors':[],'runner':runner}
        try:
            if any(self._run(dep)['status']!='pass' for dep in case['depends_on']):
                return self._finish(identifier,{**result,'status':'blocked','reason':'Dependency did not pass'})
            if time.monotonic()>=self.deadline or self.cancellation is not None and self.cancellation.is_set():
                raise TimeoutError('Suite budget exhausted')
            if runner=='unit':
                if not self.workspace:raise OSError('Repository workspace required')
                result=unit_case(case,self.workspace,self.cancellation,self.unit_images.get(case['unit']['language']))
            elif runner=='browser':
                result=browser_case(case,self.base_url,self.allow_mutations,self.auth_token,self.cancellation,self.browser_channel)
            elif runner=='k6':
                if any(request['auth']=='bearer_env' for request in case['steps']) and not self.auth_token:
                    result.update(status='blocked',reason='Project API token unavailable')
                else:result=load_case(case,self.base_url,self.auth_token,self.k6_binary,self.cancellation)
            elif runner=='ai':result=self._ai(case)
            else:result.update(status='invalid',reason='Runner unsupported')
        except Exception as error:
            result.update(status='environment_error',reason='Runner execution failed ('+type(error).__name__+')')
        return self._finish(identifier,result)

    def _finish(self,identifier,result):
        self.visiting.discard(identifier);self.results[identifier]=result;return result

    def _ai(self,case):
        result={'case_id':case['id'],'status':'pass','steps':[],'cleanup_errors':[],'runner':'ai'}
        if any(request['method'] not in {'GET','HEAD'} for request in case['steps']+case['cleanup']) and not self.allow_mutations:
            return {**result,'status':'blocked','reason':'Mutation policy blocks this evaluation'}
        if any(request['auth']=='bearer_env' for request in case['steps']+case['cleanup']) and not self.auth_token:
            return {**result,'status':'blocked','reason':'Project API token unavailable'}
        if case.get('grading') and not self.judge:
            return {**result,'status':'environment_error','reason':'AI judge is not configured'}
        try:
            for index,request in enumerate(case['steps']):
                status,body,latency=self._request(request);failures=[];grade=None
                assertions=next(step['assertions'] for step in case['oracle']['steps'] if step['step_index']==index)
                for assertion in assertions:
                    expected=json.loads(assertion['expected_json']);kind=assertion['kind']
                    if kind=='status':passed=status==expected
                    elif kind=='text_equals':passed=self.last_text==expected
                    elif kind=='text_contains':passed=expected in self.last_text
                    elif kind=='ai_score':
                        rubric=case['grading'];actual=self.last_text if body is INVALID_JSON else json_pointer(body,rubric['response_pointer'])
                        if not isinstance(actual,str):actual=json.dumps(actual,ensure_ascii=False)
                        if len(actual)>12000:raise ValueError('AI output exceeds grading budget')
                        if grade is None:grade=self.judge(rubric['rubric'],actual)
                        score=grade['score']
                        if type(score) not in {float,int} or not 0<=score<=1:raise ValueError('Invalid AI grade')
                        passed=score>=expected
                    else:
                        try:
                            actual=json_pointer(body,assertion['pointer']);passed=equal(actual,expected) if kind=='json_equals' else contains(actual,expected)
                        except (KeyError,ValueError,TypeError,IndexError):passed=False
                    if not passed:failures.append({'kind':kind,'pointer':assertion['pointer']})
                step={'index':index,'status_code':status,'latency_ms':round(latency,3),'failed_assertions':failures}
                if grade:step['grade']=grade
                result['steps'].append(step)
                if failures:result['status']='defect';break
        finally:
            for cleanup in case['cleanup']:
                try:
                    status,_,_=self._request(cleanup)
                    if status>=400:result['cleanup_errors'].append('Cleanup failed')
                except Exception:result['cleanup_errors'].append('Cleanup transport failed')
            if result['cleanup_errors'] and result['status']=='pass':result['status']='environment_error'
        return result
