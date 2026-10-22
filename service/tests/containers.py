"""Owned native fixture containers. Each sees only its own credentials and private network."""
import asyncio
import json
import os
import sys

from .support import LAB
from .test_native import bridge

sys.path.insert(0,str(LAB.parent))
from lab import PINS,docker

class Containers:
    def __init__(self):
        self.manifest=json.loads((LAB/'manifest.json').read_text())
        self.names=[]
    async def start(self,purpose,args,files=(),directories=(),address=None,interactive=False):
        name=self.manifest['prefix']+'-native-'+purpose+'-'+os.urandom(3).hex()
        command=['docker','--host=unix:///var/run/docker.sock','run','--rm','--name',name,
            '--network',self.manifest['prefix']+'-transport','--user',f'{os.getuid()}:{os.getgid()}',
            '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--memory','256m','--pids-limit','64','--entrypoint','/bridge',
            '--mount',f'type=bind,src={bridge()},dst=/bridge,readonly',
            '--env','SSL_CERT_FILE='+str(LAB/'tls/ca.crt'),
            '--env','NO_PROXY=127.0.0.1,localhost,172.30.245.0/24,172.30.246.0/24,172.30.247.0/24',
            '--env','no_proxy=127.0.0.1,localhost,172.30.245.0/24,172.30.246.0/24,172.30.247.0/24']
        if interactive:command+=['-i']
        if address:command+=['--ip',address]
        for path in [LAB/'tls/ca.crt',*files]:
            command+=['--mount',f'type=bind,src={path},dst={path},readonly']
        for path,readonly in directories:
            command+=['--mount',f'type=bind,src={path},dst={path}'+(',readonly' if readonly else '')]
        command += [PINS['postgres'],*map(str,args)]
        self.names.append(name)
        env={k:v for k,v in os.environ.items() if k not in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH')}
        return await asyncio.create_subprocess_exec(*command,env=env,
            stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
            stderr=(LAB/(name+".diagnostic")).open("wb"))
    async def close(self):
        for name in reversed(self.names):
            await asyncio.to_thread(docker,'rm','-f',name,check=False)
