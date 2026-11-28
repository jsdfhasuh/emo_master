"""Optional observation of the existing synthetic benchmark's native Qt tick."""
import json
import time
from itertools import islice


class HubTickProbe:
    MAX_ROWS = 4096
    MAX_BYTES = 512 * 1024

    def __init__(self, clock=time.perf_counter_ns):
        self.rows = []
        self.clock = clock
        self.dropped = 0
        self.errors = 0

    def wrap(self, tick):
        def observed(hub):
            if len(self.rows) >= self.MAX_ROWS:
                self.dropped += 1
                return tick(hub)
            row = {"enter": self.clock(), "leave": None, "reads": [], "advanced": False, "finished": False}
            self.rows.append(row)
            prior = hub.lastToken
            session = hub.session
            try:
                namespace = vars(session)
                original = session.readSnapshot
                has_override = "readSnapshot" in namespace
                raw = namespace.get("readSnapshot")
            except (TypeError, AttributeError):
                self.errors += 1
                return tick(hub)

            def read():
                if len(row["reads"]) >= 2:
                    self.errors += 1
                    return original()
                rec = {"enter": self.clock(), "leave": None, "ordinals": []}
                row["reads"].append(rec)
                result = original()
                rec["leave"] = self.clock()
                try:
                    if len(result.scopes) > 16:
                        self.errors += 1
                    else:
                        rec["ordinals"] = sorted(item.result.identity.resultOrdinal
                                                  for item in islice(result.scopes.values(), 16))
                except (AttributeError, TypeError, ValueError):
                    self.errors += 1
                return result

            bound = False
            try:
                try:
                    session.readSnapshot = read
                    bound = True
                except (TypeError, AttributeError):
                    self.errors += 1
                result = tick(hub)
                row["finished"] = True
                return result
            finally:
                if bound:
                    try:
                        if has_override:
                            session.readSnapshot = raw
                        else:
                            del session.readSnapshot
                    except (TypeError, AttributeError):
                        self.errors += 1
                row["leave"] = self.clock()
                row["advanced"] = hub.lastToken is not prior
        return observed

    def save(self, path):
        try:
            value = {"schema": 1, "clock": "perf_counter_ns", "rows": self.rows,
                     "dropped": self.dropped, "errors": self.errors}
            raw = json.dumps(value, separators=(",", ":")).encode("ascii")
            if len(raw) > self.MAX_BYTES:
                value.update(rows=[], dropped=self.dropped + len(self.rows))
                raw = json.dumps(value, separators=(",", ":")).encode("ascii")
            with path.open("xb") as stream:
                stream.write(raw)
            return True
        except (OSError, TypeError, ValueError):
            return False
