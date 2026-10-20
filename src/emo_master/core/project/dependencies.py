"""Installed-operator requirements; online writes never install executable code."""


def validateOperatorDependencies(document, registry):
    seen = set()
    for item in document.dependencies.operators:
        if isinstance(item, str):
            key, version = item, None
        elif isinstance(item, dict) and set(item) == {"operatorId", "version"}:
            key, version = item["operatorId"], item["version"]
            if not isinstance(version, str) or not version:
                raise ValueError("operator dependency version must be a non-empty exact version")
        else:
            raise ValueError("operator dependency must be an ID or an operatorId/version lock")
        if not isinstance(key, str) or key not in registry:
            raise ValueError("required operator is not installed: " + str(key))
        if key in seen:
            raise ValueError("duplicate operator dependency: " + key)
        seen.add(key)
        if version is not None and registry[key].manifest.version != version:
            raise ValueError("installed operator version differs from dependency lock: " + key)
