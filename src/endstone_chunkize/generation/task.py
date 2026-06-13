import time

from endstone.command import CommandSenderWrapper

from endstone_chunkize.generation.plan import GenerationPlan
from endstone_chunkize.generation.progress import RateTracker
from endstone_chunkize.util.dimensions import normalizeDimensionName
from endstone_chunkize.util.text import PREFIX, describeMessage, formatDuration, formatNumber

STAMP_HEIGHTS = {"overworld": 319, "nether": 127, "the_end": 255}
FLUSH_TIMEOUT_SECONDS = 60
FLUSH_READY_MARKERS = ("commands.save-all.success", "ready to be copied")
LOAD_CHECK_SECONDS = 5.0
VERIFY_FLOOR = {"overworld": -40, "nether": 0}


class AreaSlot:
    def __init__(self, name):
        self.name = name
        self.cellIndex = -1
        self.cell = None
        self.pending = set()
        self.deadline = 0.0
        self.settleStart = 0.0
        self.verifyPending = set()
        self.stampQueue = []
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
        self.commandOutput = []
        self.chunksSinceFlush = 0
        self.flushing = False
        self.flushDeadline = 0.0
        self.activeTarget = settings.minActiveAreas
        self.areaCeiling = settings.maxActiveAreas
        self.lastLoadCheck = time.monotonic()
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
        self.activeTarget = self.settings.minActiveAreas
        self.areaCeiling = self.settings.maxActiveAreas
        self.startedAt = time.monotonic()
        self.lastSave = time.monotonic()
        self.lastLog = time.monotonic()
        self.lastLoadCheck = time.monotonic()
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
        self.abortFlush()
        for slot in self.slots:
            if slot.active:
                self.removeArea(slot)
            slot.active = False
            slot.pending.clear()
            slot.stampQueue = []
            slot.verifyPending = set()
            slot.settleStart = 0.0
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
        if self.flushing:
            self.advanceFlush(now)
        elif self.shouldFlush():
            self.beginFlush(now)
        else:
            self.adjustConcurrency(now)
            self.fillSlots(now)
        if self.task is None:
            return
        if self.finished and not self.flushing and not any(slot.active for slot in self.slots):
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
            if slot.settleStart == 0.0:
                slot.settleStart = now
                if self.settings.stampChunks:
                    slot.stampQueue = list(slot.cell.chunkCoords())
                if self.settings.verifyGeneration:
                    slot.verifyPending = set(slot.cell.chunkCoords())
                return
            self.stampChunks(slot)
            self.verifyChunks(slot)
            elapsed = now - slot.settleStart
            verifiedDone = (
                elapsed >= self.settings.settleMinSeconds
                and not slot.verifyPending
                and not slot.stampQueue
            )
            if not (verifiedDone or elapsed >= self.settings.settleSeconds):
                return
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
        activeCount = sum(1 for slot in self.slots if slot.active)
        ceiling = min(self.activeTarget, self.settings.maxActiveAreas, self.areaCeiling)
        for slot in self.slots:
            if self.task is None:
                return
            if slot.active or slot.freedSerial >= self.serial:
                continue
            if activeCount >= ceiling:
                return
            cellIndex = self.peekNextCell()
            if cellIndex is None:
                return
            cell = self.plan.cells[cellIndex]
            if loaded is None:
                loaded = self.loadedChunkSet()
            pending = {coord for coord in cell.chunkCoords() if coord not in loaded}
            slot.cellIndex = cellIndex
            slot.cell = cell
            slot.deadline = now + self.settings.cellTimeoutSeconds
            slot.settleStart = 0.0
            slot.verifyPending = set()
            slot.stampQueue = []
            if not self.addArea(slot):
                self.dispatch(f"execute in {self.dimension} run tickingarea remove {slot.name}")
                self.handleAreaFailure(activeCount)
                return
            self.failStreak = 0
            self.takeNextCell()
            slot.pending = pending
            alreadyLoaded = cell.chunkCount - len(pending)
            if alreadyLoaded:
                self.chunksDone += alreadyLoaded
                self.rate.record(alreadyLoaded)
            slot.active = True
            activeCount += 1

    def handleAreaFailure(self, activeCount):
        self.failStreak += 1
        if activeCount > 0:
            self.areaCeiling = activeCount
            self.activeTarget = activeCount
            return
        if self.failStreak >= 5:
            self.plugin.logger.error(
                "Could not create a ticking area, the world may be at its limit. "
                "Free up ticking areas or lower maxActiveAreas, then run /chunkize resume"
            )
            self.pause(byUser=True)

    def adjustConcurrency(self, now):
        if now - self.lastLoadCheck < LOAD_CHECK_SECONDS:
            return
        self.lastLoadCheck = now
        mspt = self.serverMspt()
        if mspt is None or mspt <= 0.0:
            self.activeTarget = min(self.settings.maxActiveAreas, self.areaCeiling)
            return
        target = self.settings.targetMspt
        if mspt > target:
            step = 2 if mspt > target * 1.3 else 1
            self.activeTarget -= step
        elif mspt < target * 0.8:
            if self.activeTarget >= min(self.settings.maxActiveAreas, self.areaCeiling) \
                    and self.areaCeiling < self.settings.maxActiveAreas:
                self.areaCeiling += 1
            self.activeTarget += 1
        upper = min(self.settings.maxActiveAreas, self.areaCeiling)
        self.activeTarget = max(self.settings.minActiveAreas, min(self.activeTarget, upper))

    def serverMspt(self):
        try:
            value = self.plugin.server.average_mspt
        except Exception:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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

    def stampChunks(self, slot):
        if not slot.stampQueue:
            return
        dimension = self.resolveDimension()
        if dimension is None:
            slot.stampQueue.clear()
            return
        stampY = STAMP_HEIGHTS.get(self.dimension, 319)
        for _ in range(min(32, len(slot.stampQueue))):
            chunkX, chunkZ = slot.stampQueue.pop()
            try:
                block = dimension.get_block_at(chunkX * 16 + 8, stampY, chunkZ * 16 + 8)
                original = block.data
                block.set_type("minecraft:bedrock", False)
                block.set_data(original, False)
            except Exception:
                pass

    def verifyChunks(self, slot):
        if not slot.verifyPending:
            return
        floor = VERIFY_FLOOR.get(self.dimension)
        if floor is None:
            return
        dimension = self.resolveDimension()
        if dimension is None:
            return
        verified = []
        for chunkX, chunkZ in slot.verifyPending:
            try:
                height = dimension.get_highest_block_y_at(chunkX * 16 + 8, chunkZ * 16 + 8)
            except Exception:
                height = None
            if height is not None and height > floor:
                verified.append((chunkX, chunkZ))
        slot.verifyPending.difference_update(verified)

    def markComplete(self, cellIndex):
        self.chunksSinceFlush += self.plan.cells[cellIndex].chunkCount
        self.completedAhead.add(cellIndex)
        while self.watermark in self.completedAhead:
            self.completedAhead.discard(self.watermark)
            self.watermark += 1

    def shouldFlush(self):
        if self.chunksSinceFlush <= 0:
            return False
        if self.settings.flushIntervalChunks <= 0:
            return False
        if self.chunksSinceFlush >= self.settings.flushIntervalChunks:
            return True
        return self.finished and not any(slot.active for slot in self.slots)

    def beginFlush(self, now):
        self.flushing = True
        self.flushDeadline = now + FLUSH_TIMEOUT_SECONDS
        self.dispatch("save hold")

    def advanceFlush(self, now):
        if self.flushReady():
            self.endFlush()
            return
        if now >= self.flushDeadline:
            self.plugin.logger.warning(
                "World save did not confirm in time, resuming saves anyway"
            )
            self.endFlush()

    def flushReady(self):
        self.dispatch("save query")
        text = " ".join(
            describeMessage(message)
            for message in (*self.commandOutput, *self.commandErrors)
        ).lower()
        return any(marker in text for marker in FLUSH_READY_MARKERS)

    def endFlush(self):
        self.flushing = False
        self.chunksSinceFlush = 0
        self.dispatch("save resume")

    def abortFlush(self):
        if self.flushing:
            self.flushing = False
            self.dispatch("save resume")

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
                on_message=self.commandOutput.append,
                on_error=self.commandErrors.append,
            )
        return self.quietSender

    def dispatch(self, commandLine):
        self.commandErrors.clear()
        self.commandOutput.clear()
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
            if self.flushing:
                lines.append("Flushing world save to disk")
            activeCount = sum(1 for slot in self.slots if slot.active)
            mspt = self.serverMspt()
            load = f"{mspt:.0f} ms/tick" if mspt is not None else "unknown"
            lines.append(
                f"Active areas: {activeCount} (target {self.activeTarget} of {self.settings.maxActiveAreas}), "
                f"server load {load}"
            )
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
