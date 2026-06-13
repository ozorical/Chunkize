from endstone.command import Command, CommandSender
from endstone.event import ChunkLoadEvent, event_handler
from endstone.plugin import Plugin

from endstone_chunkize.command.handler import handleCommand
from endstone_chunkize.generation.progress import ProgressStore
from endstone_chunkize.generation.task import buildTaskFromState
from endstone_chunkize.settings import Settings


class ChunkizePlugin(Plugin):
    api_version = "0.11"

    commands = {
        "chunkize": {
            "description": "Pregenerate world chunks ahead of time.",
            "usages": [
                "/chunkize (start|pause|resume|cancel|status|config)<mode: ChunkizeControl>",
                "/chunkize (start)<mode: ChunkizeStart> <radius: int> [dimension: string] [centerX: int] [centerZ: int] [shape: string]",
                "/chunkize (pause)<mode: ChunkizePause>",
                "/chunkize (resume)<mode: ChunkizeResume>",
                "/chunkize (cancel)<mode: ChunkizeCancel>",
                "/chunkize (status)<mode: ChunkizeStatus>", 
                "/chunkize (config)<mode: ChunkizeConfig>", 
            ],
            "permissions": ["chunkize.command"],
        }
    }

    permissions = {
        "chunkize.command": {
            "description": "Allows access to the /chunkize command.",
            "default": "op",
        }
    }

    def __init__(self):
        super().__init__()
        self.settings = None
        self.progressStore = None
        self.generationTask = None

    def on_enable(self):
        self.settings = Settings(str(self.data_folder))
        self.progressStore = ProgressStore(str(self.data_folder))
        self.register_events(self)
        if self.settings.autoResume:
            self.server.scheduler.run_task(self, self.tryAutoResume, delay=100)
        self.logger.info("Chunkize enabled, made by ozz")

    def on_disable(self):
        task = self.generationTask
        if task is not None and task.running:
            task.saveState(userPaused=False)
            task.stopScheduler()
            task.releaseSlots()
        self.generationTask = None

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name != "chunkize":
            return False
        return handleCommand(self, sender, args)

    def tryAutoResume(self):
        if self.generationTask is not None:
            return
        assert self.progressStore, "Called tryAutoResume while progressStore is None"
        state = self.progressStore.load()
        if state is None or state.get("userPaused"):
            return
        self.generationTask = buildTaskFromState(self, state)
        self.generationTask.start()
        self.logger.info("Resuming chunk generation from saved progress")

    @event_handler
    def onChunkLoad(self, event: ChunkLoadEvent):
        task = self.generationTask
        if task is not None:
            task.onChunkLoad(event.chunk.x, event.chunk.z, event.chunk.dimension.name)
