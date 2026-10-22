"""Host-configured persistent native Tailcat client. No message-controlled executable or endpoint."""
import asyncio

from .contracts import Rejected,canonical,strict_json


class Client:
    def __init__(self,bridge,key_file,endpoint_file):
        self.command=(str(bridge),'session',str(key_file),str(endpoint_file))
        self.process=None
        self.lock=asyncio.Lock()

    async def start(self):
        if self.process is not None:
            raise Rejected('CLIENT_ALREADY_STARTED')
        self.process=await asyncio.create_subprocess_exec(*self.command,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,limit=1_100_001)
        return self

    async def request(self,operation,arguments):
        raw=canonical(dict(operation=operation,arguments=arguments)).encode()+b'\n'
        if len(raw)>1_100_000:
            raise Rejected('BODY_CAPACITY')
        async with self.lock:
            if not self.process or self.process.returncode is not None:
                raise Rejected('TRANSPORT_UNAVAILABLE','HELD')
            try:
                async with asyncio.timeout(15):
                    self.process.stdin.write(raw)
                    await self.process.stdin.drain()
                    response=strict_json(await self.process.stdout.readline())
            except (OSError,ValueError,TimeoutError,Rejected):
                await self.close()
                raise Rejected('TRANSPORT_OUTCOME_UNKNOWN','HELD') from None
            if response.get('ok') is not True:
                raise Rejected(response.get('code','INVALID_RESPONSE'),response.get('disposition','HELD'))
            return response['result']

    async def governance_commit_state(self,value,request_key):
        return await self.request('commit',dict(value=value,request_key=request_key))

    async def governance_verify_payload(self,envelope):
        return await self.request('verify',dict(envelope=envelope))

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(),2)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
