"""SQLite table properties with a quote-aware fallback for older libraries."""
import re

from emo_master.core.contracts.sqlite_writer import quoteIdentifier


_DDL_TOKENS = re.compile(r'''--[^\r\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|"(?:""|[^"])*"|`(?:``|[^`])*`|\[[^\]]*\]|[A-Za-z_]+|[^\s]''')


def tableFlags(connection, table: str, ddl: str) -> tuple[str, bool]:
    # SQLite 3.37+: type distinguishes ordinary, virtual and shadow tables; wr
    # is the actual WITHOUT ROWID flag. Filter by name rather than scan all tables.
    metadata = connection.execute(f'PRAGMA main.table_list({quoteIdentifier(table)})').fetchone()
    if metadata is not None:
        return metadata[2], bool(metadata[4])
    # Unsupported PRAGMAs yield no rows on older SQLite. Keep quoted names,
    # literals and comments opaque so they cannot manufacture syntax tokens.
    tokens = [('<QUOTED>' if token[0] in "'\"`[" else token.upper())
              for token in _DDL_TOKENS.findall(ddl) if not token.startswith(('--', '/*'))]
    kind = 'virtual' if tokens[:3] == ['CREATE', 'VIRTUAL', 'TABLE'] else 'table'
    closing = max((i for i, token in enumerate(tokens) if token == ')'), default=-1)
    tail = tokens[closing + 1:]
    withoutRowid = any(tail[i:i + 2] == ['WITHOUT', 'ROWID'] for i in range(len(tail) - 1))
    return kind, withoutRowid
