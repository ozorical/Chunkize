import os
import tomllib

DEFAULT_MODE = "medium"
MODE_ORDER = ("light", "medium", "intense")

MODE_PRESETS = {
    "light": {
        "cellChunks": 8,
        "maxActiveAreas": 1,
        "settleSeconds": 8,
        "settleMinSeconds": 2,
        "verifyGeneration": True,
        "checkIntervalTicks": 10,
        "targetMspt": 40.0,
        "flushIntervalChunks": 512,
    },
    "medium": {
        "cellChunks": 10,
        "maxActiveAreas": 6,
        "settleSeconds": 8,
        "settleMinSeconds": 1,
        "verifyGeneration": True,
        "checkIntervalTicks": 5,
        "targetMspt": 45.0,
        "flushIntervalChunks": 512,
    },
    "intense": {
        "cellChunks": 10,
        "maxActiveAreas": 10,
        "settleSeconds": 8,
        "settleMinSeconds": 0,
        "verifyGeneration": True,
        "checkIntervalTicks": 5,
        "targetMspt": 48.0,
        "flushIntervalChunks": 1024,
    },
}

MODE_RATES = {
    "light": "10 chunks/s",
    "medium": "50 chunks/s",
    "intense": "~150 chunks/s",
}

DEFAULT_CONFIG = """[generation]
# Speed mode: light, medium or intense. Easiest to change in game with /chunkize config.
mode = "medium"
minActiveAreas = 1
cellTimeoutSeconds = 60
stampChunks = true
maxRadius = 50000

[progress]
autoResume = true
logIntervalSeconds = 30
saveIntervalSeconds = 30
"""


def clamp(value, low, high):
    return max(low, min(high, value))


def writeMode(dataFolder, mode):
    path = os.path.join(dataFolder, "config.toml")
    lines = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as file:
                lines = file.readlines()
        except OSError:
            lines = []
    out = []
    replaced = False
    for line in lines:
        stripped = line.lstrip()
        if not replaced and (stripped.startswith("mode ") or stripped.startswith("mode=")):
            out.append(f'mode = "{mode}"\n')
            replaced = True
        else:
            out.append(line)
    if not replaced:
        rebuilt = []
        inserted = False
        for line in out:
            rebuilt.append(line)
            if not inserted and line.strip() == "[generation]":
                rebuilt.append(f'mode = "{mode}"\n')
                inserted = True
        out = rebuilt if inserted else [f'mode = "{mode}"\n', *out]
    try:
        with open(path, "w", encoding="utf-8") as file:
            file.writelines(out)
    except OSError:
        pass


class Settings:
    def __init__(self, dataFolder):
        path = os.path.join(dataFolder, "config.toml")
        os.makedirs(dataFolder, exist_ok=True)
        if not os.path.isfile(path):
            with open(path, "w", encoding="utf-8") as file:
                file.write(DEFAULT_CONFIG)
        try:
            with open(path, "rb") as file:
                raw = tomllib.load(file)
        except (OSError, tomllib.TOMLDecodeError):
            raw = {}
        generation = raw.get("generation", {})
        progress = raw.get("progress", {})
        mode = str(generation.get("mode", DEFAULT_MODE)).lower()
        if mode not in MODE_PRESETS:
            mode = DEFAULT_MODE
        self.mode = mode
        preset = MODE_PRESETS[mode]
        self.cellChunks = clamp(int(generation.get("cellChunks", preset["cellChunks"])), 1, 10)
        self.maxActiveAreas = clamp(int(generation.get("maxActiveAreas", preset["maxActiveAreas"])), 1, 10)
        self.minActiveAreas = clamp(int(generation.get("minActiveAreas", 1)), 1, self.maxActiveAreas)
        self.targetMspt = clamp(float(generation.get("targetMspt", preset["targetMspt"])), 10.0, 50.0)
        self.checkIntervalTicks = clamp(int(generation.get("checkIntervalTicks", preset["checkIntervalTicks"])), 1, 200)
        self.cellTimeoutSeconds = clamp(int(generation.get("cellTimeoutSeconds", 60)), 5, 3600)
        self.verifyGeneration = bool(generation.get("verifyGeneration", preset["verifyGeneration"]))
        self.settleSeconds = clamp(int(generation.get("settleSeconds", preset["settleSeconds"])), 0, 600)
        self.settleMinSeconds = clamp(int(generation.get("settleMinSeconds", preset["settleMinSeconds"])), 0, self.settleSeconds)
        self.stampChunks = bool(generation.get("stampChunks", True))
        self.maxRadius = clamp(int(generation.get("maxRadius", 50000)), 16, 1000000)
        self.flushIntervalChunks = clamp(int(generation.get("flushIntervalChunks", preset["flushIntervalChunks"])), 0, 1000000)
        self.autoResume = bool(progress.get("autoResume", True))
        self.logIntervalSeconds = clamp(int(progress.get("logIntervalSeconds", 30)), 0, 3600)
        self.saveIntervalSeconds = clamp(int(progress.get("saveIntervalSeconds", 30)), 5, 3600)


def applyMode(plugin, mode):
    writeMode(str(plugin.data_folder), mode)
    plugin.settings = Settings(str(plugin.data_folder))
    return plugin.settings
