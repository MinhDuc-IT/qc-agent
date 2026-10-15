"""Compare observations against a hashed reference snapshot, without changing verdicts."""


def failure_signature(result):
    return sorted((step.get('index'),failure.get('kind'),failure.get('pointer',''))
        for step in result.get('steps',[]) for failure in step.get('failed_assertions',[]))


def compare_reference(results,reference):
    previous={result['case_id']:result for result in reference.get('results',[])} if isinstance(reference,dict) else {}
    compared=[]
    for result in results:
        old=previous.get(result['case_id']);classification=None
        if result['status']=='defect':
            if not old or old['status'] not in {'pass','defect'}:classification='unclassified_failure'
            elif old['status']=='pass':classification='new_failure'
            elif failure_signature(old) and failure_signature(old)==failure_signature(result):classification='existing_failure'
            else:classification='changed_failure'
        elif result['status']=='pass' and old and old['status']=='defect':classification='resolved_failure'
        compared.append({**result,'reference_status':old['status'] if old else None,'comparison':classification})
    return compared
