import asyncio
import logging
import uuid
from asyncio import Future
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Optional
from rift.util import file_diff
import os
import re
import types

import rift.lsp.types as lsp
from rift.agents.abstract import (
    Agent,
    AgentProgress,  # AgentTask,
    AgentRunParams,
    AgentRunResult, 
    AgentState,
    RequestChatRequest,
    RequestInputRequest,
    RequestChatRequest,
    RunAgentParams,
    agent,
)
import typer
from pathlib import Path

from rift.agents.agenttask import AgentTask
from rift.llm.abstract import AbstractCodeCompletionProvider, InsertCodeResult
from rift.lsp import LspServer as BaseLspServer
from rift.lsp.document import TextDocumentItem
from rift.server.selection import RangeSet

try:
    import gpt_engineer
    import gpt_engineer.chat_to_files
    import gpt_engineer.db

except ImportError:
    raise Exception("`gpt_engineer` not found. Try `pip install gpt-engineer`")

UPDATES_QUEUE = asyncio.Queue()
INPUT_PROMPT_QUEUE = asyncio.Queue()
INPUT_RESPONSE_QUEUE = asyncio.Queue()
TASK_QUEUE = asyncio.Queue()
OUTPUT_CHAT_QUEUE = asyncio.Queue()

STEPS_AGENT_TASKS_NAME_QUEUE = asyncio.Queue()
STEPS_AGENT_TASKS_EVENT_QUEUE = asyncio.Queue()

SEEN = set()

from gpt_engineer.ai import AI, fallback_model
from gpt_engineer.collect import collect_learnings
from gpt_engineer.db import DB, DBs, archive
from gpt_engineer.learning import collect_consent
from gpt_engineer.steps import STEPS
from gpt_engineer.steps import Config as StepsConfig
import threading
import json
import rift.llm.openai_types as openai

logger = logging.getLogger(__name__)

def __fix_windows_path(path: str) -> str:
    """
    Replace a windows path represented as "/c%3A"... with "c:"...

    :param path: Original path
    :return: Usable windows path, or original path if not a windows path
    """
    pattern = r'^/(.)%3A'

    match = re.match(pattern, path)
    
    if match:
        drive_letter = match.group(1)
        return path.replace(f"/{drive_letter}%3A", f"{drive_letter}:")
    else:
        return path

async def __popup_input(prompt: str) -> str:
    await INPUT_PROMPT_QUEUE.put(prompt)
    while True:
        try:
            resp = await asyncio.wait_for(INPUT_RESPONSE_QUEUE.get(), timeout=1.0)
            break  
        except:
            continue  
    return resp


def __popup_input_wrapper(prompt: str="") -> str:
    try: 
        loop = asyncio.get_running_loop()
        result_future = loop.create_future()

        tsk = loop.create_task(__popup_input(prompt))
        tsk.add_done_callback(
            lambda t: result_future.set_result(t.result())
        )

        while not result_future.done():
            loop.run_until_complete(asyncio.sleep(0.1))

        return result_future.result()
    except:
        return asyncio.run(__popup_input(prompt))

gpt_engineer.steps.input = __popup_input_wrapper

async def __popup_chat(prompt: str="NONE", end=""):
    await OUTPUT_CHAT_QUEUE.put(prompt)

def __popup_chat_wrapper(prompt: str="NONE", end=""):
    try:
        loop = asyncio.get_running_loop()
        tsk = loop.create_task(__popup_chat(prompt, end))
        tsk.add_done_callback(
            lambda t: print(f"Task completed with: {t.result()}")
        )
    except:
        asyncio.run(__popup_chat(prompt, end))

gpt_engineer.ai.print = __popup_chat_wrapper
gpt_engineer.steps.print = __popup_chat_wrapper

from asyncio import Lock

response_lock = Lock()   

async def _main(
    project_path: any = "",
    model: str = "gpt-4",
    temperature: float = 0.1,
    steps_config: StepsConfig = StepsConfig.DEFAULT,
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    **kwargs,
) -> DBs:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    model = fallback_model(model)
    ai = AI(
        model=model,
        temperature=temperature,
    )


    input_path = __fix_windows_path(project_path.__fspath__())
    input_path = os.path.sep.join(input_path.split(os.path.sep)[:-1])
    memory_path = input_path +"/"+ "memory"
    workspace_path = input_path +"/"+ "workspace"
    archive_path = input_path +"/"+ "archive"

    

    dbs = DBs(
        memory=DB(memory_path),
        logs=DB(memory_path +"/"+ "logs"),
        input=DB(input_path),
        workspace=DB(workspace_path, in_memory_dict={}),  # in_memory_dict={}),
        preprompts=DB(Path(gpt_engineer.__file__).parent / "preprompts"),
        archive=DB(archive_path),
    )

    if steps_config not in [
        StepsConfig.EXECUTE_ONLY,
        StepsConfig.USE_FEEDBACK,
        StepsConfig.EVALUATE,
    ]:
        archive(dbs)

    steps = STEPS[steps_config]
    from concurrent import futures

    # Add all steps to task list
    for step in steps:
        await STEPS_AGENT_TASKS_NAME_QUEUE.put(step.__name__)

    counter = 0
    with futures.ThreadPoolExecutor(1) as pool:
        for step in steps:
            await asyncio.sleep(0.1)
            messages = await asyncio.get_running_loop().run_in_executor(pool, step, ai, dbs)
            await asyncio.sleep(0.1)
            dbs.logs[step.__name__] = json.dumps(messages)
            items = list(dbs.workspace.in_memory_dict.items())
            if len([x for x in items if x[0] not in SEEN]) > 0:
                await UPDATES_QUEUE.put([x for x in items if x[0] not in SEEN])
                for x in items:
                    if x[0] in SEEN:
                        pass
                    else:
                        SEEN.add(x[0])
            
            # Mark this step as complete
            event = await STEPS_AGENT_TASKS_EVENT_QUEUE.get()
            event.set()
            await asyncio.sleep(0.5)
            counter += 1


# dataclass for representing the result of the code completion agent run
@dataclass
class EngineerRunResult(AgentRunResult):
    ...



@dataclass
class EngineerAgentParams(AgentRunParams):
    textDocument: lsp.TextDocumentIdentifier
    instructionPrompt: Optional[str] = None


@dataclass
class EngineerProgress(
    AgentProgress
):  # reports what tasks are active and responsible for reporting new tasks
    response: Optional[str] = None
    done_streaming: bool = False

@dataclass
class EngineerAgentState(AgentState):
    model: AbstractCodeCompletionProvider
    document: lsp.TextDocumentItem
    params: EngineerAgentParams
    messages: list[openai.Message]
    change_futures: Dict[str, Future] = field(default_factory=dict)
    _done: bool = False



# decorator for creating the code completion agent
@agent(
    agent_description="Generate code following an and ask clarifying questions.",
    display_name="GPT Engineer",
)
@dataclass
class EngineerAgent(Agent):
    state: EngineerAgentState
    agent_type: ClassVar[str] = "engineer"
       
    @classmethod
    def create(cls, params: EngineerAgentParams, model, server):
        state = EngineerAgentState(
            model=model,
            document=server.documents[params.textDocument.uri],
            params=params,
            messages=[openai.Message.assistant("Hello! How can I help you today?")],

        )
        obj = cls(
            state=state,
            agent_id=params.agent_id,
            server=server,
        )


        async def __run_chat_thread(obj):
            print("Started handler thread")
            
            while True:
                try:
                    response = ""
                    toSend = await asyncio.wait_for(OUTPUT_CHAT_QUEUE.get(), timeout=1.0)   
                    if toSend != "NONE":                 
                        for delta in toSend:
                            response += delta
                            async with response_lock:
                                await obj.send_progress(EngineerProgress(response=response))
                        await obj.send_progress(EngineerProgress(response=response, done_streaming=True))
                        async with response_lock:
                            obj.state.messages.append(openai.Message.assistant(content=response))
                except asyncio.TimeoutError:
                    continue


        async def __run_popup_thread(obj):
            while True:
                try:
                    prompt = await asyncio.wait_for(INPUT_PROMPT_QUEUE.get(), timeout=1.0)
                    if prompt != "":
                        await asyncio.wait_for(OUTPUT_CHAT_QUEUE.put(prompt), timeout=1.0)
                    response = await obj.request_chat(RequestChatRequest(messages=obj.state.messages))
                    async with response_lock:
                        obj.state.messages.append(openai.Message.user(content=response))
                    await INPUT_RESPONSE_QUEUE.put(response)
                        
                except asyncio.TimeoutError:
                    continue

        async def __run_create_task_thread(obj: EngineerAgent):
            while True:
                try:
                    # Wait till we get the name of a new task to spawn
                    task_name = await asyncio.wait_for(STEPS_AGENT_TASKS_NAME_QUEUE.get(), timeout=1.0)
                    # Create an event to trigger when the task is complete
                    event = asyncio.Event()
                    # Put the event object on the queue so that it can be triggered
                    await STEPS_AGENT_TASKS_EVENT_QUEUE.put(event)
                    # Setup the function that will be waiting on the event
                    async def __event_wait():
                        await event.wait()
                        return True

                    _ = asyncio.get_running_loop().create_task(obj.add_task(AgentTask(task_name, __event_wait)).run())

                    await obj.send_progress()
                except asyncio.TimeoutError:
                    continue

        asyncio.create_task(__run_create_task_thread(obj))
        asyncio.create_task(__run_chat_thread(obj))
        asyncio.create_task(__run_popup_thread(obj))


        return obj
    

    async def run(self) -> AgentRunResult:  # main entry point
        await self.send_progress()
        main_t = asyncio.create_task(_main(project_path=self.state.document))
        counter = 0
        while (not main_t.done()) or (UPDATES_QUEUE.qsize() > 0):
            counter += 1
            try:
                updates = await asyncio.wait_for(UPDATES_QUEUE.get(), 1.0)
                for file_path, new_contents in updates:
                    await self.server.apply_workspace_edit(lsp.ApplyWorkspaceEditParams(file_diff.edits_from_file_change(file_diff.get_file_change(
                        file_path, new_contents
                    ))))
                

            except asyncio.TimeoutError:
                continue