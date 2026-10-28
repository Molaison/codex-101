#!/usr/bin/env bash
# Codex101 optional Pi profile. Existing Pi profiles and Codex are untouched.
set -Eeuo pipefail
command -v python3 >/dev/null
python3 -c 'import sys; assert sys.version_info >= (3,8), "Python 3.8+ required"'
export PATH="$HOME/.local/bin:$PATH"
if ! command -v pi >/dev/null 2>&1; then
    curl --http1.1 -fsSL --retry 2 https://pi.dev/install.sh | sh
fi
export CODEX101_PI_BINARY
CODEX101_PI_BINARY=$(command -v pi)
pi --version
python3 - <<'PY'
import getpass,json,os,shlex,stat,subprocess,tempfile,urllib.request
from pathlib import Path
home=Path.home()
root=home/'.local/share/codex101-pi'
if root.is_symlink(): raise SystemExit('Codex101 Pi directory must not be a symlink.')
root.mkdir(mode=0o700,parents=True,exist_ok=True)
root.chmod(0o700)
agent=root/'agent'
if agent.is_symlink(): raise SystemExit('Pi profile directory must not be a symlink.')
agent.mkdir(mode=0o700,exist_ok=True)
agent.chmod(0o700)
url='https://cpr.molaisonz.dpdns.org/v1'
key=os.environ.get('CODEX101_PI_API_KEY','').strip()
previous=agent/'auth.json'
if not key and previous.is_file() and not previous.is_symlink():
    key=json.loads(previous.read_text()).get('cpr',{}).get('key','')
connection=Path(os.environ.get('CODEX_HOME',str(home/'.codex')))/'provider-connect/connection.json'
if not key and connection.is_file():
    saved=json.loads(connection.read_text())
    if saved.get('url','').rstrip('/')==url:
        key=saved.get('api_key','')
if not key:
    with open('/dev/tty','r+') as terminal:
        key=getpass.getpass('CPR API key: ',stream=terminal).strip()
if not key or '\r' in key or '\n' in key: raise SystemExit('A single-line CPR API key is required.')
# Authenticate without a model request; system curl keeps the existing proxy/TLS
# policy, with the key in stdin rather than argv or a temporary file.
config='url = '+json.dumps(url+'/models?client_version=0.165.0')+'\nheader = '+json.dumps('Authorization: Bearer '+key)+'\n'
probe=subprocess.run(['curl','--config','-','--http1.1','--fail','--silent','--show-error','--max-time','30'],input=config.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
if probe.returncode: raise SystemExit('CPR model preflight failed; Pi configuration was not replaced.')
body=json.loads(probe.stdout)
visible={m.get('id',m.get('slug')) for m in body.get('data',body.get('models',[]))}
if 'gpt-6.1-sol' not in visible: raise SystemExit('This key cannot access gpt-6.1-sol; Pi configuration was not replaced. Model response shape: '+str(list(body))+'; visible count: '+str(len(visible)))
def write(path,data):
    if path.is_symlink(): raise SystemExit('Refusing to replace symlink: '+str(path))
    fd,name=tempfile.mkstemp(prefix='.pending-',dir=str(path.parent))
    try:
        with os.fdopen(fd,'w') as output: output.write(data)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)
models=[{'id':'gpt-6.1-sol','name':'CPR Sol','reasoning':True}]
if 'DeepSeek-V4.1-Flash' in visible:
    models.append({'id':'DeepSeek-V4.1-Flash','name':'CPR DeepSeek Flash','reasoning':False})
write(agent/'models.json',json.dumps({'providers':{'cpr':{'baseUrl':url,'api':'openai-responses','models':models}}},indent=2)+'\n')
write(agent/'auth.json',json.dumps({'cpr':{'type':'api_key','key':key}})+'\n')
write(agent/'settings.json',json.dumps({'defaultProvider':'cpr','defaultModel':'gpt-6.1-sol','defaultThinkingLevel':'high'})+'\n')
launcher=home/'.local/bin/pi-cpr'
launcher.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
marker='# Managed by codex-101 Pi profile'
if launcher.exists() and (launcher.is_symlink() or marker not in launcher.read_text()):
    raise SystemExit('pi-cpr already exists and is not owned by codex-101; it was preserved.')
write(launcher,'#!/bin/sh\n'+marker+'\nexport PI_CODING_AGENT_DIR='+shlex.quote(str(agent))+'\nexec '+shlex.quote(os.environ['CODEX101_PI_BINARY'])+' "$@"\n')
launcher.chmod(0o700)
print('Pi 已配置：运行 ~/.local/bin/pi-cpr。默认 CPR / gpt-6.1-sol / high。')
print('模型列表与认证已配置；尚未提交真实模型请求。')
PY
