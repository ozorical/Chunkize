from endstone import ColorFormat, Player
from endstone.form import ActionForm

from endstone_chunkize.generation.plan import GenerationPlan
from endstone_chunkize.generation.task import GenerationTask, buildTaskFromState
from endstone_chunkize.settings import MODE_ORDER, MODE_PRESETS, MODE_RATES, applyMode
from endstone_chunkize.util.dimensions import normalizeDimensionName
from endstone_chunkize.util.text import PREFIX, formatNumber

USAGE_LINES = [
    f"{PREFIX}Chunk pregeneration, made by ozz",
    "/chunkize start <radius> [dimension] [centerX] [centerZ] [shape]",
    "/chunkize pause",
    "/chunkize resume",
    "/chunkize cancel",
    "/chunkize status",
    "/chunkize config",
]


def sendError(sender, message):
    sender.send_message(f"{PREFIX}{ColorFormat.RED}{message}")


def sendInfo(sender, message):
    sender.send_message(f"{PREFIX}{message}")


def handleCommand(plugin, sender, args):
    if not args:
        for line in USAGE_LINES:
            sender.send_message(line)
        return True
    action = args[0].lower()
    if action == "start":
        return handleStart(plugin, sender, args[1:])
    if action == "pause":
        return handlePause(plugin, sender)
    if action == "resume":
        return handleResume(plugin, sender)
    if action == "cancel":
        return handleCancel(plugin, sender)
    if action == "status":
        return handleStatus(plugin, sender)
    if action == "config":
        return handleConfig(plugin, sender, args[1:])
    for line in USAGE_LINES:
        sender.send_message(line)
    return True


def handleConfig(plugin, sender, params):
    if params:
        mode = params[0].lower()
        if mode not in MODE_PRESETS:
            sendError(sender, "Mode must be light, medium or intense")
            return True
        applyMode(plugin, mode)
        sendInfo(sender, f"{ColorFormat.GREEN}Speed mode set to {mode} ({MODE_RATES[mode]}), applies to the next /chunkize start")
        return True
    if not isinstance(sender, Player):
        sendInfo(sender, f"Current speed mode: {plugin.settings.mode} ({MODE_RATES.get(plugin.settings.mode, '')})")
        sendInfo(sender, "Set it with /chunkize config <light|medium|intense>")
        return True
    showConfigForm(plugin, sender)
    return True


def showConfigForm(plugin, player):
    current = plugin.settings.mode
    form = ActionForm(
        title="Chunkize speed",
        content=f"Current mode: {ColorFormat.YELLOW}{current}{ColorFormat.RESET}\n\nChoose how fast to pregenerate. Faster modes work the server harder.",
    )
    for mode in MODE_ORDER:
        label = f"{mode.capitalize()}\n{MODE_RATES[mode]}"
        if mode == current:
            label = f"{label} (current)"
        form.add_button(label, on_click=makeModeChoice(plugin, mode))
    player.send_form(form)


def makeModeChoice(plugin, mode):
    def choose(player):
        applyMode(plugin, mode)
        player.send_message(
            f"{PREFIX}{ColorFormat.GREEN}Speed mode set to {mode} ({MODE_RATES[mode]}), "
            "applies to the next /chunkize start"
        )

    return choose


def handleStart(plugin, sender, params):
    if plugin.generationTask is not None:
        sendError(sender, "A generation task already exists, run /chunkize cancel first")
        return True
    if not params:
        sendError(sender, "Usage: /chunkize start <radius> [dimension] [centerX] [centerZ] [shape]")
        return True
    try:
        radius = int(params[0])
    except ValueError:
        sendError(sender, "Radius must be a whole number of blocks")
        return True
    if radius < 16:
        sendError(sender, "Radius must be at least 16 blocks")
        return True
    if radius > plugin.settings.maxRadius:
        sendError(sender, f"Radius is capped at {formatNumber(plugin.settings.maxRadius)} blocks, raise maxRadius in config.toml to go further")
        return True
    dimension = None
    centerX = 0
    centerZ = 0
    if isinstance(sender, Player):
        location = sender.location
        dimension = normalizeDimensionName(location.dimension)
        centerX = location.block_x
        centerZ = location.block_z
    if len(params) > 1:
        dimension = normalizeDimensionName(params[1])
        if dimension is None:
            sendError(sender, f"Unknown dimension {params[1]}, use overworld, nether or the_end")
            return True
    if dimension is None:
        dimension = "overworld"
    if len(params) > 2:
        try:
            centerX = int(params[2])
        except ValueError:
            sendError(sender, "centerX must be a whole number")
            return True
    if len(params) > 3:
        try:
            centerZ = int(params[3])
        except ValueError:
            sendError(sender, "centerZ must be a whole number")
            return True
    shape = "square"
    if len(params) > 4:
        shape = params[4].lower()
        if shape not in ("square", "circle"):
            sendError(sender, "Shape must be square or circle")
            return True
    plan = GenerationPlan(centerX, centerZ, radius, shape, plugin.settings.cellChunks)
    plugin.progressStore.clear()
    task = GenerationTask(plugin, plugin.settings, plan, dimension)
    plugin.generationTask = task
    task.start()
    sendInfo(sender, f"{ColorFormat.GREEN}Started pregenerating a {shape} of radius {formatNumber(radius)} around {centerX}, {centerZ} in {dimension}")
    sendInfo(sender, f"{formatNumber(plan.totalChunks)} chunks queued across {formatNumber(len(plan.cells))} batches in {plugin.settings.mode} mode, check /chunkize status anytime")
    plugin.logger.info(
        f"Generation started: {dimension}, radius {radius}, center {centerX} {centerZ}, "
        f"{plan.totalChunks} chunks"
    )
    return True


def handlePause(plugin, sender):
    task = plugin.generationTask
    if task is None or not task.running:
        sendError(sender, "No generation task is running")
        return True
    task.pause(byUser=True)
    sendInfo(sender, "Generation paused, run /chunkize resume to continue")
    return True


def handleResume(plugin, sender):
    task = plugin.generationTask
    if task is not None and task.running:
        sendError(sender, "Generation is already running")
        return True
    if task is not None and task.paused:
        task.start()
        sendInfo(sender, f"{ColorFormat.GREEN}Generation resumed")
        return True
    state = plugin.progressStore.load()
    if state is None:
        sendError(sender, "Nothing to resume")
        return True
    plugin.generationTask = buildTaskFromState(plugin, state)
    plugin.generationTask.start()
    sendInfo(sender, f"{ColorFormat.GREEN}Generation resumed from saved progress")
    return True


def handleCancel(plugin, sender):
    task = plugin.generationTask
    if task is not None:
        task.cancel()
        plugin.generationTask = None
        sendInfo(sender, "Generation cancelled")
        return True
    if plugin.progressStore.load() is not None:
        plugin.progressStore.clear()
        sendInfo(sender, "Cleared saved generation progress")
        return True
    sendError(sender, "No generation task to cancel")
    return True


def handleStatus(plugin, sender):
    task = plugin.generationTask
    if task is not None:
        for line in task.statusLines():
            sender.send_message(line)
        return True
    state = plugin.progressStore.load()
    if state is not None:
        done = state.get("chunksDone", 0) + state.get("skippedChunks", 0)
        total = state.get("totalChunks", 0)
        percent = done / total * 100.0 if total else 0.0
        sender.send_message(f"{PREFIX}{state['dimension']} (saved, not running)")
        sender.send_message(f"Progress: {percent:.1f}% ({formatNumber(min(done, total))} / {formatNumber(total)} chunks)")
        sender.send_message("Run /chunkize resume to continue")
        return True
    sendInfo(sender, "No generation task is running")
    return True
