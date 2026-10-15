"""Fixed native runner templates. Test data never supplies source code or shell commands."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit


PYTHON_UNIT = r'''import asyncio, importlib.util, inspect, json, sys
from pathlib import Path
import pytest
def same(actual,expected):
    if isinstance(expected,bool) or isinstance(actual,bool):return type(actual) is type(expected) and actual==expected
    if isinstance(expected,dict):return isinstance(actual,dict) and actual.keys()==expected.keys() and all(same(actual[k],v) for k,v in expected.items())
    if isinstance(expected,list):return isinstance(actual,list) and len(actual)==len(expected) and all(same(a,e) for a,e in zip(actual,expected))
    return actual==expected
def test_generated_case():
    case=json.loads(Path(__file__).with_name('case.json').read_text(encoding='utf-8'))
    root=Path(__file__).with_name('workspace.txt').read_text(encoding='utf-8')
    sys.path.insert(0,root)
    path=Path(root)/case['unit']['module_path']
    name=case['unit']['module_path'].replace('/','.').removesuffix('.py')
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    try:
        value=getattr(module,case['unit']['function'])(*json.loads(case['unit']['args_json']),**json.loads(case['unit']['kwargs_json']))
        if inspect.isawaitable(value):value=asyncio.run(value)
        actual={'returned':value}
    except Exception as error:
        actual={'raised':type(error).__name__,'classes':[cls.__name__ for cls in type(error).__mro__], 'message':str(error)}
    failures=[]
    for assertion in case['oracle']['steps'][0]['assertions']:
        expected=json.loads(assertion['expected_json']);kind=assertion['kind']
        passed=same(actual.get('returned'),expected) and 'returned' in actual if kind=='return_equals' else expected in actual.get('classes',[]) if kind=='raises' else actual.get('message')==expected
        if not passed:failures.append({'kind':kind,'pointer':assertion['pointer']})
    result={'case_id':case['id'],'status':'defect' if failures else 'pass','steps':[{'index':0,'actual':actual,'failed_assertions':failures}],'cleanup_errors':[]}
    Path(__file__).with_name('result.json').write_text(json.dumps(result,allow_nan=False),encoding='utf-8')
    assert not failures,'Generated unit assertion failed'
'''

JS_UNIT = r'''import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
const folder=path.dirname(fileURLToPath(import.meta.url));
const spec=JSON.parse(fs.readFileSync(path.join(folder,'case.json'),'utf8'));
const workspace=fs.readFileSync(path.join(folder,'workspace.txt'),'utf8');
test(spec.id,async()=>{
  const module=await import(pathToFileURL(path.join(workspace,spec.unit.module_path)).href);
  const fn=module[spec.unit.function]||module.default?.[spec.unit.function];
  let actual;
  try{const value=await fn(...JSON.parse(spec.unit.args_json));actual={returned:value}}
  catch(error){actual={raised:error.name,classes:[error.name],message:error.message}}
  if('returned' in actual && actual.returned===undefined)throw new Error('Return value is not JSON');
  const failures=[];
  for(const a of spec.oracle.steps[0].assertions){const expected=JSON.parse(a.expected_json);let ok=false;
    if(a.kind==='return_equals'){try{assert.deepEqual(actual.returned,expected);ok='returned' in actual}catch{}}
    else if(a.kind==='raises')ok=(actual.classes||[]).includes(expected);else ok=actual.message===expected;
    if(!ok)failures.push({kind:a.kind,pointer:a.pointer});
  }
  fs.writeFileSync(path.join(folder,'result.json'),JSON.stringify({case_id:spec.id,status:failures.length?'defect':'pass',steps:[{index:0,actual,failed_assertions:failures}],cleanup_errors:[]}));
  assert.equal(failures.length,0,'Generated unit assertion failed');
});
'''

K6_RUNNER = r'''import http from 'k6/http';
import {sleep} from 'k6';
import {Counter} from 'k6/metrics';
const cfg=JSON.parse(open('./case.json'));
const failures=new Counter('oracle_failures');
const thresholds={oracle_failures:['count==0']};
for(const step of cfg.case.oracle.steps){for(const a of step.assertions){
 if(a.kind==='latency_p95_ms')thresholds['http_req_duration{step:'+step.step_index+'}']=['p(95)<='+JSON.parse(a.expected_json)];
 if(a.kind==='error_rate')thresholds['http_req_failed{step:'+step.step_index+'}']=['rate<='+JSON.parse(a.expected_json)];
}}
export const options={vus:cfg.case.load.vus,duration:cfg.case.load.duration_seconds+'s',thresholds,discardResponseBodies:false};
function same(actual,expected){if(actual===expected)return true;if(!actual||!expected||typeof actual!=='object'||typeof expected!=='object'||Array.isArray(actual)!==Array.isArray(expected))return false;const keys=Object.keys(expected);return Object.keys(actual).length===keys.length&&keys.every(k=>Object.prototype.hasOwnProperty.call(actual,k)&&same(actual[k],expected[k]))}
function contains(actual,expected){if(expected&&typeof expected==='object'&&!Array.isArray(expected))return actual&&Object.keys(expected).every(k=>contains(actual[k],expected[k]));return same(actual,expected)}
function pointer(value,path){for(const part of path.split('/').slice(1))value=value[part.replace(/~1/g,'/').replace(/~0/g,'~')];return value}
export default function(){for(let i=0;i<cfg.case.steps.length;i++){
 const request=cfg.case.steps[i];let url=cfg.base_url+request.path;
 if(request.query.length)url+='?'+request.query.map(p=>encodeURIComponent(p.name)+'='+encodeURIComponent(p.value)).join('&');
 const headers=request.auth==='bearer_env'?{Authorization:'Bearer '+__ENV.TDA_AUTH_TOKEN}:{};
 const response=http.request(request.method,url,null,{headers,tags:{step:String(i)},redirects:0,timeout:'5s'});
 let failed=0;
 for(const a of cfg.case.oracle.steps.find(s=>s.step_index===i).assertions){const expected=JSON.parse(a.expected_json);let ok=true;
  try{if(a.kind==='status')ok=response.status===expected;
   else if(a.kind==='text_equals')ok=response.body===expected;else if(a.kind==='text_contains')ok=response.body.includes(expected);
   else if(a.kind==='json_equals')ok=same(pointer(response.json(),a.pointer),expected);
   else if(a.kind==='json_contains')ok=contains(pointer(response.json(),a.pointer),expected);
  }catch{ok=false}if(!ok)failed++;
}failures.add(failed,{step:String(i)});
}sleep(0.1)}
export function handleSummary(data){return {'result.json':JSON.stringify(data)}}
'''


def clean_environment(extra=None):
    allowed={'PATH','PATHEXT','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','LANG','LC_ALL'}
    return {**{key:value for key,value in os.environ.items() if key.upper() in allowed},
            'PYTHONIOENCODING':'utf-8','K6_NO_USAGE_REPORT':'true',**(extra or {})}


def run_bounded(command, directory, timeout, cancellation=None, extra=None):
    process=subprocess.Popen(command,cwd=directory,env=clean_environment(extra),stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,shell=False,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
        start_new_session=os.name!='nt')
    deadline=time.monotonic()+timeout
    try:
        while True:
            if time.monotonic()>=deadline or cancellation is not None and cancellation.is_set():
                raise TimeoutError('Runner budget exhausted or cancelled')
            try:
                output,_=process.communicate(timeout=0.25)
                return process.returncode,output[-8000:]
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            else:
                import signal
                os.killpg(process.pid,signal.SIGKILL)
            process.wait(timeout=5)


def unit_case(case, workspace, cancellation=None, docker_image=None):
    root=Path(workspace).resolve()
    source=(root/case['unit']['module_path']).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError('Unit module unavailable')
    with tempfile.TemporaryDirectory(prefix='tda-unit-') as temporary:
        folder=Path(temporary)
        (folder/'case.json').write_text(json.dumps(case),encoding='utf-8')
        (folder/'workspace.txt').write_text(str(root),encoding='utf-8')
        if case['unit']['language']=='python':
            (folder/'test_generated.py').write_text(PYTHON_UNIT,encoding='utf-8')
            command=[sys.executable,'-I','-m','pytest','test_generated.py','-q','-p','no:cacheprovider','--confcutdir',str(folder)]
        else:
            node=shutil.which('node')
            if not node:raise OSError('Node unavailable')
            (folder/'test_generated.mjs').write_text(JS_UNIT,encoding='utf-8')
            command=[node,'--test','test_generated.mjs']
        container=None
        if docker_image:
            import hashlib
            container='qc-unit-'+hashlib.sha256(str(folder).encode()).hexdigest()[:20]
            (folder/'workspace.txt').write_text('/workspace',encoding='utf-8')
            inner=['python','-m','pytest','test_generated.py','-q','-p','no:cacheprovider','--basetemp','/tmp/pytest','--confcutdir','/tests'] if case['unit']['language']=='python' else ['node','--test','test_generated.mjs']
            command=['docker','run','--rm','--name',container,'--network','none','--cap-drop','ALL','--security-opt','no-new-privileges',
                '--read-only','--tmpfs','/tmp:rw,size=64m','--cpus','1','--memory','512m','--pids-limit','128',
                '--mount',f'type=bind,source={root},target=/workspace,readonly',
                '--mount',f'type=bind,source={folder},target=/tests','--workdir','/tests',docker_image,*inner]
        try:run_bounded(command,folder,30,cancellation)
        finally:
            if container:subprocess.run(['docker','rm','--force',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
        result=folder/'result.json'
        if not result.is_file():raise OSError('Unit framework produced no report')
        return json.loads(result.read_text(encoding='utf-8'))


def load_case(case, base_url, auth_token=None, binary=None, cancellation=None):
    if urlsplit(base_url).hostname not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('Load runner is restricted to the isolated local target')
    executable=binary or shutil.which('k6')
    if not executable or not Path(executable).is_file():raise OSError('k6 unavailable')
    with tempfile.TemporaryDirectory(prefix='tda-k6-') as temporary:
        folder=Path(temporary)
        (folder/'case.json').write_text(json.dumps({'case':case,'base_url':base_url}),encoding='utf-8')
        (folder/'test_generated.js').write_text(K6_RUNNER,encoding='utf-8')
        code,_=run_bounded([str(executable),'run','--quiet','test_generated.js'],folder,
            case['load']['duration_seconds']+30,cancellation,{'TDA_AUTH_TOKEN':auth_token} if auth_token else None)
        summary=folder/'result.json'
        if not summary.is_file():raise OSError('k6 produced no report')
        report=json.loads(summary.read_text(encoding='utf-8'))
        if code not in {0,99}:raise OSError('k6 execution failed')
        thresholds={name:metric.get('thresholds',{}) for name,metric in report.get('metrics',{}).items() if metric.get('thresholds')}
        failed=code!=0 or any(not item.get('ok',False) for values in thresholds.values() for item in values.values())
        observations=[]
        for index in range(len(case['steps'])):
            values=report.get('metrics',{}).get('http_req_duration{step:'+str(index)+'}',{}).get('values',{})
            observations.append({'index':index,'p95_ms':values.get('p(95)'),
                'failed_assertions':[{'kind':'load_threshold','pointer':''}] if failed else []})
        return {'case_id':case['id'],'status':'defect' if failed else 'pass','steps':observations,
                'cleanup_errors':[],'metrics':report.get('metrics',{}),'thresholds':thresholds,
                'runner':'k6','vus':case['load']['vus'],'duration_seconds':case['load']['duration_seconds']}


def browser_case(case, base_url, allow_mutations, auth_token=None, cancellation=None, channel='chrome'):
    from playwright.sync_api import sync_playwright, expect, Error
    origin=urlsplit(base_url)
    if origin.hostname not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('Browser runner is restricted to the isolated local target')
    if case['browser']['auth']=='bearer_env' and not auth_token:
        return {'case_id':case['id'],'status':'blocked','steps':[],'cleanup_errors':[],'reason':'Project API token unavailable'}
    result={'case_id':case['id'],'status':'pass','steps':[],'cleanup_errors':[],'runner':'playwright'}
    blocked=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel=channel or None,headless=True)
        try:
            context=browser.new_context(service_workers='block',extra_http_headers=
                {'Authorization':'Bearer '+auth_token} if case['browser']['auth']=='bearer_env' else {})
            def route_request(route):
                request=route.request;url=urlsplit(request.url)
                if (url.scheme,url.netloc)!=(origin.scheme,origin.netloc) or request.method not in {'GET','HEAD'} and not allow_mutations:
                    blocked.append(request.url);route.abort()
                else:route.continue_()
            context.route('**/*',route_request)
            page=context.new_page();page.set_default_timeout(5000);status=None
            deadline=time.monotonic()+30
            for index,action in enumerate(case['browser']['actions']):
                if cancellation is not None and cancellation.is_set():raise TimeoutError('Cancelled')
                remaining=min(5000,max(1,(deadline-time.monotonic())*1000))
                if time.monotonic()>=deadline:raise TimeoutError('Browser budget exhausted')
                page.set_default_timeout(remaining)
                try:
                    if action['action']=='goto':
                        response=page.goto(base_url+action['path'],wait_until='domcontentloaded');status=response.status if response else None
                    elif action['action']=='fill':page.locator(action['selector']).fill(action['value'])
                    elif action['action']=='click':page.locator(action['selector']).click()
                    elif action['action']=='select':page.locator(action['selector']).select_option(action['value'])
                    elif action['action']=='check':page.locator(action['selector']).check()
                except Error:
                    result.update(status='blocked' if blocked else 'defect',reason='Browser action did not complete')
                    result['steps'].append({'index':index,'failed_assertions':[{'kind':'browser_action','pointer':action.get('selector','')}]})
                    break
                failures=[]
                for assertion in next(step for step in case['oracle']['steps'] if step['step_index']==index)['assertions']:
                    expected=json.loads(assertion['expected_json']);kind=assertion['kind']
                    try:
                        locator=page.locator(assertion['pointer']) if assertion['pointer'] else None
                        if kind=='status':assert status==expected
                        elif kind=='dom_visible':expect(locator).to_be_visible(timeout=remaining) if expected else expect(locator).not_to_be_visible(timeout=remaining)
                        elif kind=='dom_text_equals':expect(locator).to_have_text(expected,timeout=remaining)
                        elif kind=='dom_text_contains':expect(locator).to_contain_text(expected,timeout=remaining)
                        elif kind=='dom_value_equals':expect(locator).to_have_value(expected,timeout=remaining)
                        else:raise ValueError('Unsupported browser assertion')
                    except (AssertionError,Error):failures.append({'kind':kind,'pointer':assertion['pointer']})
                result['steps'].append({'index':index,'failed_assertions':failures})
                if failures:result['status']='defect';break
            if blocked:result.update(status='blocked',reason='Browser request outside allowed target or mutation policy')
            context.close()
        finally:browser.close()
    return result
