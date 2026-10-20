"""Offline identity hints from trusted metadata, without loading driver code."""
import ipaddress


def declaredIdentity(definition, declaration):
    fields = []
    incomplete = False

    def visit(schema, value, path=()):
        nonlocal incomplete
        if schema.get('xDeviceIdentity') is True:
            if type(value) not in (str, int, bool) or value == '':
                incomplete = True
                return
            if schema.get('xAddressLiteral'):
                try:
                    address = ipaddress.ip_address(value)
                    value = str(getattr(address, 'ipv4_mapped', None) or address)
                except ValueError:
                    incomplete = True
                    return
            fields.append((path, value))
        for key, child in sorted(schema.get('properties', {}).items()):
            visit(child, value.get(key) if isinstance(value, dict) else None, (*path, key))

    visit(declaration.connectionSchema, definition.get('connection', {}))
    return (definition['deviceTypeId'], tuple(fields)) if fields and not incomplete else None


def matchingDevices(value, definitions, declaration, *, excluding=None):
    identity = declaredIdentity(value, declaration)
    if identity is None:
        return []
    return [key for key, candidate in definitions.items() if key != excluding
            and candidate.get('deviceTypeId') == value['deviceTypeId']
            and declaredIdentity(candidate, declaration) == identity]
