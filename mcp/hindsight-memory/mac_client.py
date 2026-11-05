"""Launch the shared Hindsight stdio bridge over a configured SSH connection."""
import base64
import ipaddress
import os
from pathlib import Path
import re


def ssh_target(user, host, port):
    ipaddress.IPv4Address(host)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', user) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Invalid SSH user or port.')
    return user + '@' + host


source = Path((Path(__file__).parent / 'config-path').read_text().strip())
values = {}
for line in source.read_text(encoding='utf-8-sig').splitlines():
    match = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', line)
    if match:
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] in ('\"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        values[match.group(1)] = value
network = values['HINDSIGHT_NETWORK']
if network not in ('lan', 'easytier'):
    raise ValueError('HINDSIGHT_NETWORK must be lan or easytier.')
cfg = {
    'ssh_user': values['HINDSIGHT_WIN_SSH_USER'],
    'ssh_port': int(values['HINDSIGHT_WIN_SSH_PORT']),
    'host_key_alias': values.get('HINDSIGHT_WIN_SSH_HOST_KEY_ALIAS'),
    'remote_python': values['HINDSIGHT_WIN_PYTHON'],
    'remote_bridge': values['HINDSIGHT_WIN_MCP_BRIDGE'],
}
host = values['HINDSIGHT_WIN_LAN_HOST'] if network == 'lan' else values['HINDSIGHT_WIN_EASYTIER_HOST']
target = ssh_target(cfg['ssh_user'], host, cfg['ssh_port'])
opts = ['-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']
command = ['/usr/bin/ssh', *opts, '-p', str(cfg['ssh_port'])]
if cfg.get('host_key_alias'):
    ipaddress.IPv4Address(cfg['host_key_alias'])
    command += ['-o', 'HostKeyAlias=' + cfg['host_key_alias']]
paths = [cfg['remote_python'], cfg['remote_bridge']]
for path in paths:
    if not path.startswith('D:\\Hindsight\\') or any(c in path for c in "'\r\n"):
        raise ValueError('Remote executable paths must stay inside D:\\Hindsight.')
script = "$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; & '" + paths[0] + "' '" + paths[1] + "'; exit $LASTEXITCODE"
encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
command += [target, 'powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]
os.execv(command[0], command)
