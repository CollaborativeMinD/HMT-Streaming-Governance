"""Run the separate unittest suite and emit portable XML without another test dependency."""
import json
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).parents[1]
class Result(unittest.TextTestResult):
    def startTest(self,test):
        self.started=time.perf_counter();super().startTest(test)
    def stopTest(self,test):
        self.records.append((test.id(),time.perf_counter()-self.started));super().stopTest(test)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.records=[]

def main():
    suite=unittest.defaultTestLoader.loadTestsFromName('tests.unittest_cross')
    result=unittest.TextTestRunner(resultclass=Result,verbosity=1).run(suite)
    root=ET.Element('testsuite',name='durable-cross-unittest',tests=str(result.testsRun),failures=str(len(result.failures)),errors=str(len(result.errors)))
    failed={test.id():value for test,value in result.failures};errors={test.id():value for test,value in result.errors}
    for identifier,duration in result.records:
        case=ET.SubElement(root,'testcase',classname=identifier.rsplit('.',1)[0],name=identifier.rsplit('.',1)[1],time=str(duration))
        for tag,values in [('failure',failed),('error',errors)]:
            if identifier in values:ET.SubElement(case,tag).text=values[identifier]
    ET.ElementTree(root).write(ROOT/'evidence/unittest.xml',encoding='utf-8',xml_declaration=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
if __name__=='__main__':main()
