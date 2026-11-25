"""Selected device work values for operator diagnostics."""


def deviceSettings(context, slot, fields):
    """Keep diagnostic provenance tied to a resolved device slot and its own schema."""
    resolved = context.get("deviceParameters", {}).get(slot, {})
    schema = context.get("deviceParameterSchemas", {}).get(slot)
    values = {key: resolved[key] for key in fields if key in resolved}
    if not schema or not values:
        return {}
    return {"deviceSettings": {"slot": slot, **schema, "values": values}}
