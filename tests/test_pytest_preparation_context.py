from types import SimpleNamespace
from test_flow_design_app import flow_app
from app.infrastructure.test_design.pytest_context import preparation_context,script_setup_issues
from tda.domain.worker_artifacts import ExecutionProfile
from tda.infrastructure.repository_tools import RepositoryTools


def test_context_reads_model_tail_and_imported_test_helpers(tmp_path):
    (tmp_path/'app').mkdir();(tmp_path/'tests/utils').mkdir(parents=True)
    (tmp_path/'app/items.py').write_text('from app.models import Item\n')
    (tmp_path/'app/models.py').write_text('# model\n'*130+'class Item: created_at = None\n')
    (tmp_path/'tests/conftest.py').write_text('import pytest\nfrom tests.utils.user import create_test_user\n@pytest.fixture\ndef normal_user_token_headers(): return {}\n')
    (tmp_path/'tests/utils/user.py').write_text('def create_test_user(): return "sandbox"\n')
    tools=RepositoryTools(tmp_path,SimpleNamespace(write=lambda *a:None))
    sources,fixtures=preparation_context(tools,ExecutionProfile(fixture_files=['tests/conftest.py']),{'app/items.py'})
    assert 'normal_user_token_headers' in fixtures
    assert any('class Item: created_at' in ''.join(s['excerpts']) for s in sources)
    assert any(s['path']=='tests/utils/user.py' for s in sources)


def test_unknown_fixture_and_uncreated_account_are_rejected():
    assert script_setup_issues('def test_case(user_token_headers):\n assert user_token_headers\n',['normal_user_token_headers'])
    code='def test_case(client):\n r=client.post("/api/v1/login/access-token", data={"username":"unknown@example.com", "password":"unknown"})\n assert r.status_code==200\n'
    assert script_setup_issues(code,['client'])
    assert not script_setup_issues('def test_case(client, normal_user_token_headers):\n assert client\n',['client','normal_user_token_headers'])


def test_missing_runner_after_repair_discards_old_invalid_script(flow_app,tmp_path):
    from test_generated_pytest import prepare_pytest
    from app.infrastructure.test_design.pytest_builder import PytestArtifactBuilder
    from tda.infrastructure.artifacts import LocalArtifactStore
    from tda.domain.worker_artifacts import PytestDraft,ArtifactReview
    _,_,_,engine,_,_,w,case,root=prepare_pytest(flow_app,tmp_path)
    w.worker_artifacts.pop(case.id)
    class Agent:
        calls=0;max_calls=8
        def propose(self,stage,instructions,context,output):
            self.calls+=1
            if stage=='artifacts.review':return ArtifactReview(mapping=[],issues=['Live runner replaced by mock'])
            if self.calls==1:return PytestDraft(disposition='needs_review',reason='Draft',code='def test_case():\n assert 1 == 2\n')
            return PytestDraft(disposition='needs_setup',reason='Missing isolated live runner',prerequisites=['Socket.IO runner fixture'])
    artifacts=LocalArtifactStore(tmp_path/'missing-runner');agent=Agent()
    result=PytestArtifactBuilder(agent,RepositoryTools(root,artifacts),artifacts,engine.settings,w.execution_profiles).run(w,[case.id])
    state=result.automation_preparation[case.id]
    assert state['status']=='needs_setup' and 'failure_category' not in state
    assert state['reason']=='Missing isolated live runner'
    assert case.id not in result.worker_artifacts
    assert result.cases==w.cases
    calls=agent.calls
    cached=PytestArtifactBuilder(agent,RepositoryTools(root,artifacts),artifacts,engine.settings,w.execution_profiles,reuse_unavailable=True).run(result,[case.id])
    assert agent.calls==calls
    assert cached.automation_preparation[case.id]==state


def test_docker_infrastructure_failure_is_not_script_repair():
    from app.infrastructure.test_design.pytest_builder import infrastructure_blocked
    assert infrastructure_blocked('Invalid worker configuration: Docker environment failed: pipe missing')
    assert not infrastructure_blocked('E AssertionError: expected 201, got 500')


def test_generated_cases_cannot_leak_files_into_each_other(flow_app,tmp_path):
    from test_generated_pytest import prepare_pytest
    from app.infrastructure.execution.pytest_environment import run_artifacts
    _,_,_,engine,_,_,w,case,root=prepare_pytest(flow_app,tmp_path)
    first=w.worker_artifacts[case.id].model_copy(deep=True)
    first.artifact.content='from pathlib import Path\ndef test_case():\n Path("leaked-state").write_text("previous case")\n assert True\n'
    second=first.model_copy(deep=True);second.case_id='second'
    second.artifact.content='from pathlib import Path\ndef test_case():\n assert not Path("leaked-state").exists()\n'
    result=run_artifacts([first,second],root,engine.settings)
    assert result.verdict=='pass',result.output
    assert [r['status'] for r in result.summary['results']]==['pass','pass']
    assert result.summary['isolation']=='per_case'


def test_mapping_accepts_formatting_but_rejects_changed_expected(flow_app,tmp_path):
    import pytest
    from test_generated_pytest import prepare_pytest
    from tda.application.worker_artifacts import resolve_mapping
    from tda.domain.worker_artifacts import ReviewedAssertion
    _,_,_,_,_,_,w,case,_=prepare_pytest(flow_app,tmp_path)
    mapping=ReviewedAssertion(expected_key='step:0/expected:0',assertion_sources=['assert add( *operands )==5'],explanation='Same assertion AST')
    assert resolve_mapping(case,w.worker_artifacts[case.id].artifact.content,[mapping])[0].assertion_lines==[3]
    mapping.assertion_sources=['assert add(*operands)==4']
    with pytest.raises(ValueError):resolve_mapping(case,w.worker_artifacts[case.id].artifact.content,[mapping])


def test_preparation_repairs_invalid_fixture_and_retains_success(flow_app,tmp_path):
    from test_generated_pytest import prepare_pytest
    from app.infrastructure.test_design.pytest_builder import PytestArtifactBuilder
    from tda.infrastructure.artifacts import LocalArtifactStore
    from tda.domain.worker_artifacts import PytestDraft,ArtifactReview
    _,_,_,engine,_,_,w,case,root=prepare_pytest(flow_app,tmp_path)
    class Agent:
        calls=0;max_calls=8;writes=0
        def propose(self,stage,instructions,context,output):
            self.calls+=1
            if stage=='artifacts.write':
                self.writes+=1
                if self.writes==1:code='def test_case(missing_fixture):\n assert missing_fixture == 5\n'
                else:
                    assert any('missing_fixture' in e for e in context['repair_feedback'])
                    code='def test_case(operands):\n from service import add\n assert add(*operands) == 5\n'
                return PytestDraft(disposition='needs_review',reason='test repair',code=code)
            import ast
            node=next(n for n in ast.walk(ast.parse(context['code'])) if isinstance(n,ast.Assert))
            return ArtifactReview(mapping=[{'expected_key':'step:0/expected:0','assertion_sources':[ast.get_source_segment(context['code'],node)],'explanation':'Reviewed expected sum'}],issues=[])
    agent=Agent();artifacts=LocalArtifactStore(tmp_path/'repair-artifacts')
    builder=PytestArtifactBuilder(agent,RepositoryTools(root,artifacts),artifacts,engine.settings,w.execution_profiles)
    preserved=builder.run(w,[case.id]);assert agent.calls==0
    assert preserved.worker_artifacts[case.id]==w.worker_artifacts[case.id]
    w.worker_artifacts.pop(case.id)
    repaired=builder.run(w,[case.id])
    assert agent.calls==4 and repaired.worker_artifacts[case.id].preflight['status']=='pass'
    assert repaired.cases==w.cases
    assert repaired.worker_artifacts[case.id].state=='proposed'
