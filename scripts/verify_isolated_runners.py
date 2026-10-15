"""Real Docker + pytest isolation smoke check. No GitHub or LLM calls."""
import sys
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[1]))
import socket
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from app.infrastructure.target.docker import DockerTargetProvisioner
from tda.runner_native import unit_case
import httpx


def main():
    with tempfile.TemporaryDirectory(prefix='qc-isolation-') as temporary:
        root=Path(temporary)
        (root/'probe.py').write_text('import os,socket\ndef probe():\n    secret=os.environ.get("OPENAI_API_KEY")\n    try:\n        socket.create_connection(("1.1.1.1",443),timeout=.2)\n        blocked=False\n    except OSError:\n        blocked=True\n    return {"secret":secret,"network_blocked":blocked}\n',encoding='utf-8')
        (root/'server.py').write_text('import sys\nfrom http.server import HTTPServer,BaseHTTPRequestHandler\nclass Handler(BaseHTTPRequestHandler):\n    def do_GET(self):\n        self.send_response(200);self.end_headers();self.wfile.write(b"ready")\nHTTPServer(("0.0.0.0",int(sys.argv[1])),Handler).serve_forever()\n',encoding='utf-8')
        case={'id':'isolation','unit':{'language':'python','module_path':'probe.py','function':'probe','args_json':'[]','kwargs_json':'{}'},
              'oracle':{'steps':[{'step_index':0,'assertions':[{'kind':'return_equals','expected_json':'{"secret":null,"network_blocked":true}','pointer':''}]}]}}
        builder=DockerTargetProvisioner();run=SimpleNamespace(run_id='isolation-smoke')
        image=builder.prepare_unit(run,root)
        result=unit_case(case,root,docker_image=image)
        assert result['status']=='pass',result
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        analysis=SimpleNamespace(config={'target':{'command':['python','server.py',str(port)],'base_url':url,'healthcheck':url+'/'}})
        try:
            target=builder.provision(run,analysis,root)
            assert httpx.get(target.ref,trust_env=False).text=='ready'
            print(json.dumps({'docker_unit':'passed','backend_key_inherited':False,'unit_network':'blocked','docker_http':'passed'}))
        finally:builder.cleanup(run.run_id)


if __name__=='__main__':main()
