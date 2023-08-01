"""
This module defines the abstract base classes and types for the Agent API.
"""

import asyncio
import logging
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Type

import rift.lsp.types as lsp
from rift.agents.agenttask import AgentTask
from rift.llm.openai_types import Message as ChatMessage
from rift.lsp import LspServer as BaseLspServer
from rift.lsp import rpc_method

logger = logging.getLogger(__name__)


"""
Status is an enumeration representing the possible states of an agent task.
"""


class Status(Enum):
    running = "running"
    done = "done"
    error = "error"
    accepted = "accepted"
    rejected = "rejected"


@dataclass
class RequestInputRequest:
    msg: str
    place_holder: str = ""


@dataclass
class RequestInputResponse:
    response: str


@dataclass
class RequestChatRequest:
    messages: List[ChatMessage]


@dataclass
class RequestChatResponse:
    message: ChatMessage  # TODO make this richer


AgentTaskId = str


@dataclass
class AgentRunParams(ABC):
    agent_id: str
    textDocument: lsp.TextDocumentIdentifier
    selection: Optional[lsp.Selection]
    workspaceFolderPath: str


@dataclass
class RunAgentParams:
    agent_type: str
    agent_params: Any
    agent_id: Optional[str]


@dataclass
class AgentProgress:
    agent_type: Optional[str] = None
    agent_id: Optional[str] = None
    tasks: Optional[Dict[str, Any]] = None
    payload: Optional[Any] = None


@dataclass
class AgentRunResult(ABC):
    """
    Abstract base class for AgentRunResult
    """


@dataclass
class AgentState(ABC):
    """
    Abstract base class for AgentState. Always contains a copy of the params used to create the Agent.
    """

    params: AgentRunParams


@dataclass
class Agent:
    """
    Agent is the base class for all agents.

    `agent_type` is a string that is defined in the source code and represents the type of the agent.
    `agent_id` is a unique identifier for the agent that is generated by convention in the lsp's handler for 'morph/run'.
    `state` is a namespace that encapsulates all special state for the agent.
    `tasks` is a list of `AgentTask`s and is used to report the progress of the agent.
    `server` is a handle to the global language server.
    """

    agent_type: ClassVar[str]
    server: Optional[BaseLspServer] = None
    state: Optional[AgentState] = None
    agent_id: Optional[str] = None
    tasks: List[AgentTask] = field(default_factory=list)
    task: Optional[AgentTask] = None
    params_cls: ClassVar[Any] = AgentRunParams

    def get_display(self):
        """Get agent display information"""
        return self.agent_type, self.description

    def __str__(self):
        """Get string representation of the agent"""
        return f"<{self.agent_type}> {self.agent_id}"

    @classmethod
    async def create(cls, params: RunAgentParams, server: BaseLspServer, *args, **kwargs):
        """
        Factory function which is responsible for constructing the agent's state.
        """
        ...

    async def main(self):
        """
        The main method called by the LSP server to handle method `morph/run`.

        This method:
            - Creates a task to be run
            - Logs the status of the running task
            - Awaits the result of the running task
            - Sends progress of the task
            - Handles cancellation and exception situations

        Raises:
            asyncio.CancelledError: If the task being run was cancelled.
        """
        # Create a task to run with assigned description and run method
        self.task = AgentTask(description=self.agent_type, task=self.run)

        try:
            # Log the status of the running task
            logger.info(f"{self} running")

            # Await to get the result of the task
            result_t = asyncio.create_task(self.task.run())
            await self.send_progress()
            result = await result_t

            # Send the progress of the task
            await self.send_progress()
            return result
        except asyncio.CancelledError as e:
            # Log information if task is cancelled
            logger.info(f"{self} cancelled: {e}")

            # Call the cancel method if a CancelledError exception happens
            await self.cancel()

    async def run(self) -> AgentRunResult:
        """
        Run the agent.
        """
        ...

    def set_tasks(self, tasks: List[AgentTask]):
        self.tasks = tasks

    def add_task(self, *args, **kwargs):
        """
        Register a subtask.
        """
        task = AgentTask(*args, **kwargs)
        self.tasks.append(task)
        return task

    async def cancel(self, msg: Optional[str] = None, doesSendProgress=True):
        """
        Cancel all tasks and update progress. Assumes that `Agent.main()` has been called and that the main task has been created.
        """
        if self.task.cancelled:
            return
        logger.info(f"{self.agent_type} {self.agent_id} cancel run {msg or ''}")
        self.task.cancel()
        for task in self.tasks:
            if task is not None:
                task.cancel()
        if doesSendProgress:
            await self.send_progress()

    async def request_input(self, req: RequestInputRequest) -> str:
        """
        Prompt the user for more information.
        """
        try:
            response = await self.server.request(
                f"morph/{self.agent_type}_{self.agent_id}_request_input", req
            )
            return response["response"]
        except Exception as e:
            logger.info(f"Caught exception in `request_input`, cancelling Agent.run(): {e}")
            await self.cancel()
            raise asyncio.CancelledError

    async def send_update(self, msg: str):
        """
        Creates a notification toast in the Rift extension by default.
        """
        await self.server.notify(
            f"morph/{self.agent_type}_{self.agent_id}_send_update",
            {"msg": f"[{self.agent_type}] {msg}"},
        )
        await self.send_progress()

    async def request_chat(self, req: RequestChatRequest) -> str:
        """Send chat request"""
        response = await self.server.request(
            f"morph/{self.agent_type}_{self.agent_id}_request_chat", req
        )
        return response["message"]

    async def send_progress(self, payload: Optional[Any] = None, payload_only: bool = False):
        """
        Send an update about the progress of the agent's tasks to the server at `morph/{agent_type}_{agent_id}_send_progress`.
        It will try to package the description and status of the main and subtasks into the payload, unless the 'payload_only' parameter is set to True.

        Parameters:
        - payload (dict, optional): A dictionary containing arbitrary data about the agent's progress. Default is None.
        - payload_only (bool, optional): If set to True, the function will not include task updates and will send only the payload. Default is False.

        Note:
        This function assumes that `Agent.main()` has been run and the main task has been created.

        Returns:
        This function does not return a value.
        """
        # Check whether we're only sending payload or also tasks' data
        # logging.getLogger().info(f"sending progress with payload={payload}")
        if payload_only:
            # If only payload is to be sent, set tasks to None
            tasks = None
        else:
            # Try to wrap main and subtasks' data into tasks dictionary
            try:
                tasks = {
                    "task": {
                        "description": AGENT_REGISTRY.registry[self.agent_type].display_name,
                        "status": self.task.status,
                    },
                    "subtasks": (
                        [{"description": x.description, "status": x.status} for x in self.tasks]
                    ),
                }
            # If unable to create tasks dictionary due to an exception, log the exception and set tasks to None
            except Exception as e:
                logger.debug(f"Caught exception: {e}")
                tasks = None

        # Package all agent's progress into an AgentProgress object
        progress = AgentProgress(
            agent_type=self.agent_type,
            agent_id=self.agent_id,
            tasks=tasks,
            payload=payload,
        )

        # If the main task's status is 'error', log it as an info level message
        if self.task.status == "error":
            logger.info(f"[error]: {self.task._task.exception()}")

        # Notify the server about the agent's progress
        await self.server.notify(f"morph/{self.agent_type}_{self.agent_id}_send_progress", progress)

    async def send_result(self) -> ...:
        """Send agent result"""
        ...


@dataclass
class AgentRegistryItem:
    """
    Stored in the registry by the @agent decorator, created upon Rift initialization.
    """

    agent: Type[Agent]
    agent_description: str
    display_name: Optional[str] = None

    def __post_init__(self):
        if self.display_name is None:
            self.display_name = self.agent_type


@dataclass
class AgentRegistryResult:
    """
    To be returned as part of a list of available agent workflows to the language server client.
    """

    agent_type: str
    agent_description: str
    display_name: Optional[str] = None
    agent_icon: Optional[str] = None  # svg icon information


@dataclass
class AgentRegistry:
    """
    AgentRegistry is an organizational class that is used to track all agents in one central location.
    """

    # Initial registry to store agents
    registry: Dict[str, Type[Agent]] = field(default_factory=dict)

    def __getitem__(self, key):
        """
        Allows access to agents in the registry using indexing ([]).

        Parameters:
        - key (str): Key to be used to find the agent.

        Returns:
        - get_agent method called for provided key.
        """
        return self.get_agent(key)

    def register_agent(
        self, agent: Type[Agent], agent_description: str, display_name: Optional[str] = None
    ) -> None:
        """
        Registers new agent into the registry.

        Parameters:
        - agent (Type[Agent]): Agent to be registered.
        - agent_description (str): Description of the agent.
        - display_name (Optional[str]): Display name of the agent, defaults to None.

        Throws:
        - ValueError: if agent.agent_type already exists in the registry.
        """
        if agent.agent_type in self.registry:
            raise ValueError(f"Agent '{agent.agent_type}' is already registered.")
        self.registry[agent.agent_type] = AgentRegistryItem(
            agent=agent,
            agent_description=agent_description,
            display_name=display_name,
        )

    def get_agent(self, agent_type: str) -> Type[Agent]:
        """
        Get the agent from registry based on agent_type provided.

        Parameters:
        - agent_type (str): agent type for the searching agent.

        Returns:
        - Matching agent.

        Throws:
        - ValueError: if agent_type not found in the registry.
        """
        result = self.registry.get(agent_type)
        if result is not None:
            return result.agent
        else:
            raise ValueError(f"Agent not found: {agent_type}")

    def get_agent_icon(self, item: AgentRegistryItem) -> ...:
        """
        Placeholder function to get the icon for a given agent. Currently returns None.

        Parameters:
        - item (AgentRegistryItem): Item containing details of the agent.

        Returns:
        - None
        """
        return None  # TODO

    def list_agents(self) -> List[AgentRegistryResult]:
        """
        Lists all registered agents with their details.

        Returns:
        - List[AgentRegistryResult] : List of all registered agents with their details.
        """
        return [
            AgentRegistryResult(
                agent_type=item.agent.agent_type,
                agent_description=item.agent_description,
                agent_icon=self.get_agent_icon(item),
                display_name=item.display_name,
            )
            for item in self.registry.values()
        ]


AGENT_REGISTRY = AgentRegistry()  # Creating an instance of AgentRegistry


def agent(agent_description: str, display_name: Optional[str] = None):
    """
    The agent decorator is used to bind a class of type Agent to the AgentRegistry.
    The decorator registers the agent class with the AGENT_REGISTRY using the
    'register_agent' method and then returns the class.

    Parameters:
    - agent_description (str): A description of the agent.
    - display_name (str, optional): The display name of the agent. If not provided, None is assumed.
    """

    def decorator(cls: Type[Agent]) -> Type[Agent]:
        AGENT_REGISTRY.register_agent(cls, agent_description, display_name)  # Registering the agent
        return cls

    return decorator
