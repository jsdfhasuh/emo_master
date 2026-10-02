// Counter only: one read-only query, fixed storage, no session detail access.
#include <windows.h>
#include <evntrace.h>
#include <cstddef>
#include <cstdio>
#include <limits>

constexpr ULONG CAPACITY = 64;
struct SessionBuffer {
    EVENT_TRACE_PROPERTIES properties;
    wchar_t loggerName[16384];
    wchar_t logFileName[16384];
};
static_assert(sizeof(SessionBuffer) <= (std::numeric_limits<ULONG>::max)());

int main() {
    // Static storage avoids a multi-megabyte allocation on the Windows stack.
    // The API requires name/path storage. It is never inspected or printed.
    static SessionBuffer buffers[CAPACITY]{};
    EVENT_TRACE_PROPERTIES* properties[CAPACITY]{};
    for (ULONG i = 0; i < CAPACITY; ++i) {
        properties[i] = &buffers[i].properties;
        properties[i]->Wnode.BufferSize = static_cast<ULONG>(sizeof(SessionBuffer));
        properties[i]->LoggerNameOffset = static_cast<ULONG>(offsetof(SessionBuffer, loggerName));
        properties[i]->LogFileNameOffset = static_cast<ULONG>(offsetof(SessionBuffer, logFileName));
    }
    ULONG count = 0;
    const ULONG status = QueryAllTracesW(properties, CAPACITY, &count);
    if (status == ERROR_SUCCESS || status == ERROR_MORE_DATA) {
        std::printf("{\"api_status\":%lu,\"returned_count\":%lu}\n",
                    static_cast<unsigned long>(status), static_cast<unsigned long>(count));
    } else {
        // On unrelated errors even a nonzero output parameter is untrusted.
        std::printf("{\"api_status\":%lu,\"returned_count\":null}\n",
                    static_cast<unsigned long>(status));
    }
    return std::ferror(stdout) ? 2 : 0;
}
