from app.infrastructure.test_design.comparison import compare_reference


def observation(status,kind='json_equals'):
    return {'case_id':'C','status':status,'steps':[{'index':0,'failed_assertions':[{'kind':kind,'pointer':'/value'}] if status=='defect' else []}]}


def test_missing_or_unusable_reference_does_not_claim_a_new_regression():
    for reference in (None,{'results':[]},{'results':[observation('environment_error')]}):
        assert compare_reference([observation('defect')],reference)[0]['comparison']=='unclassified_failure'


def test_changed_failure_is_distinct_from_known_reference_failure():
    reference={'results':[observation('defect')]}
    assert compare_reference([observation('defect','status')],reference)[0]['comparison']=='changed_failure'
    assert compare_reference([observation('defect')],reference)[0]['comparison']=='existing_failure'
