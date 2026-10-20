"""Read-only Codex usage overlay. No model calls or credential file reads."""
import argparse
import ctypes
import datetime as dt
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
from pg_storage import Store

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
ALLOWED = {'initialize', 'account/rateLimits/read', 'account/usage/read'}


def codex_executable():
    override = os.environ.get('USAGE_METER_CODEX_EXE')
    if override and Path(override).is_file():
        return override
    base = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@openai/codex'
    matches = list(base.glob('node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe'))
    if not matches:
        raise RuntimeError('Codex executable not found. Set USAGE_METER_CODEX_EXE to codex.exe.')
    return str(matches[0])


class Client:
    def __init__(self):
        self.process = subprocess.Popen(
            [codex_executable(), 'app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        self.responses = queue.Queue()
        self.counter = 0
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'usage_meter', 'version': '1.0.0'}})
            self._send({'method': 'initialized'})
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.responses.put(json.loads(line))
                except ValueError:
                    pass
        finally:
            self.responses.put({'disconnected': True})

    def _send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def call(self, method, params=None):
        if method not in ALLOWED:
            raise ValueError('Only read-only account requests are allowed')
        self.counter += 1
        request = {'id': self.counter, 'method': method}
        if params is not None:
            request['params'] = params
        self._send(request)
        deadline = time.monotonic() + 30
        while True:
            response = self.responses.get(timeout=max(.01, deadline - time.monotonic()))
            if response.get('disconnected'):
                raise RuntimeError('Codex account connection closed')
            if response.get('id') != self.counter:
                continue
            if 'error' in response:
                raise RuntimeError(response['error'].get('message', 'Account request failed'))
            return response['result']

    def close(self):
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
        except OSError:
            pass


def windows(payload):
    buckets = payload.get('rateLimitsByLimitId') or {'codex': payload.get('rateLimits')}
    result = []
    for key, bucket in buckets.items():
        if not bucket:
            continue
        for kind in ('primary', 'secondary'):
            window = bucket.get(kind)
            if window and window.get('usedPercent') is not None and window.get('resetsAt') is not None:
                result.append(dict(window, bucket=key, kind=kind))
    return result


def weekly(payload):
    options = [w for w in windows(payload) if w['windowDurationMins'] == 10080]
    return next((w for w in options if w['bucket'] == 'codex'), options[0] if options else None)


def budget(rows, current, now=None):
    """Track from first observation; never invent historical daily attribution."""
    now = now or dt.datetime.now().astimezone()
    reset = current['resetsAt']
    matching = [(stamp, used) for stamp, used, r in rows if abs(r - reset) <= 60]
    if not matching:
        return None
    first_stamp, first_used = matching[0]
    first_day = dt.datetime.fromtimestamp(first_stamp).astimezone().date()
    days = max(0, (now.date() - first_day).days)
    spent = max(0., current['usedPercent'] - first_used)
    allocated = min(max(0., 100 - first_used), 14. * (days + 1))
    previous_days = [(s, u) for s, u in matching if dt.datetime.fromtimestamp(s).astimezone().date() < now.date()]
    base = previous_days[-1][1] if previous_days else first_used
    return {'today': max(0., current['usedPercent'] - base),
            'available': max(0., allocated - spent), 'over': max(0., spent - allocated),
            'partial': days == 0, 'baseline': first_used,
            'decrease': any(b[1] < a[1] for a, b in zip(matching, matching[1:]))}


class App:
    def __init__(self):
        DATA.mkdir(exist_ok=True)
        self.store = Store()
        self.root = tk.Tk()
        self.root.title('Usage Meter')
        self.root.configure(bg='#18212b')
        self.root.attributes('-topmost', True)
        self.root.resizable(False, False)
        self.root.geometry(self.geometry())
        self.root.protocol('WM_DELETE_WINDOW', self.quit)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.refresh = threading.Event()
        self.last = self.store.latest('limits')
        self.activity = self.store.latest('activity')
        self.error = 'Waiting for fresh reading'
        self.connection = None
        self.next_poll = time.monotonic()
        self.compact = True
        self.header = tk.Frame(self.root, bg='#18212b')
        self.header.pack(fill='x')
        self.icon_refresh = tk.Button(self.header, text='↻', font=('Segoe UI', 14), command=self.refresh.set, fg='white', bg='#18212b', bd=0, takefocus=False)
        self.icon_full = tk.Button(self.header, text='⛶', font=('Segoe UI', 14), command=self.toggle_view, fg='white', bg='#18212b', bd=0, takefocus=False)
        self.primary = tk.Label(self.header, text='Connecting…', font=('Segoe UI', 16, 'bold'), fg='#77e0c3', bg='#18212b')
        self.primary.pack(anchor='w', padx=12, pady=(8, 2))
        self.daily = tk.Label(self.root, font=('Segoe UI', 10), fg='white', bg='#18212b')
        self.daily.pack(anchor='w', padx=12)
        self.status = tk.Label(self.root, font=('Segoe UI', 9), fg='#aebdca', bg='#18212b')
        self.status.pack(anchor='w', padx=12, pady=(3, 5))
        controls = self.controls = tk.Frame(self.root, bg='#18212b')
        controls.pack(fill='x', padx=8, pady=(0, 6))
        for title, action in [('Refresh', self.refresh.set), ('Details', self.details), ('Minimize', self.root.iconify), ('Exit', self.quit)]:
            tk.Button(controls, text=title, command=action, takefocus=False).pack(side='left', padx=3)
        for widget in (self.header, self.primary, self.status):
            widget.bind('<ButtonPress-1>', self.drag_start)
            widget.bind('<B1-Motion>', self.drag_move)
            widget.bind('<Button-3>', self.context_menu)
        self.apply_view()
        threading.Thread(target=self.collect, daemon=True).start()
        self.tick()

    def drag_start(self, event):
        self.drag_offset = (event.x_root-self.root.winfo_x(), event.y_root-self.root.winfo_y())

    def drag_move(self, event):
        if self.compact:
            x, y = self.drag_offset
            self.root.geometry(f'+{event.x_root-x}+{event.y_root-y}')

    def context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='Full view' if self.compact else 'Compact view', command=self.toggle_view)
        menu.add_command(label='Exit', command=self.quit)
        menu.tk_popup(event.x_root, event.y_root)

    def toggle_view(self):
        self.compact = not self.compact
        self.apply_view()
        self.render()

    def apply_view(self):
        self.primary.pack_forget()
        self.icon_refresh.pack_forget()
        self.icon_full.pack_forget()
        self.daily.pack_forget()
        self.status.pack_forget()
        self.controls.pack_forget()
        self.root.overrideredirect(self.compact)
        self.root.geometry('280x40' if self.compact else '440x160')
        self.icon_full.config(text='⛶' if self.compact else '▣')
        self.icon_full.pack(side='right', padx=(2,8))
        self.icon_refresh.pack(side='right')
        self.primary.pack(side='left', padx=(8,4) if self.compact else 12, pady=(5,2))
        if self.compact:
            self.status.pack(in_=self.header, side='left', padx=(0,4), pady=(5,2))
        else:
            self.daily.pack(anchor='w', padx=12)
            self.status.pack(in_=self.root, anchor='w', padx=12, pady=(3,5))
        if not self.compact:
            self.controls.pack(fill='x', padx=8, pady=(0,6))
        self.root.attributes('-topmost', True)

    def geometry(self):
        try:
            pos = json.loads((DATA / 'position.json').read_text())
            x = min(max(0, int(pos['x'])), max(0, self.root.winfo_screenwidth()-440))
            y = min(max(0, int(pos['y'])), max(0, self.root.winfo_screenheight()-180))
            return f'440x160+{x}+{y}'
        except (OSError, ValueError, KeyError):
            return f'440x160+{max(0,self.root.winfo_screenwidth()-470)}+50'

    def collect(self):
        activity_due = 0
        previous_window = None
        try:
            while not self.stop.is_set():
                try:
                    if self.connection is None:
                        self.connection = Client()
                    payload = self.connection.call('account/rateLimits/read')
                    self.store.save('limits', payload)
                    self.events.put(('limits', payload))
                    current_window = weekly(payload)
                    changed = current_window != previous_window
                    previous_window = current_window
                    if changed or time.monotonic() >= activity_due:
                        try:
                            activity = self.connection.call('account/usage/read')
                            self.store.save('activity', activity)
                            self.events.put(('activity', activity))
                        except Exception as exc:
                            self.events.put(('activity_error', str(exc)))
                        activity_due = time.monotonic() + 900
                except Exception as exc:
                    self.events.put(('error', str(exc) or type(exc).__name__))
                    if self.connection:
                        self.connection.close()
                        self.connection = None
                self.next_poll = time.monotonic() + 60
                self.refresh.wait(60)
                self.refresh.clear()
        finally:
            if self.connection:
                self.connection.close()
            self.store.close()

    def tick(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind in ('limits', 'activity'):
                    if kind == 'limits':
                        self.last = (time.time(), value)
                        self.error = None
                    else:
                        self.activity = (time.time(), value)
                elif kind == 'error':
                    self.error = value
        except queue.Empty:
            pass
        self.render()
        self.root.after(1000, self.tick)

    def render(self):
        if not self.last:
            self.status.config(text='Unavailable — retrying' if self.error else 'Connecting…')
            return
        stamp, payload = self.last
        w = weekly(payload)
        age = int(time.time() - stamp)
        if not w:
            self.primary.config(text='Weekly allowance unavailable')
            self.daily.config(text='Other reported windows are in Details')
        else:
            remaining = max(0., min(100., 100-w['usedPercent']))
            stale = bool(self.error) or age > 180 or time.time() >= w['resetsAt']
            label = f'{remaining:g}%' if self.compact else f'{remaining:g}% reported left'
            self.primary.config(text=label + (' !' if stale and self.compact else ' · STALE' if stale else ''))
            b = budget(self.store.rows(w['resetsAt']), w)
            if b:
                if b['decrease']:
                    line = 'Allowance adjustment detected — budget uncertain'
                else:
                    line = f"Observed today: {b['today']:g}pp · Budget left: {b['available']:g}pp"
                    if b['partial']:
                        line += ' *'
                self.daily.config(text=line)
                self.primary.config(fg='#ffba75' if stale or b['available'] <= 2 else '#77e0c3')
            seconds = max(0, int(w['resetsAt'] - time.time()))
            days, hours = seconds//86400, seconds%86400//3600
            countdown = max(0, math.ceil(self.next_poll-time.monotonic()))
            if self.compact:
                self.status.config(text=f'Reset {days}d {hours}h · {countdown}s' + (' !' if stale or self.store.storage_error else ''))
            else:
                self.status.config(text=f'Reset: {days}d {hours}h · Refresh {countdown}s · Checked {age}s ago')

    def details(self):
        lines = ['Read-only polling: allowance every 60s; daily tokens every 15min.',
                 'No model calls. No automatic resets. All percentages are service-reported.',
                 'pp = percentage points. Daily allocation: 14pp, with carryover.',
                 '* First day starts at your first observation, not midnight.',
                 'Today uses the last reading before local midnight when available.',
                 'Gaps/delayed accounting can shift daily attribution. This is not a hard cap.',
                 'Token-history bucket dates are preserved exactly as returned by Codex.', '']
        if self.last:
            for w in windows(self.last[1]):
                reset = dt.datetime.fromtimestamp(w['resetsAt']).astimezone().strftime('%Y-%m-%d %H:%M %Z')
                lines.append(f"{w['bucket']}/{w['kind']}: {w['usedPercent']}% used; {w['windowDurationMins']} min; reset {reset}")
        if self.activity:
            summary = self.activity[1].get('summary') or {}
            lines.append('\nLifetime tokens: ' + f"{summary.get('lifetimeTokens', 'Unavailable'):,}" if isinstance(summary.get('lifetimeTokens'), int) else '\nLifetime tokens: unavailable')
            for row in sorted(self.activity[1].get('dailyUsageBuckets') or [], key=lambda row: row['startDate'])[-7:]:
                lines.append(f"{row['startDate']}: {row['tokens']:,} tokens")
        if self.error:
            lines.append('\nConnection status: ' + self.error[:300])
        lines.append('\nData: PostgreSQL configured target / usage_meter')
        lines.append('Storage: ' + (self.store.storage_error or 'Connected; readings saved to PostgreSQL'))
        messagebox.showinfo('Usage Meter — details', '\n'.join(lines), parent=self.root)

    def quit(self):
        (DATA / 'position.json').write_text(json.dumps({'x': self.root.winfo_x(), 'y': self.root.winfo_y()}))
        self.stop.set()
        self.refresh.set()
        if self.connection:
            self.connection.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true', help='Fetch read-only account status, print summary, exit')
    args = parser.parse_args()
    if args.probe:
        client = Client()
        try:
            limits = client.call('account/rateLimits/read')
            activity = client.call('account/usage/read')
            print(json.dumps({'windows': windows(limits), 'summary': activity.get('summary'),
                              'dailyBucketCount': len(activity.get('dailyUsageBuckets') or [])}, indent=2))
        finally:
            client.close()
        return
    # Prevent duplicate collectors on this Windows desktop.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, 'Local\\CodexUsageMeter')
    if ctypes.get_last_error() == 183:
        return
    try:
        App().root.mainloop()
    finally:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)


if __name__ == '__main__':
    main()
