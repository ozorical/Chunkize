import time

from endstone.command import CommandSenderWrapper

from endstone_chunkize.generation.plan import GenerationPlan
from endstone_chunkize.generation.progress import RateTracker
from endstone_chunkize.util.dimensions import normalizeDimensionName
from endstone_chunkize.util.text import PREFIX, formatDuration, formatNumber


class AreaSlot:
    def __init__(self, name):
        self.name = name
        self.cellIndex = -1
        self.cell = None
        self.pending = set()
        self.deadline = 0.0
        self.active = False
        self.freedSerial = -1


class GenerationTask:
    def __init__(self, plugin, settings, plan, dimension, watermark=0, skippedChunks=0):
        self.plugin = plugin
        self.settings = settings
        self.plan = plan
        self.dimension = dimension
        self.watermark = watermark
        self.nextCell = watermark
        self.completedAhead = set()
        self.retryQueue = []
        self.retriedCells = set()
        self.slots = [AreaSlot(f"chunkize{index}") for index in range(settings.maxActiveAreas)]
        self.chunksDone = sum(cell.chunkCount for cell in plan.cells[:watermark])
        self.skippedChunks = skippedChunks
        self.rate = RateTracker()
        self.task = None
        self.paused = False
        self.serial = 0
        self.failStreak = 0
        self.quietSender = None
        self.commandErrors = []
        self.startedAt = time.monotonic()
        self.lastSave = time.monotonic()
        self.lastLog = time.monotonic()

    @property
    def running(self):
        return self.task is not None

    @property
    def finished(self):
        return self.watermark >= len(self.plan.cells)

    def start(self):
        if self.task is not None:
            return
        self.paused = False
        self.nextCell = self.watermark
        self.completedAhead.clear()
        self.retryQueue.clear()
        self.retriedCells.clear()
        self.chunksDone = sum(cell.chunkCount for cell in self.plan.cells[:self.watermark])
        self.startedAt = time.monotonic()
        self.lastSave = time.monotonic()
        self.lastLog = time.monotonic()
        self.clearStaleAreas()
        self.task = self.plugin.server.scheduler.run_task(
            self.plugin, self.tick, delay=1, period=self.settings.checkIntervalTicks
        )

    def pause(self, byUser=True):
        self.stopScheduler()
        self.releaseSlots()
        self.paused = True
        self.saveState(userPaused=byUser)

    def cancel(self):
        self.stopScheduler()
        self.releaseSlots()
        self.plugin.progressStore.clear()

    def stopScheduler(self):
        if self.task is not None:
            self.task.cancel()
            self.task = None

    def releaseSlots(self):
        for slot in self.slots:
            if slot.active:
                self.removeArea(slot)
            slot.active = False
            slot.pending.clear()
        self.nextCell = self.watermark
        self.completedAhead.clear()
        self.retryQueue.clear()
        self.retriedCells.clear()

    def tick(self):
        self.serial += 1
        now = time.monotonic()
        for slot in self.slots:
            if slot.active:
                self.checkSlot(slot, now)
        self.fillSlots(now)
        if self.task is None:
            return
        if self.finished and not any(slot.active for slot in self.slots):
            self.finish()
            return
        if now - self.lastSave >= self.settings.saveIntervalSeconds:
            self.lastSave = now
            self.saveState(userPaused=False)
        if self.settings.logIntervalSeconds > 0 and now - self.lastLog >= self.settings.logIntervalSeconds:
            self.lastLog = now
            self.logProgress()

    def checkSlot(self, slot, now):
        if not slot.pending:
            self.releaseSlot(slot)
            self.markComplete(slot.cellIndex)
            return
        if now < slot.deadline:
            return
        self.releaseSlot(slot)
        if slot.cellIndex in self.retriedCells:
            self.skippedChunks += len(slot.pending)
            self.plugin.logger.warning(
                f"Batch {slot.cellIndex} timed out twice, skipping {len(slot.pending)} chunks"
            )
            slot.pending.clear()
            self.markComplete(slot.cellIndex)
            return
        self.retriedCells.add(slot.cellIndex)
        self.retryQueue.append(slot.cellIndex)
        slot.pending.clear()

    def fillSlots(self, now):
        loaded = None
        emptySkips = 0
        for slot in self.slots:
            if self.task is None:
                return
            if slot.active or slot.freedSerial >= self.serial:
                continue
            while True:
                cellIndex = self.peekNextCell()
                if cellIndex is None:
                    return
                cell = self.plan.cells[cellIndex]
                if loaded is None:
                    loaded = self.loadedChunkSet()
                pending = {coord for coord in cell.chunkCoords() if coord not in loaded}
                if not pending:
                    if emptySkips >= 25:
                        return
                    emptySkips += 1
                    self.takeNextCell()
                    self.chunksDone += cell.chunkCount
                    self.rate.record(cell.chunkCount)
                    self.markComplete(cellIndex)
                    continue
                slot.cellIndex = cellIndex
                slot.cell = cell
                slot.deadline = now + self.settings.cellTimeoutSeconds
                if not self.addArea(slot):
                    self.dispatch(f"execute in {self.dimension} run tickingarea remove {slot.name}")
                    self.failStreak += 1
                    if self.failStreak >= 5:
                        self.plugin.logger.error(
                            "Could not create a ticking area, the world may be at its limit. "
                            "Free up ticking areas or lower maxActiveAreas, then run /chunkize resume"
                        )
                        self.pause(byUser=True)
                    return
                self.failStreak = 0
                self.takeNextCell()
                slot.pending = pending
                alreadyLoaded = cell.chunkCount - len(pending)
                if alreadyLoaded:
                    self.chunksDone += alreadyLoaded
                    self.rate.record(alreadyLoaded)
                slot.active = True
                break

    def peekNextCell(self):
        if self.retryQueue:
            return self.retryQueue[0]
        if self.nextCell < len(self.plan.cells):
            return self.nextCell
        return None

    def takeNextCell(self):
        if self.retryQueue:
            return self.retryQueue.pop(0)
        index = self.nextCell
        self.nextCell += 1
        return index

    def releaseSlot(self, slot):
        self.removeArea(slot)
        slot.active = False
        slot.freedSerial = self.serial

    def markComplete(self, cellIndex):
        self.completedAhead.add(cellIndex)
        while self.watermark in self.completedAhead:
            self.completedAhead.discard(self.watermark)
            self.watermark += 1

    def onChunkLoad(self, chunkX, chunkZ, dimensionName):
        if self.task is None:
            return
        if normalizeDimensionName(dimensionName) != self.dimension:
            return
        coord = (chunkX, chunkZ)
        for slot in self.slots:
            if slot.active and coord in slot.pending:
                slot.pending.discard(coord)
                self.chunksDone += 1
                self.rate.record(1)
                return

    def loadedChunkSet(self):
        dimension = self.resolveDimension()
        if dimension is None:
            return set()
        return {(chunk.x, chunk.z) for chunk in dimension.loaded_chunks}

    def resolveDimension(self):
        for dimension in self.plugin.server.level.dimensions:
            if normalizeDimensionName(dimension.name) == self.dimension:
                return dimension
        return None

    def addArea(self, slot):
        cell = slot.cell
        minX = cell.minChunkX * 16
        minZ = cell.minChunkZ * 16
        maxX = cell.maxChunkX * 16 + 15
        maxZ = cell.maxChunkZ * 16 + 15
        return self.dispatch(
            f"execute in {self.dimension} run tickingarea add {minX} 0 {minZ} {maxX} 0 {maxZ} {slot.name}"
        )

    def removeArea(self, slot):
        self.dispatch(f"execute in {self.dimension} run tickingarea remove {slot.name}")

    def clearStaleAreas(self):
        for index in range(10):
            self.dispatch(f"execute in {self.dimension} run tickingarea remove chunkize{index}")

    def commandSender(self):
        if self.quietSender is None:
            self.quietSender = CommandSenderWrapper(
                self.plugin.server.command_sender,
                on_message=lambda message: None,
                on_error=self.commandErrors.append,
            )
        return self.quietSender

    def dispatch(self, commandLine):
        self.commandErrors.clear()
        try:
            handled = self.plugin.server.dispatch_command(self.commandSender(), commandLine)
        except Exception:
            return False
        return handled and not self.commandErrors

    def saveState(self, userPaused):
        self.plugin.progressStore.save({
            "dimension": self.dimension,
            "centerX": self.plan.centerX,
            "centerZ": self.plan.centerZ,
            "radius": self.plan.radius,
            "shape": self.plan.shape,
            "cellChunks": self.plan.cellChunks,
            "watermark": self.watermark,
            "skippedChunks": self.skippedChunks,
            "totalChunks": self.plan.totalChunks,
            "chunksDone": self.chunksDone,
            "userPaused": userPaused,
        })

    def finish(self):
        self.stopScheduler()
        self.plugin.progressStore.clear()
        elapsed = formatDuration(time.monotonic() - self.startedAt)
        total = formatNumber(self.chunksDone)
        self.plugin.logger.info(f"Generation finished: {total} chunks processed in {elapsed}")
        self.plugin.server.broadcast_message(
            f"{PREFIX}Finished pregenerating {total} chunks in {self.dimension}"
        )
        self.plugin.generationTask = None

    def progressSnapshot(self):
        total = self.plan.totalChunks
        done = min(self.chunksDone + self.skippedChunks, total)
        percent = done / total * 100.0 if total else 100.0
        return done, total, percent

    def statusLines(self):
        done, total, percent = self.progressSnapshot()
        state = "running" if self.running else "paused"
        lines = [
            f"{PREFIX}{self.dimension} ({state})",
            f"Progress: {percent:.1f}% ({formatNumber(done)} / {formatNumber(total)} chunks)",
        ]
        if self.running:
            speed = self.rate.perSecond()
            if speed >= 0.1:
                eta = formatDuration(max(total - done, 0) / speed)
                lines.append(f"Speed: {speed:.1f} chunks/s, ETA: {eta}")
            else:
                lines.append("Speed: warming up")
        lines.append(
            f"Center: {self.plan.centerX}, {self.plan.centerZ} | "
            f"Radius: {formatNumber(self.plan.radius)} | Shape: {self.plan.shape}"
        )
        if self.skippedChunks:
            lines.append(f"Skipped chunks: {formatNumber(self.skippedChunks)}")
        return lines

    def logProgress(self):
        done, total, percent = self.progressSnapshot()
        speed = self.rate.perSecond()
        eta = formatDuration(max(total - done, 0) / speed) if speed >= 0.1 else "unknown"
        self.plugin.logger.info(
            f"{self.dimension}: {percent:.1f}% ({formatNumber(done)}/{formatNumber(total)} chunks), "
            f"{speed:.1f} chunks/s, ETA {eta}"
        )


def buildTaskFromState(plugin, state):
    plan = GenerationPlan(
        state["centerX"],
        state["centerZ"],
        state["radius"],
        state["shape"],
        state["cellChunks"],
    )
    return GenerationTask(
        plugin,
        plugin.settings,
        plan,
        state["dimension"],
        watermark=state.get("watermark", 0),
        skippedChunks=state.get("skippedChunks", 0),
    )
