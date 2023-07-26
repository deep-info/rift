import asyncio
import logging
from typing import Any, List

import rift.lsp.types as lsp
from rift.agents.abstract import AgentRegistryResult
from rift.lsp.types import InitializeParams
from rift.rpc.io_transport import AsyncStreamTransport
from rift.rpc.jsonrpc import RpcServer, rpc_method, rpc_request
from rift.server.core import CodeCapabilitiesServer, rift_splash
from rift.util.ofdict import todict

if __name__ == "__main__":

    class MockLspClient(RpcServer):
        @rpc_request("morph/run")
        async def run(self, params: Any) -> Any:
            ...

        @rpc_request("initialize")
        async def initialize(self, params: InitializeParams) -> Any:
            ...

        @rpc_method("morph/chat_progress")
        async def chat_progress(self, params: Any):
            print("PROGRESS: ", params)

        @rpc_request("morph/listAgents")
        async def listAgents(self, params: Any) -> list[AgentRegistryResult]:
            ...

        @rpc_method("window/logMessage")
        async def logmessage(self, params: Any):
            ...

        @rpc_method("morph/code_completion_1_send_progress")
        async def chat_progress(self, params: Any):
            print("PROGRESS: ", params)

        @rpc_method("morph/rift_chat_1_send_progress")
        async def rift_chat_progress(self, params: Any):
            print("RIFT CHAT PROGRESS: ", params)

        @rpc_method("morph/rift_chat_1_request_chat")
        async def rift_chat_request(self, params: Any):
            print("RIFT CHAT MESSAGES: ", params["messages"])
            return {"message": input("what do you want to say?\n")}

        @rpc_method("morph/smol_dev_1_request_chat")
        async def smol_agent_chat(self, params: Any):
            print("SMOL CHAT: ", params)
            return {"message": "print hello world in a python file"}

        @rpc_method("morph/smol_dev_1_send_progress")
        async def smol_agent_progress(self, params: Any):
            print("SMOL PROGRESS: ", params)

        @rpc_method("window/logMessage")
        async def logmessage(self, params: Any):
            ...

        @rpc_method("workspace/applyEdit")
        async def applyEdit(self, params: Any) -> lsp.ApplyWorkspaceEditResponse:
            print("**********************")
            print("**********************")
            print("**********************")
            print("**********************")
            print("**********************")
            print("EDIT: ", params)
            return {"applied": True}

        @rpc_request("textDocument/didOpen")
        async def on_did_open(self, params: lsp.DidOpenTextDocumentParams):
            ...

    async def main():
        reader, writer = await asyncio.open_connection("127.0.0.1", 7797)
        transport = AsyncStreamTransport(reader, writer)
        client = MockLspClient(transport=transport)
        t = asyncio.create_task(client.listen_forever())
        print("CAPABILITIES: ", await client.initialize(params=InitializeParams()))

        print("AGENTS: ", await client.listAgents({}))

        # await t
        # from pydantic import BaseModel
        # from rift.server.chat_agent import RunChatParams

        # register a file
        on_did_open_params = lsp.DidOpenTextDocumentParams(
            textDocument=lsp.TextDocumentItem(
                text="yeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeehaw",
                uri="file:///home/pv/Downloads/yeehaw-dev/yeehaw.py",
                languageId="python",
                version=0,
            )
        )
        print("REGISTER FILE: ", await client.on_did_open(params=on_did_open_params))

        import rift.agents.rift_chat as agentchat

        chat_agent_params = dict(
            textDocument=lsp.TextDocumentIdentifier(
                uri="file:///home/pv/Downloads/yeehaw-dev/yeehaw.py", version=0
            ),
            position=None,
        )
        # from rift.server.lsp import AgentRunParams

        params = dict(agent_type="rift_chat", agent_params=chat_agent_params, agent_id="1")

        print(await client.run(params=params))

        # from rift.agents.smol import SmolAgentParams
        # from rift.server.lsp import AgentRunParams

        # class RunParams(BaseModel):
        #     agent_type: str = "chat"

        # params = todict(
        #     AgentRunParams(agent_type="smol_dev", agent_params=SmolAgentParams(instructionPrompt="write hello world in Python", position=lsp.Position(0,0), textDocument=lsp.TextDocumentIdentifier(uri="file:///home/pv/Downloads/yeehaw-dev/yeehaw.py", version=0)))
        # )
        # print("RUN RESULT: ", await client.run(params=params))
        # print("initialized")
        # await t
        await t

    asyncio.run(main())
