"""Expose Hercules' real assertion flags in JUnit, without changing its verdict."""
import runpy


def instrument_junit():
    from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator
    original=JUnitXMLGenerator.add_test_case
    def add_test_case(self,scenario,feature,json_data,execution_time,cost_metric):
        data={**json_data,'qc_is_assert':json_data.get('is_assert') is True,
            'qc_is_passed':json_data.get('is_passed') is True,
            'qc_assert_summary':str(json_data.get('assert_summary') or '')}
        return original(self,scenario,feature,data,execution_time,cost_metric)
    JUnitXMLGenerator.add_test_case=add_test_case


if __name__=='__main__':
    instrument_junit()
    runpy.run_module('testzeus_hercules',run_name='__main__')
