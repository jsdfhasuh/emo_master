"""Shared, opt-in Designer profiling derived from the flow drag probe.

The old flow probe used cProfile/pstats plus perf_counter samples. Keep those
mechanisms for both workspaces; use GUI-thread CPU time for function rankings
so waiting inside QDrag/dialog event loops is not reported as expensive code.
"""
from collections import Counter, deque
import cProfile
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pstats
from statistics import median
import subprocess
import sys
from threading import Event, Thread, get_ident
from time import perf_counter, thread_time
from typing import Any
from uuid import uuid4


def durationStatistics(durations):
    """Preserve the flow validation script's median and percentile convention."""
    ordered = sorted(durations)
    return {'samples': len(ordered), 'medianMs': median(ordered) if ordered else 0,
            'p95Ms': ordered[int(.95 * (len(ordered) - 1))] if ordered else 0,
            'maxMs': max(ordered, default=0)}


def sourceLocation(filename):
    path = str(filename).replace('\\', '/')
    for marker in ('/src/', '/site-packages/'):
        if marker in path:
            return marker.strip('/') + '/' + path.split(marker, 1)[1]
    return path.rsplit('/', 1)[-1]


def category(filename):
    for token, title in (
        ('responsive_preview.py', '候选布局测量'), ('core/presentation/responsive.py', '响应式布局求解'),
        ('responsive_layout.py', 'Qt布局测量与应用'), ('presentation/renderer.py', '页面渲染与控件创建'),
        ('page_canvas.py', '画布适配与输入'), ('responsive_tools.py', '页面选择、拖动与尺寸调整'),
        ('page_designer/tools.py', '属性面板与编辑'), ('page_designer/workspace.py', '页面刷新与切换'),
        ('designer/state/', '工程模型与历史'), ('flow_routing.py', '流程连线路由'),
        ('flow_scene.py', '流程场景'), ('designer_graphics_view.py', '画布导航'),
    ):
        if token in filename:
            return title
    return '其他GUI调用'


def sourceIdentity():
    root = Path(__file__).resolve().parents[5]
    result: dict = {'head': None, 'branch': None, 'dirty': None, 'files': {}}
    try:
        for name, args in [('head', ['rev-parse', 'HEAD']), ('branch', ['branch', '--show-current']),
                           ('dirty', ['status', '--porcelain', '--untracked-files=no'])]:
            value = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                text=True, timeout=2, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0).stdout.strip()
            result[name] = bool(value) if name == 'dirty' else value
    except (OSError, subprocess.SubprocessError):
        pass  # A frozen installation still has version/platform metadata.
    for name in ('apps/designer/services/performance_probe.py', 'apps/designer/ui/performance_probe.py',
                 'apps/designer/page_designer/responsive_tools.py', 'apps/designer/page_designer/page_canvas.py',
                 'apps/designer/page_designer/responsive_preview.py', 'apps/designer/page_designer/tools.py',
                 'apps/designer/page_designer/workspace.py', 'apps/designer/state/project_edit_session.py',
                 'apps/designer/ui/flow_scene.py', 'apps/designer/ui/flow_routing.py',
                 'core/presentation/responsive.py', 'ui/presentation/responsive_layout.py',
                 'ui/presentation/renderer.py'):
        path = root / 'src/emo_master' / name
        if path.is_file():
            result['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class DesignerPerformanceProbe:
    INTERVAL_MS = 20
    MAX_BEATS = 15000
    MAX_STACKS = 600
    MAX_MARKS = 100

    def __init__(self, metadata, *, clock=perf_counter, detailed=True):
        self.metadata = metadata
        self.clock = clock
        self.profile = cProfile.Profile(timer=thread_time) if detailed else None
        self.running = False
        self.beats: deque[dict] = deque(maxlen=self.MAX_BEATS)
        self.stacks: deque[dict] = deque(maxlen=self.MAX_STACKS)
        self.marks = []
        self.events: Counter[str] = Counter()
        self.beatCount = self.stackCount = self.markCount = 0
        self.samplingErrors = 0
        self._stop = Event()
        self._sampler = None
        self.functions: list[dict] = []
        self.areas: list[dict] = []

    def start(self):
        if self.running or self._stop.is_set():
            raise ValueError('每次记录请使用一个新的探针实例')
        if self.profile is not None and sys.getprofile() is not None:
            raise ValueError('当前已有函数分析器，请结束它或关闭“记录函数耗时”后再开始')
        self.threadId = get_ident()
        self.started = self.lastBeat = self.clock()
        self.lastWorkspace = 'unknown'
        self.startedUtc = datetime.now(timezone.utc).isoformat()
        self.running = True
        self._sampler = Thread(target=self._sampleStalls, name='DesignerProbeSampler', daemon=True)
        self._sampler.start()
        if self.profile is not None:
            self.profile.enable()

    def heartbeat(self, workspace):
        if not self.running:
            return
        now = self.clock()
        elapsed = (now - self.lastBeat) * 1000
        self.lastBeat = now
        self.lastWorkspace = workspace
        self.beatCount += 1
        self.beats.append({'atMs': (now - self.started) * 1000,
                           'lateMs': max(0, elapsed - self.INTERVAL_MS), 'workspace': workspace})

    def mark(self, workspace):
        if self.running:
            self.markCount += 1
            if len(self.marks) < self.MAX_MARKS:
                self.marks.append({'atMs': (self.clock() - self.started) * 1000, 'workspace': workspace})

    def event(self, name):
        if self.running:
            self.events[name] += 1

    def _sampleStalls(self):
        while not self._stop.wait(.05):
            now = self.clock()
            if now - self.lastBeat < .1:
                continue
            try:
                frame = sys._current_frames().get(self.threadId)
                stack: list[list] = []
                while frame is not None and len(stack) < 32:
                    stack.append([sourceLocation(frame.f_code.co_filename), frame.f_lineno, frame.f_code.co_name])
                    frame = frame.f_back
                del frame
                self.stackCount += 1
                self.stacks.append({'atMs': (now - self.started) * 1000,
                    'sinceHeartbeatMs': (now - self.lastBeat) * 1000, 'frames': stack})
            except Exception:
                self.samplingErrors += 1

    def stop(self):
        if not self.running:
            return
        if get_ident() != self.threadId:
            raise RuntimeError('Stop the Designer probe on its owning GUI thread')
        if self.profile is not None:
            self.profile.disable()
        # A blocked final operation may finish just before the timer is served.
        if (self.clock() - self.lastBeat) * 1000 > self.INTERVAL_MS:
            self.heartbeat(self.lastWorkspace)
        self.ended = self.clock()
        self.running = False
        self._stop.set()
        if self._sampler is not None:
            self._sampler.join()
        if self.profile is not None:
            stats: Any = pstats.Stats(self.profile)
            areas: Counter[str] = Counter()
            rows = []
            for (filename, line, name), (_primitive, calls, own, total, callers) in stats.stats.items():
                location = sourceLocation(filename)
                if location.endswith(('designer/services/performance_probe.py', 'designer/ui/performance_probe.py')):
                    continue
                area = category(location)
                areas[area] += own * 1000
                rows.append({'file': location, 'line': line, 'function': name, 'calls': calls,
                    'selfCpuMs': own * 1000, 'totalCpuMs': total * 1000, 'area': area,
                    'callers': [{'file': sourceLocation(key[0]), 'line': key[1], 'function': key[2],
                                 'calls': value[1], 'totalCpuMs': value[3] * 1000}
                                for key, value in sorted(callers.items(), key=lambda item: item[1][3], reverse=True)[:8]]})
            self.functions = sorted(rows, key=lambda row: row['selfCpuMs'], reverse=True)
            self.areas = [{'area': name, 'selfCpuMs': value} for name, value in areas.most_common()]

    def report(self):
        if self.running or not self._stop.is_set():
            raise ValueError('请先停止记录再导出')
        lateness = [row['lateMs'] for row in self.beats]
        return {'schemaVersion': '1.0', 'probe': 'designer-performance', 'startedUtc': self.startedUtc,
            'elapsedMs': (self.ended - self.started) * 1000, 'metadata': self.metadata,
            'detailed': self.profile is not None,
            'functionClock': 'GUI thread CPU time; inclusive totals must not be summed',
            'heartbeat': {**durationStatistics(lateness), 'intervalMs': self.INTERVAL_MS,
                          'lateOver50Ms': sum(value > 50 for value in lateness), 'series': list(self.beats)},
            'events': dict(self.events), 'marks': self.marks, 'stallStacks': list(self.stacks),
            'areas': self.areas, 'functions': self.functions,
            'omitted': {'beats': self.beatCount - len(self.beats), 'stacks': self.stackCount - len(self.stacks),
                        'marks': self.markCount - len(self.marks)}, 'samplingErrors': self.samplingErrors,
            'limitations': ['函数分析会增加采集开销；不代表关闭探针后的帧率。',
                '源码标识来自开始记录时的磁盘文件，不能证明进程已加载更新；更新后请重新启动对应目录的Designer。',
                '响应延迟是Qt定时器调度延迟，不是显示器帧率，也不能单凭延迟认定某个函数是根因。',
                '调用栈只含Python帧，不包含Qt/GPU原生栈；被长期持有的GIL可能使采样缺失。',
                'Windows线程CPU时钟可能较粗，短函数应结合调用次数、调用者与多次复现分析。',
                '事件计数包含Qt事件转发，不等同于用户操作次数。',
                '没有采集控件文字、绑定值、图片、工程内容、凭据或Python局部变量。']}

    def export(self, root):
        data = self.report()
        destination = Path(root) / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
        destination.mkdir(parents=True, exist_ok=False)
        (destination / 'report.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        source = self.metadata.get('source', {})
        lines = ['# Designer 性能探针报告', '', f"采集时长：{data['elapsedMs'] / 1000:.1f} 秒。",
            f"源码 HEAD：{source.get('head', '未知')}；分支：{source.get('branch', '未知')}；"
            f"有未提交修改：{source.get('dirty', '未知')}。具体源码文件 SHA256 见 report.json。",
            '函数分析：' + ('已开启，以下为GUI线程CPU统计。' if data['detailed'] else '未开启，函数表为空；仍可分析延迟和调用栈。'),
            f"界面响应额外延迟：P95 {data['heartbeat']['p95Ms']:.2f} ms，最大 {data['heartbeat']['maxMs']:.2f} ms；"
            f"超过50 ms的记录 {data['heartbeat']['lateOver50Ms']} 次。", '',
            '## CPU耗时分类', '', '| 环节 | 自身累计CPU毫秒 |', '| --- | ---: |']
        lines.extend(f"| {row['area']} | {row['selfCpuMs']:.2f} |" for row in self.areas)
        lines += ['', '## 自身CPU耗时最高的函数', '', '| 函数位置 | 次数 | 自身CPU毫秒 | 含子调用CPU毫秒 |',
                  '| --- | ---: | ---: | ---: |']
        lines.extend(f"| `{row['file']}:{row['line']} {row['function'].replace('|', '/')}` | {row['calls']} | "
                     f"{row['selfCpuMs']:.2f} | {row['totalCpuMs']:.2f} |" for row in self.functions[:40])
        lines += ['', '## 含子调用CPU耗时最高的工程函数', '',
                  '用于从布局、渲染等入口向下追查；包含时间不能相加。', '',
                  '| 函数位置 | 次数 | 含子调用CPU毫秒 |', '| --- | ---: | ---: |']
        projectFunctions = [row for row in self.functions if row['file'].startswith('src/emo_master/')]
        lines.extend(f"| `{row['file']}:{row['line']} {row['function']}` | {row['calls']} | {row['totalCpuMs']:.2f} |"
                     for row in sorted(projectFunctions, key=lambda row: row['totalCpuMs'], reverse=True)[:25])
        lines += ['', '## 响应延迟最大的时刻', '', '| 采集后毫秒 | 额外延迟毫秒 | 工作区 |', '| ---: | ---: | --- |']
        lines.extend(f"| {row['atMs']:.1f} | {row['lateMs']:.1f} | {row['workspace']} |"
                     for row in sorted(self.beats, key=lambda row: row['lateMs'], reverse=True)[:15])
        lines += ['', '## 解读边界', '', *['- ' + value for value in data['limitations']], '',
                  f"- 有限缓冲区省略计数：{data['omitted']}；采样异常：{self.samplingErrors}。",
                  '- report.json包含完整函数统计、调用者、延迟时间线、手动标记及卡顿期间的Python栈。',
                  '- 先看延迟时间点对应的调用栈，再核对CPU热点；调用次数很多但单次很短也可能形成累计负担。']
        (destination / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return destination
