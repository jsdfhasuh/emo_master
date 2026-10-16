// DIAGNOSTIC ONLY. SDK/inbox APIs; never stdout raw records or error text.
// Build with already-installed cl.exe: /std:c++17 /EHsc /W4 /O2 ... tdh.lib advapi32.lib
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <evntrace.h>
#include <tdh.h>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>
#include <stdexcept>
#include <sstream>
#include <iomanip>
#include <algorithm>
#include <cstddef>
#include <climits>
#include <cwchar>
#include <cstring>
#include <utility>
#pragma comment(lib, "tdh.lib")
#pragma comment(lib, "advapi32.lib")

static const GUID PROCESS_GUID = {0x3d6fa8d0,0xfe05,0x11d0,{0x9d,0xda,0x00,0xc0,0x4f,0xd7,0xba,0x7c}};
static const GUID THREAD_GUID = {0x3d6fa8d1,0xfe05,0x11d0,{0x9d,0xda,0x00,0xc0,0x4f,0xd7,0xba,0x7c}};
static const GUID FILE_GUID = {0x90cbdc39,0x4a3e,0x11d1,{0x84,0xf4,0x00,0x00,0xf8,0x04,0x64,0xe3}};
static const GUID DISK_GUID = {0x3d6fa8d4,0xfe05,0x11d0,{0x9d,0xda,0x00,0xc0,0x4f,0xd7,0xba,0x7c}};
static FILE* output = nullptr;
static uint64_t bytes = 0, records = 0, schemaErrors = 0, lost = 0;
static bool abortRead = false;
static constexpr uint64_t MAX_BYTES = 768ull * 1024 * 1024, MAX_EVENTS = 8000000;
static void emit(const std::string& value) {
    if (abortRead || bytes + value.size() + 1 > MAX_BYTES || records >= MAX_EVENTS) {
        abortRead = true; return;
    }
    bytes += value.size() + 1; records++;
    if (fwrite(value.data(), 1, value.size(), output) != value.size() || fputc('\n', output) == EOF) abortRead = true;
}
static std::string quote(const std::wstring& value) {
    std::ostringstream out; out << '"';
    for (wchar_t c : value) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c);
    out << '"'; return out.str();
}
struct Schema {
    EVENT_RECORD* event;
    std::vector<unsigned char> buffer;
    TRACE_EVENT_INFO* info;
    explicit Schema(EVENT_RECORD* r) : event(r), info(nullptr) {
        ULONG size = 0;
        if (TdhGetEventInformation(r, 0, nullptr, nullptr, &size) != ERROR_INSUFFICIENT_BUFFER || size > 1024 * 1024) throw 1;
        buffer.resize(size); info = reinterpret_cast<TRACE_EVENT_INFO*>(buffer.data());
        if (TdhGetEventInformation(r, 0, nullptr, info, &size) != ERROR_SUCCESS || size > buffer.size() ||
            size < offsetof(TRACE_EVENT_INFO, EventPropertyInfoArray) || info->DecodingSource != DecodingSourceWbem ||
            info->TopLevelPropertyCount > info->PropertyCount ||
            info->PropertyCount > (size - offsetof(TRACE_EVENT_INFO, EventPropertyInfoArray)) / sizeof(EVENT_PROPERTY_INFO)) throw 1;
        buffer.resize(size);
    }
    std::pair<std::vector<unsigned char>, USHORT> property(const wchar_t* name) {
        const EVENT_PROPERTY_INFO* found = nullptr;
        for (ULONG i = 0; i < info->TopLevelPropertyCount; ++i) {
            auto& p = info->EventPropertyInfoArray[i];
            if (p.NameOffset >= buffer.size() || p.NameOffset % 2) throw 1;
            const wchar_t* n = reinterpret_cast<const wchar_t*>(buffer.data() + p.NameOffset);
            size_t max = (buffer.size() - p.NameOffset) / sizeof(wchar_t);
            size_t len = wcsnlen_s(n, max);
            if (len == max) throw 1;
            if (wcscmp(n, name) == 0) { if (found) throw 1; found = &p; }
        }
        if (!found || found->Flags != 0 || found->count != 1) throw 1;
        PROPERTY_DATA_DESCRIPTOR descriptor{};
        descriptor.PropertyName = reinterpret_cast<ULONGLONG>(name);
        descriptor.ArrayIndex = ULONG_MAX;
        ULONG size = 0;
        if (TdhGetPropertySize(event, 0, nullptr, 1, &descriptor, &size) != ERROR_SUCCESS || size > 16384) throw 1;
        std::vector<unsigned char> data(size);
        if (TdhGetProperty(event, 0, nullptr, 1, &descriptor, size, data.data()) != ERROR_SUCCESS) throw 1;
        return {std::move(data), found->nonStructType.InType};
    }
    uint64_t number(const wchar_t* name) {
        auto p = property(name); auto& data = p.first;
        if (p.second != TDH_INTYPE_UINT8 && p.second != TDH_INTYPE_INT8 &&
            p.second != TDH_INTYPE_UINT16 && p.second != TDH_INTYPE_UINT32 &&
            p.second != TDH_INTYPE_UINT64 && p.second != TDH_INTYPE_POINTER &&
            p.second != TDH_INTYPE_HEXINT32 && p.second != TDH_INTYPE_HEXINT64) throw 1;
        if (data.size() != 1 && data.size() != 2 && data.size() != 4 && data.size() != 8) throw 1;
        uint64_t value = 0; memcpy(&value, data.data(), data.size()); return value;
    }
    std::wstring string(const wchar_t* name) {
        auto p = property(name);
        if (p.second != TDH_INTYPE_UNICODESTRING || p.first.size() < 2 || p.first.size() % 2) throw 1;
        const wchar_t* text = reinterpret_cast<const wchar_t*>(p.first.data());
        size_t count = p.first.size() / 2, len = wcsnlen_s(text, count);
        if (len + 1 != count || len > 4096) throw 1;
        return std::wstring(text, len);
    }
};
static VOID WINAPI consume(EVENT_RECORD* event) {
    if (abortRead) return;
    const auto& guid = event->EventHeader.ProviderId;
    const unsigned op = event->EventHeader.EventDescriptor.Opcode;
    bool process = IsEqualGUID(guid, PROCESS_GUID), thread = IsEqualGUID(guid, THREAD_GUID);
    bool file = IsEqualGUID(guid, FILE_GUID), disk = IsEqualGUID(guid, DISK_GUID);
    bool selected = (process && op >= 1 && op <= 4) || (thread && ((op >= 1 && op <= 4) || op == 36 || op == 50)) ||
        (file && (op == 0 || op == 32 || op == 35 || op == 36 || (op >= 64 && op <= 77))) || (disk && op == 14);
    if (!selected) return;
    try {
        // Reviewed MOF schema versions only. A new version requires review,
        // even when some property names happen to match.
        const unsigned version = event->EventHeader.EventDescriptor.Version;
        if ((process && version != 3 && version != 4) || (thread && version != 2 && version != 3) ||
            (file && version != 2 && version != 3) || (disk && version != 2 && version != 3)) throw 1;
        Schema s(event);
        std::ostringstream row;
        auto n = [&](const char* key, const wchar_t* name) { row << ",\"" << key << "\":" << s.number(name); };
        auto str = [&](const char* key, const wchar_t* name) { row << ",\"" << key << "\":" << quote(s.string(name)); };
        std::string kind;
        if (process || (thread && op <= 4)) kind = (process ? "process_" : "thread_") + std::string(op == 1 || op == 3 ? "start" : "end");
        else if (thread) kind = op == 36 ? "switch" : "ready";
        else if (disk) kind = "disk_flush";
        else if (op <= 36) kind = "file_name";
        else if (op == 64) kind = "file_create";
        else if (op == 66) kind = "file_close";
        else if (op == 73) kind = "file_flush";
        else if (op == 76) kind = "file_end";
        else kind = "file_other";
        row << "{\"kind\":\"" << kind << "\",\"qpc\":" << event->EventHeader.TimeStamp.QuadPart << ",\"opcode\":" << op;
        if (process || (thread && op <= 4)) {
            n("pid", L"ProcessId"); if (thread) n("tid", L"TThreadId");
            row << ",\"rundown\":" << (op >= 3 ? "true" : "false");
        } else if (thread && op == 36) {
            n("new_tid", L"NewThreadId"); n("old_tid", L"OldThreadId"); n("old_state", L"OldThreadState");
            row << ",\"cpu\":" << event->BufferContext.ProcessorIndex;
        } else if (thread) { n("tid", L"TThreadId"); }
        else if (disk) {
            n("irp", L"Irp"); n("tid", L"IssuingThreadId"); n("duration_ticks", L"HighResResponseTime");
        } else if (op <= 36) {
            n("object", L"FileObject"); str("name", L"FileName");
            row << ",\"remove\":" << (op == 35 ? "true" : "false");
        } else if (op == 76) { n("irp", L"IrpPtr"); n("status", L"NtStatus"); }
        else {
            n("irp", L"IrpPtr"); n("tid", L"TTID"); n("object", L"FileObject");
            if (op == 64) str("name", L"OpenPath"); else n("key", L"FileKey");
        }
        row << '}'; emit(row.str());
    } catch (...) { ++schemaErrors; abortRead = true; }
}
static ULONG WINAPI bufferDone(EVENT_TRACE_LOGFILEW* log) {
    lost = std::max<uint64_t>(lost, log->EventsLost);
    return abortRead ? FALSE : TRUE;
}
static int sessionCheck(const wchar_t* instance, const wchar_t* destination) {
    // Read-only query. Never change privileges or another session.
    constexpr ULONG COUNT = 64, EXTRA = 32768;
    std::vector<std::vector<unsigned char>> memory(COUNT);
    std::vector<EVENT_TRACE_PROPERTIES*> properties(COUNT);
    for (ULONG i = 0; i < COUNT; ++i) {
        memory[i].resize(sizeof(EVENT_TRACE_PROPERTIES) + EXTRA);
        properties[i] = reinterpret_cast<EVENT_TRACE_PROPERTIES*>(memory[i].data());
        properties[i]->Wnode.BufferSize = static_cast<ULONG>(memory[i].size());
        properties[i]->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
        properties[i]->LogFileNameOffset = sizeof(EVENT_TRACE_PROPERTIES) + EXTRA / 2;
    }
    ULONG count = 0;
    if (QueryAllTracesW(properties.data(), COUNT, &count) != ERROR_SUCCESS || count > COUNT) return 2;
    unsigned matched = 0;
    bool valid = false;
    for (ULONG i = 0; i < count; ++i) {
        auto* p = properties[i];
        if (p->LoggerNameOffset >= memory[i].size()) return 2;
        auto* name = reinterpret_cast<wchar_t*>(memory[i].data() + p->LoggerNameOffset);
        size_t maximum = (memory[i].size() - p->LoggerNameOffset) / sizeof(wchar_t);
        if (wcsnlen_s(name, maximum) == maximum) return 2;
        if (wcsstr(name, instance)) {
            ++matched;
            constexpr ULONG required = EVENT_TRACE_FLAG_PROCESS | EVENT_TRACE_FLAG_THREAD | EVENT_TRACE_FLAG_CSWITCH |
                EVENT_TRACE_FLAG_DISPATCHER | EVENT_TRACE_FLAG_DISK_IO | EVENT_TRACE_FLAG_DISK_FILE_IO |
                EVENT_TRACE_FLAG_DISK_IO_INIT | EVENT_TRACE_FLAG_FILE_IO | EVENT_TRACE_FLAG_FILE_IO_INIT;
            valid = wcsstr(name, L"R3CommitTraceKernel") && p->Wnode.ClientContext == 1 &&
                (p->EnableFlags & required) == required && (p->EnableFlags & ~(required | EVENT_TRACE_FLAG_NO_SYSCONFIG)) == 0 &&
                p->BufferSize == 64 && p->MinimumBuffers <= 4096 && p->NumberOfBuffers >= 2 &&
                p->NumberOfBuffers <= 4096 && (p->LogFileMode & EVENT_TRACE_BUFFERING_MODE) &&
                !(p->LogFileMode & (EVENT_TRACE_FILE_MODE_SEQUENTIAL | EVENT_TRACE_FILE_MODE_CIRCULAR | EVENT_TRACE_REAL_TIME_MODE)) &&
                p->EventsLost == 0 && p->LogBuffersLost == 0 && p->RealTimeBuffersLost == 0;
        }
    }
    FILE* check = nullptr;
    if (_wfopen_s(&check, destination, L"wx") || !check) return 2;
    fprintf(check, "{\"matched\":%u,\"valid\":%s}\n", matched, matched == 1 && valid ? "true" : "false");
    bool bad = ferror(check) != 0; fclose(check);
    return bad ? 2 : 0;
}

static bool metadataPreflight() {
    // No trace session. TDH resolves required MOF field metadata from synthetic
    // descriptors; real data/schema/coverage is still validated after capture.
    struct Probe { const GUID* provider; UCHAR opcode; UCHAR version; const wchar_t* field; };
    const Probe probes[] = {
        {&PROCESS_GUID, 1, 4, L"ProcessId"}, {&THREAD_GUID, 1, 3, L"TThreadId"},
        {&THREAD_GUID, 36, 2, L"OldThreadState"}, {&THREAD_GUID, 50, 2, L"TThreadId"},
        {&FILE_GUID, 64, 3, L"OpenPath"}, {&FILE_GUID, 73, 3, L"FileKey"},
        {&FILE_GUID, 76, 3, L"NtStatus"}, {&DISK_GUID, 14, 3, L"HighResResponseTime"}
    };
    try {
        for (const auto& probe : probes) {
            EVENT_RECORD e{}; e.EventHeader.ProviderId = *probe.provider;
            e.EventHeader.EventDescriptor.Opcode = probe.opcode;
            e.EventHeader.EventDescriptor.Version = probe.version;
            e.EventHeader.Flags = EVENT_HEADER_FLAG_CLASSIC_HEADER | EVENT_HEADER_FLAG_64_BIT_HEADER;
            unsigned char synthetic[4096]{};
            e.UserData = synthetic; e.UserDataLength = sizeof(synthetic);
            Schema schema(&e);
            bool found = false;
            for (ULONG i = 0; i < schema.info->TopLevelPropertyCount; ++i) {
                auto offset = schema.info->EventPropertyInfoArray[i].NameOffset;
                if (offset >= schema.buffer.size()) return false;
                auto* name = reinterpret_cast<wchar_t*>(schema.buffer.data() + offset);
                auto maximum = (schema.buffer.size() - offset) / sizeof(wchar_t);
                if (wcsnlen_s(name, maximum) == maximum) return false;
                if (wcscmp(name, probe.field) == 0) found = true;
            }
            if (!found) return false;
            if (wcscmp(probe.field, L"OpenPath") == 0) {
                if (!schema.string(probe.field).empty()) return false;
            } else if (schema.number(probe.field) != 0) return false;
        }
    } catch (...) { return false; }
    return true;
}

int wmain(int argc, wchar_t** argv) {
    // Read-only API presence/QPC preflight. Does not StartTrace or record events.
    if (argc == 2 && wcscmp(argv[1], L"--preflight") == 0) {
        LARGE_INTEGER f{}, t{};
        return QueryPerformanceFrequency(&f) && f.QuadPart > 0 && QueryPerformanceCounter(&t) && metadataPreflight() ? 0 : 2;
    }
    if (argc == 4 && wcscmp(argv[1], L"--session-check") == 0) return sessionCheck(argv[2], argv[3]);
    if (argc != 3) return 2;
    if (_wfopen_s(&output, argv[2], L"wx") || !output) return 2;
    EVENT_TRACE_LOGFILEW log{};
    log.LogFileName = argv[1];
    log.ProcessTraceMode = PROCESS_TRACE_MODE_EVENT_RECORD | PROCESS_TRACE_MODE_RAW_TIMESTAMP;
    log.EventRecordCallback = consume; log.BufferCallback = bufferDone;
    TRACEHANDLE handle = OpenTraceW(&log);
    if (handle == INVALID_PROCESSTRACE_HANDLE) { fclose(output); return 2; }
    auto& h = log.LogfileHeader;
    std::ostringstream header;
    header << "{\"kind\":\"meta\",\"clock\":" << h.ReservedFlags << ",\"frequency\":" << h.PerfFreq.QuadPart
        << ",\"processors\":" << h.NumberOfProcessors << ",\"events_lost\":" << h.EventsLost << ",\"buffers_lost\":" << h.BuffersLost
        << ",\"finalized\":" << (h.EndTime.QuadPart ? "true" : "false") << '}';
    emit(header.str());
    ULONG status = ProcessTrace(&handle, 1, nullptr, nullptr);
    ULONG closeStatus = CloseTrace(handle);
    const bool aborted = abortRead;
    abortRead = false; // bounded constant trailer even after decode rejection
    emit("{\"kind\":\"end\",\"schema_errors\":" + std::to_string(schemaErrors) +
         ",\"lost\":" + std::to_string(lost) + ",\"process_status\":" + std::to_string(status) + "}");
    bool outputError = ferror(output) != 0; fclose(output);
    return aborted || outputError || status != ERROR_SUCCESS || closeStatus != ERROR_SUCCESS ? 2 : 0;
}
