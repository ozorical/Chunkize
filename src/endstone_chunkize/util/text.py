from endstone import ColorFormat

PREFIX = f"{ColorFormat.GOLD}[Chunkize]{ColorFormat.RESET} "


def formatNumber(value):
    return f"{value:,}"


def describeMessage(message):
    text = getattr(message, "text", None)
    if text is None:
        return str(message)
    params = getattr(message, "params", None) or ()
    return " ".join([str(text), *(str(param) for param in params)])


def formatDuration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
