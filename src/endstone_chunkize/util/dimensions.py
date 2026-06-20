DIMENSION_ALIASES = {
    "overworld": "overworld",
    "world": "overworld",
    "nether": "nether",
    "thenether": "nether",
    "end": "the_end",
    "theend": "the_end",
}


def normalizeDimensionName(raw):
    if raw is None:
        return None
    if not isinstance(raw, str):
        name_attr = getattr(raw, "name", None)
        if name_attr is None:
            type_attr = getattr(raw, "type", None)
            if type_attr is not None:
                name_attr = getattr(type_attr, "name", None)
        raw = name_attr if name_attr is not None else ""

    cleaned = str(raw).lower().replace("minecraft:", "").replace("_", "").replace(" ", "")
    return DIMENSION_ALIASES.get(cleaned)
