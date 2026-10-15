"""Repair selected unconfirmed automation bindings using existing-store concurrency guards."""
import argparse
import sqlite3
from pathlib import Path
from app.infrastructure.persistence.design_workspace import WorkspaceStoreMixin
from tda.application.automation import repair_current_bindings


class ExistingStore(WorkspaceStoreMixin):
    def __init__(self,path):self.path=path
    def connect(self):return sqlite3.connect(self.path,timeout=30)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',type=Path,default=Path('.data/agent-qc.sqlite3'))
    parser.add_argument('--project',required=True)
    parser.add_argument('--case',action='append',required=True)
    args=parser.parse_args()
    if not args.database.is_file():raise ValueError('Database does not exist')
    store=ExistingStore(args.database);workspace=store.get_workspace(args.project)
    if workspace is None:raise ValueError('Workspace does not exist')
    updated=store.mutate_workspace(args.project,workspace.version,'agent','repair_automation_assertions',
        lambda current:repair_current_bindings(current,args.case))
    for identifier in args.case:
        binding=updated.bindings[identifier]
        print(identifier,'validation_issues=',len(binding.validation_issues),'state=',binding.state)
    print('Workspace version:',updated.version,'QC baseline unchanged; no LLM calls')


if __name__=='__main__':main()
