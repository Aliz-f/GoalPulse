"""Generate a machine-readable planning board from owner task docs.

This is planning metadata, not a job runner; it does not execute agents.
"""
from __future__ import annotations
import re
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
OWNERS = {1: 'Developer 1 — Core & Infrastructure', 2: 'Developer 2 — AI & Frontend', 3: 'Developer 3 — Web Intelligence', 4: 'Developer 4 — Decision & Actions'}
TASK_HEADER = re.compile(r'^###\s+(D[1-4]-[A-Z]+-\d{2})\s+—\s+(.+?)\s+\[([A-Z]\d)(?:,\s*([^]]+))?\]$', re.MULTILINE)

def parse():
    tasks=[]
    for number in range(1,5):
        text=(ROOT/f'tasks/developer-{number}.md').read_text(encoding='utf-8')
        headers=list(TASK_HEADER.finditer(text))
        for i,m in enumerate(headers):
            chunk=text[m.end(): headers[i+1].start() if i+1<len(headers) else len(text)]
            fields={}
            for raw in chunk.splitlines():
                mt=re.match(r'^-\s+\*\*(.+?):\*\*\s*(.*)$',raw)
                if mt:
                    fields[mt.group(1).strip().lower()]=mt.group(2).strip()
            deps=fields.get('dependencies', 'none')
            refs=[]
            for match in re.finditer(r'(D[1-4]-[A-Z]+-)(\d{2}(?:/\d{2})*)',deps):
                refs.extend(match.group(1)+part for part in match.group(2).split('/'))
            task={
                'id':m.group(1), 'title':m.group(2).strip(), 'owner':number,
                'priority':m.group(3),'stage_hint':(m.group(4) or '').strip(),
                'status':'todo', 'dependencies':list(dict.fromkeys(refs)),
                'dependencies_note':deps,
                'allowed_paths':fields.get('allowed files',fields.get('allowed','Owner service only / see task entry')),
                'implementation':fields.get('do',fields.get('implement','See owner task description')),
                'acceptance':fields.get('acceptance',fields.get('accept','See owner task description')),
                'source':f'tasks/developer-{number}.md',
            }
            tasks.append(task)
    return tasks

if __name__=='__main__':
    tasks=parse()
    outfile=ROOT/'tasks/task-index.yaml'
    outfile.write_text(yaml.safe_dump({'version':1,'status':'planned','owners':OWNERS,'tasks':tasks},sort_keys=False,allow_unicode=True),encoding='utf-8')
    print('Created',outfile,'with',len(tasks),'tasks')
