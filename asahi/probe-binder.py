#!/usr/bin/env python3
"""Build and run a native Binder echo probe in temporary user/mount namespaces."""
import argparse
from collections import deque
import datetime
import fcntl
import json
import re
import os
from pathlib import Path
import platform
import shutil
import signal
import select
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--temporary-policy', action='store_true',
                        help='install a dedicated server policy for this attempt, then remove it')
    parser.add_argument('--android-service', action='store_true',
                        help='run the image service list client against a native empty-list stub')
    args = parser.parse_args()
    if not args.temporary_policy:
        parser.error('this revision requires --temporary-policy; see NEXT_STEPS.md for scope and reversal')
    if os.geteuid() == 0:
        sys.exit('Run as your ordinary user, without sudo.')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or os.sysconf('SC_PAGE_SIZE') != 16384:
        sys.exit('Requires the Fedora Asahi ARM64/16 KiB target.')
    required = ['cc', 'unshare', 'runcon', 'sudo', 'semodule', 'chcon', 'ldd']
    if args.android_service:
        required.extend(['dump.erofs', 'strace'])
    for tool in required:
        if not shutil.which(tool):
            sys.exit(f'Missing {tool}; send this error. No packages were installed.')
    if Path('/sys/fs/selinux/enforce').read_text().strip() != '1':
        sys.exit('Requires SELinux enforcing; no enforcement change will be made.')
    context = subprocess.check_output(['id', '-Z'], text=True).strip()
    if context.split(':')[1:3] != ['unconfined_r', 'unconfined_t']:
        sys.exit('Requires the previously observed unconfined_r/unconfined_t user context.')
    parent = Path('work/asahi')
    print('THIS STEP MODIFIES STORAGE', flush=True)
    print('Creates a new work/asahi/binder-probe-* directory containing a native probe and logs.', flush=True)
    print('Temporarily mounts binderfs only in new user/mount namespaces; namespace exit removes the mount.', flush=True)
    print('Temporarily installs gbos_binder_probe SELinux module and labels only the new probe executable.', flush=True)
    print('Cleanup restores the original executable context and removes only this new policy module.', flush=True)
    print('If interrupted during cleanup: sudo semodule -r gbos_binder_probe; see NEXT_STEPS.md.', flush=True)
    print('Existing files are retained. Delete the new result directory to reverse generated files.', flush=True)
    parent.mkdir(parents=True, exist_ok=True)
    prefix = 'libbinder-probe-' if args.android_service else 'binder-probe-'
    out = Path(tempfile.mkdtemp(prefix=prefix, dir=parent)).resolve()
    mountpoint = out / 'binderfs'
    mountpoint.mkdir()
    source_name = 'probe-libbinder.c' if args.android_service else 'probe-binder.c'
    source = Path(__file__).with_name(source_name).resolve()
    binary = out / 'binder-echo'
    lock = (parent / 'binder-policy.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit('Another Binder policy run is active; retain its results.')
    installed = False
    install_attempted = False
    original_context = None
    result = {'context': context, 'cleanup': {}, 'page_size': os.sysconf('SC_PAGE_SIZE'), 'build': None, 'execution': None,
              'exit_status': 1, 'limitations': ['Native Linux UAPI echo only; Android libbinder integration remains untested.',
                                              'Only a temporary dedicated server policy is installed; Android services and existing Binder devices are retained.']}
    report = (out / 'report.txt').open('w')
    def log(text):
        print(text, flush=True)
        print(text, file=report, flush=True)
    def cleanup_previous_clients():
        expected = set()
        for summary in parent.glob('libbinder-probe-*/summary.json'):
            old = json.loads(summary.read_text())
            if old.get('exit_status') == 0:
                continue
            runtime_text = old.get('android_runtime', {}).get('runtime')
            if not runtime_text:
                continue
            runtime = Path(runtime_text).resolve()
            if runtime.parent != summary.parent.resolve() or runtime.name != 'android-runtime':
                continue
            linker, client = str(runtime / 'linker64'), str(runtime / 'service')
            expected.add((linker, (linker, client, 'list')))
            expected.add((linker, (client, 'list')))
        killed = []
        if expected and (not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal')):
            raise RuntimeError('pidfd support required for safe cleanup of previous failed clients')
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            fd = None
            matched = False
            try:
                fd = os.pidfd_open(int(entry.name))
                uid = re.search(r'^Uid:\s+(\d+)', (entry / 'status').read_text(), re.M)
                if not uid or int(uid[1]) != os.getuid():
                    continue
                argv = tuple(x.decode(errors='surrogateescape') for x in (entry / 'cmdline').read_bytes().split(b'\0') if x)
                executable = os.readlink(entry / 'exe')
                if (executable, argv) not in expected:
                    continue
                matched = True
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                poller = select.poll()
                poller.register(fd, select.POLLIN)
                if not poller.poll(3000):
                    raise RuntimeError('previous probe client did not exit after targeted cleanup')
                killed.append({'pid': int(entry.name), 'executable': executable, 'argv': argv})
                log(f'Cleaned previous failed probe client pid={entry.name}: {executable}')
            except PermissionError:
                if matched:
                    raise RuntimeError('permission denied cleaning a verified previous probe client')
                continue
            except (FileNotFoundError, ProcessLookupError):
                continue
            finally:
                if fd is not None:
                    os.close(fd)
        result['cleanup']['previous_clients'] = killed

    def run(command, name, seconds):
        isolated = command[0] != 'sudo'
        with (out / (name + '.stdout')).open('wb') as stdout, (out / (name + '.stderr')).open('wb') as stderr:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=isolated,
                                       env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
            try:
                if isolated:
                    # Observe exit without reaping: the leader PID cannot be reused
                    # before its remaining process group has been terminated.
                    deadline = time.monotonic() + seconds
                    while True:
                        event = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                        if event is not None:
                            code = event.si_status if event.si_code == os.CLD_EXITED else -event.si_status
                            break
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired(command, seconds)
                        time.sleep(0.05)
                else:
                    code = process.wait(timeout=seconds)
            except BaseException:
                if not isolated:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                raise
            finally:
                # Always drain isolated descendants, including after a launcher alarm.
                if isolated:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
        data = {'command': command, 'returncode': code,
                'stdout': (out / (name + '.stdout')).read_text(errors='replace'),
                'stderr': (out / (name + '.stderr')).read_text(errors='replace')}
        log(f'{name} exit status: {code}\n{data["stdout"]}{data["stderr"]}')
        return data
    try:
        log(f'Results: {out}')
        if args.android_service:
            cleanup_previous_clients()
            import importlib.util
            spec = importlib.util.spec_from_file_location('service_extract', Path(__file__).with_name('prepare-service.py'))
            extraction = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(extraction)
            result['android_runtime'] = extraction.prepare(out, log)
            result['property_fixture'] = {
                'path': '/dev/__properties__', 'scope': 'private mount namespace only',
                'format': 'Bionic ContextsPreSplit, magic 0x504f5250, version 0xfc6ed0ab',
                'properties': {'servicemanager.ready': 'true'},
                'publication': 'after native context-manager readiness handshake',
                'property_service_started': False,
            }
            result['limitations'] = ['Android service list against a native empty-list stub only; no Android servicemanager or services are started.',
                                    'servicemanager.ready is a private immutable fixture for the ready stub; Android init/property service and split property contexts are not demonstrated.',
                                    'Object/FD transfer, complete namespaces, VINTF, HALs and graphics remain untested.']
            log('Temporary /dev overlay and Binder alias are private to the child mount namespace.')
        result['build'] = run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', str(source), '-o', str(binary)], 'build', 60)
        if result['build']['returncode']:
            raise RuntimeError('build stopped; retain diagnostics, no execution attempted')
        namespace = ['unshare', '--user', '--map-root-user', '--mount', '--pid', '--mount-proc', '--propagation', 'private',
                     '--fork', '--kill-child']
        description = run(namespace + [str(binary), str(mountpoint), '--describe'], 'device-context', 30)
        if description['returncode']:
            raise RuntimeError('device context collection failed; policy not installed')
        match = re.search(r'^BINDER_DEVICE_CONTEXT=([^\n]+)', description['stdout'], re.M)
        if not match:
            raise RuntimeError('no Binder device context; policy not installed')
        def type_of(context):
            parts = context.split(':')
            if len(parts) < 4 or not re.fullmatch(r'[A-Za-z0-9_]+', parts[2]):
                raise ValueError('invalid target SELinux type')
            return parts[2]
        def file_context(path):
            return os.getxattr(path, 'security.selinux').decode().rstrip('\0')
        root_match = re.search(r'^BINDER_ROOT_CONTEXT=([^\n]+)', description['stdout'], re.M)
        if not root_match:
            raise RuntimeError('no binderfs root context; policy not installed')
        device_type = type_of(match[1])
        result['device_context'] = match[1]
        original_context = file_context(binary)
        result['original_executable_context'] = original_context
        directory_types = {type_of(file_context(p)) for p in binary.parents}
        directory_types.add(type_of(root_match[1]))
        library_types = set()
        symlink_types = set()
        def collect_library(path):
            library_types.add(type_of(file_context(path)))
            directory_types.update(type_of(file_context(p)) for p in path.parents)
            directory_types.update(type_of(file_context(p)) for p in path.resolve().parents)
            for p in [path, *path.parents]:
                if p.is_symlink():
                    label = os.getxattr(p, 'security.selinux', follow_symlinks=False).decode().rstrip('\0')
                    symlink_types.add(type_of(label))
        linked = run(['ldd', str(binary)], 'libraries', 30)
        if linked['returncode']:
            raise RuntimeError('library context collection failed')
        for path_text in re.findall(r'(/[^\s()]+)', linked['stdout']):
            path = Path(path_text)
            if path.is_file():
                collect_library(path)
        if not library_types:
            raise RuntimeError('no native runtime library types collected')
        cache = Path('/etc/ld.so.cache')
        if cache.is_file():
            collect_library(cache)
        log_types = {type_of(file_context(out / 'report.txt'))}
        template = Path(__file__).with_name('binder-probe-policy.cil.in').read_text()
        rules = [f'(allow gbos_binder_probe_t {device_type} (chr_file (open read write ioctl map getattr)))']
        rules += [f'(allow gbos_binder_probe_t {t} (dir (search getattr)))' for t in sorted(directory_types)]
        rules += [f'(allow gbos_binder_probe_t {t} (file (read open execute map getattr)))' for t in sorted(library_types)]
        rules += [f'(allow gbos_binder_probe_t {t} (lnk_file (read getattr)))' for t in sorted(symlink_types)]
        rules += [f'(allow gbos_binder_probe_t {t} (file (write append getattr)))' for t in sorted(log_types)]
        policy = out / 'gbos_binder_probe.cil'
        policy.write_text(template + '\n'.join(rules) + '\n')
        result['policy'] = {'device_type': device_type, 'library_types': sorted(library_types),
                            'directory_types': sorted(directory_types), 'log_types': sorted(log_types),
                            'symlink_types': sorted(symlink_types)}
        modules = run(['sudo', 'semodule', '-l'], 'existing-modules', 60)
        if modules['returncode']:
            raise RuntimeError('cannot inspect policy modules; no installation attempted')
        if any(line.split() and line.split()[0] == 'gbos_binder_probe' for line in modules['stdout'].splitlines()):
            raise RuntimeError('gbos_binder_probe already exists; preserve it and stop')
        install_attempted = True
        installation = run(['sudo', 'semodule', '-i', str(policy)], 'policy-install', 120)
        if installation['returncode']:
            raise RuntimeError('policy installation failed; retain diagnostics')
        installed = True
        label = run(['sudo', 'chcon', '-t', 'gbos_binder_probe_exec_t', str(binary)], 'probe-label', 30)
        if label['returncode']:
            raise RuntimeError('labeling new executable failed')
        result['start_time'] = datetime.datetime.now().isoformat()
        # util-linux isolates mount propagation; root privileges apply only inside the new user namespace.
        command = ['unshare', '--user', '--map-root-user', '--mount', '--pid', '--mount-proc', '--propagation', 'private',
                   '--fork', '--kill-child', str(binary), str(mountpoint)]
        if args.android_service:
            command.append(result['android_runtime']['runtime'])
        result['execution'] = run(command, 'service-list' if args.android_service else 'echo', 40)
        marker = 'LIBBINDER_LIST=PASS' if args.android_service else 'BINDER_ECHO=PASS'
        output = result['execution']['stdout']
        if args.android_service:
            required_markers = ('STUB_READY_PROPERTY=CREATED', 'LIBBINDER_STUB_REPLY=PASS',
                                'Found 0 services:', 'LIBBINDER_LIST=PASS')
            missing = [item for item in required_markers if item not in output]
            result['missing_markers'] = missing
            if missing:
                raise RuntimeError('libbinder probe missing expected evidence: ' + ', '.join(missing))
        if result['execution']['returncode'] or marker not in output:
            raise RuntimeError('private Binder execution did not pass; retain namespace/mount/driver diagnostics')
        result['exit_status'] = 0
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
        log('STOPPED: ' + result['error'])
    finally:
        if args.android_service:
            try:
                trace = out / 'android-runtime' / 'client.trace'
                if trace.is_file():
                    result['client_trace'] = str(trace)
                    with trace.open(errors='replace') as stream:
                        tail = ''.join(deque(stream, maxlen=100))
                    (out / 'client-trace-tail.txt').write_text(tail)
                    log('CLIENT TRACE TAIL:\n' + tail)
                else:
                    log('Client trace unavailable: execution may have stopped before the Android client started.')
            except BaseException as error:
                result['trace_error'] = str(error)
                result['exit_status'] = 1
                log('Trace collection failed; continuing policy cleanup: ' + str(error))
        # Attempt cleanup even when policy installation was interrupted or only partly completed.
        if install_attempted:
            try:
                if original_context is not None:
                    restored = run(['sudo', 'chcon', original_context, str(binary)], 'restore-context', 30)
                    result['cleanup']['context'] = restored['returncode']
                removed = run(['sudo', 'semodule', '-r', 'gbos_binder_probe'], 'policy-remove', 120)
                result['cleanup']['policy'] = removed['returncode']
                if removed['returncode'] or result['cleanup'].get('context', 0):
                    result['exit_status'] = 1
                    log('CLEANUP INCOMPLETE: retain logs and follow NEXT_STEPS.md cleanup instructions.')
            except BaseException as error:
                result['exit_status'] = 1
                result['cleanup']['error'] = str(error)
                log('CLEANUP INCOMPLETE: ' + str(error))
        if installed:
            try:
                if shutil.which('ausearch'):
                    run(['sudo', 'ausearch', '-m', 'AVC,USER_AVC', '-ts', 'recent', '-i'], 'audit-denials', 30)
            except Exception as error:
                log('Denial collection unavailable: ' + str(error))
        for limitation in result['limitations']:
            log('LIMITATION: ' + limitation)
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        log(f'Send {out / "report.txt"} and {out / "summary.json"}')
        report.close()
    return result['exit_status']


if __name__ == '__main__':
    sys.exit(main())
