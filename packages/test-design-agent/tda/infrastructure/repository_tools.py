"""Read-only repository tools with path, secret-file and size boundaries."""
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from ..domain.contracts import SourceDocument,SourceBundle


class RepositoryTools:
    EXCLUDED={'.git','.venv','venv','node_modules','.aws','.codex','.agents','__pycache__','dist','build','.data'}
    EXTENSIONS={'.py','.js','.ts','.tsx','.jsx','.mjs','.cjs','.go','.java','.cs','.rb','.php','.rs','.vue','.svelte',
                '.md','.rst','.txt','.json','.yaml','.yml','.toml','.sql','.html','.css','.sh','.feature'}

    def __init__(self,root,artifacts,documents=(),max_snapshot_bytes=1000000):
        self.root=Path(root).resolve();self.artifacts=artifacts;self.max_snapshot_bytes=max_snapshot_bytes
        self.documents={d.path:d.model_copy(deep=True) for d in documents}
        self.files=[];self.observed={};self.calls=0;self.bytes=sum(len(d.content.encode('utf-8')) for d in documents);self.limitations=[]
        for directory,folders,files in os.walk(self.root,followlinks=False):
            folders[:]=sorted(f for f in folders if f not in self.EXCLUDED and not f.startswith('.test-') and not (Path(directory)/f).is_symlink())
            for name in sorted(files):
                path=Path(directory)/name
                if path.is_symlink() or name.startswith('.env') or name.lower().endswith(('.pem','.key','.p12')):continue
                if path.suffix.lower() in self.EXTENSIONS or name=='Dockerfile':
                    self.files.append(path.relative_to(self.root).as_posix())
                if len(self.files)>=3000:break
            if len(self.files)>=3000:self.limitations.append('Danh sách file bị giới hạn ở 3000 files.');break

    def allowed(self,path):
        if not path or '\\' in path or ':' in path or Path(path).is_absolute() or '..' in Path(path).parts:return False
        if self.EXCLUDED.intersection(Path(path).parts) or Path(path).name.startswith('.env'):return False
        if path in self.documents:return True
        target=(self.root/path).resolve()
        return path in self.files and target.is_relative_to(self.root) and not (self.root/path).is_symlink()

    def snapshot(self,path):
        if not self.allowed(path):raise ValueError('File không được phép đọc hoặc không có trong repository inventory.')
        if path in self.documents:return self.documents[path]
        file=self.root/path
        if file.stat().st_size>256000:raise ValueError('File quá lớn; chưa đọc. Chọn file/module nhỏ hơn.')
        content=file.read_text(encoding='utf-8');size=len(content.encode('utf-8'))
        if self.bytes+size>self.max_snapshot_bytes:raise ValueError('Đã đạt giới hạn source snapshots của job.')
        parts=Path(path).parts
        kind='existing_tests' if any(p in ('tests','test','__tests__') for p in parts) or Path(path).name.startswith('test_') or '.spec.' in path else 'code'
        digest=hashlib.sha256(content.encode()).hexdigest()
        doc=SourceDocument(id='repo-'+hashlib.sha256((path+digest).encode()).hexdigest()[:20],kind=kind,path=path,
                           sha256=digest,content=content)
        self.documents[path]=doc;self.bytes+=size
        return doc

    def read_file(self,path,start_line=1,end_line=120):
        doc=self.snapshot(path);lines=doc.content.splitlines(keepends=True)
        end_line=min(end_line,start_line+119,len(lines))
        if end_line<start_line:raise ValueError('Khoảng dòng không có nội dung.')
        excerpt=''.join(lines[start_line-1:end_line])[:12000]
        self.observed.setdefault(doc.id,[]).append(excerpt)
        return {'path':path,'source_id':doc.id,'sha256':doc.sha256,'start_line':start_line,
                'end_line':end_line,'total_lines':len(lines),'content':excerpt,'truncated':len(''.join(lines[start_line-1:end_line]))>12000}

    def list_files(self,path=''):
        if path in ('.','./'):path=''
        prefix=path.rstrip('/')
        if prefix and (':' in prefix or '..' in Path(prefix).parts or '\\' in prefix):raise ValueError('Đường dẫn không hợp lệ.')
        values=sorted(set(self.files)|set(self.documents))
        values=[p for p in values if not prefix or p==prefix or p.startswith(prefix+'/')]
        return {'files':values[:250],'total':len(values),'truncated':len(values)>250}

    def search(self,query,path='',references=False):
        if path in ('.','./'):path=''
        if not query.strip() or len(query)>200:raise ValueError('Search query phải có từ 1 đến 200 ký tự.')
        paths=self.list_files(path)['files'];matches=[];rg=shutil.which('rg')
        real=[p for p in paths if p in self.files]
        if rg and real:
            for start in range(0,len(real),60):
                result=subprocess.run([rg,'--line-number','--with-filename','--no-heading','--fixed-strings','--ignore-case','--max-count','3','--',query,*real[start:start+60]],
                    cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,check=False)
                for line in result.stdout.decode('utf-8',errors='replace').splitlines():
                    m=re.match(r'^(.*?):(\d+):(.*)$',line)
                    if m:matches.append({'path':m[1].replace('\\','/'),'line':int(m[2]),'text':m[3][:500]})
                if len(matches)>=30:break
        else:
            for p in real:
                file=self.root/p
                if file.stat().st_size>256000:continue
                for index,line in enumerate(file.read_text(encoding='utf-8',errors='replace').splitlines(),1):
                    if query.casefold() in line.casefold():matches.append({'path':p,'line':index,'text':line[:500]})
                    if len(matches)>=30:break
                if len(matches)>=30:break
        for p,doc in self.documents.items():
            if p not in real and (not path or p.startswith(path)):
                for index,line in enumerate(doc.content.splitlines(),1):
                    if query.casefold() in line.casefold():matches.append({'path':p,'line':index,'text':line[:500]})
        return {'query':query,'matches':matches[:30],'truncated':len(matches)>=30 or self.list_files(path)['truncated'],
                'note':'Đây là text references, chưa chứng minh call graph. Đọc file để xác minh.' if references else ''}

    def execute(self,request):
        self.calls+=1
        if self.calls>80:
            if 'Đã đạt giới hạn repository tool calls.' not in self.limitations:self.limitations.append('Đã đạt giới hạn repository tool calls.')
            return {'error':'Đã đạt giới hạn repository tool calls.'}
        try:
            if request.name=='list_files':result=self.list_files(request.path)
            elif request.name=='read_file':result=self.read_file(request.path,request.start_line,request.end_line)
            else:result=self.search(request.query,request.path,request.name=='find_references')
        except (ValueError,OSError,UnicodeError,subprocess.TimeoutExpired):result={'error':'Không đọc được nguồn theo request này; kiểm tra path/range/budget và thử công cụ khác.'}
        self.artifacts.write(f'tools/{self.calls:03d}.json',{'request':request.model_dump(),'result':result})
        return result

    def check_evidence(self,claims):
        known={d.id:d for d in self.documents.values()}
        for claim in claims:
            if claim.source_id not in known or not claim.quote.strip() or not any(claim.quote in text for text in self.observed.get(claim.source_id,[])):
                raise ValueError('Evidence chưa được đọc qua repository tools hoặc không khớp nguyên văn.')

    def sources(self):return SourceBundle(documents=list(self.documents.values()),operations=[])

    def excerpts(self,source_ids):
        result=[];remaining=32000
        for d in self.documents.values():
            if d.id not in source_ids:continue
            values=[]
            for text in reversed(self.observed.get(d.id,[])):
                if remaining<=0:break
                part=text[:min(remaining,6000)];values.append(part);remaining-=len(part)
            result.append({'source_id':d.id,'path':d.path,'kind':d.kind,'excerpts':values,'omitted':remaining<=0})
        return result
