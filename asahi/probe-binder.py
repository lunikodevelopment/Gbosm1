#!/usr/bin/env python3
"""Build and run a native Binder echo probe in temporary user/mount namespaces."""
import argparse
import datetime
import fcntl
import json
import re
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--temporary-policy', action='store_true',
                        help='install a dedicated server policy for this attempt, then remove it')
    args = parser.parse_args()
    if not args.temporary_policy:
        parser.error('this revision requires --temporary-policy; see NEXT_STEPS.md for scope and reversal')
    if os.geteuid() == 0:
        sys.exit('Run as your ordinary user, without sudo.')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or os.sysconf('SC_PAGE_SIZE') != 16384:
        sys.exit('Requires the Fedora Asahi ARM64/16 KiB target.')
    for tool in ('cc', 'unshare', 'runcon', 'sudo', 'semodule', 'chcon', 'ldd'):
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
    out = Path(tempfile.mkdtemp(prefix='binder-probe-', dir=parent)).resolve()
    mountpoint = out / 'binderfs'
    mountpoint.mkdir()
    source = Path(__file__).with_name('probe-binder.c').resolve()
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
    def run(command, name, seconds):
        isolated = command[0] != 'sudo'
        with (out / (name + '.stdout')).open('wb') as stdout, (out / (name + '.stderr')).open('wb') as stderr:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=isolated,
                                       env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
            try:
                code = process.wait(timeout=seconds)
            except BaseException:
                try:
                    if isolated:
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
                process.wait()
                raise
        data = {'command': command, 'returncode': code,
                'stdout': (out / (name + '.stdout')).read_text(errors='replace'),
                'stderr': (out / (name + '.stderr')).read_text(errors='replace')}
        log(f'{name} exit status: {code}\n{data["stdout"]}{data["stderr"]}')
        return data
    try:
        log(f'Results: {out}')
        result['build'] = run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', str(source), '-o', str(binary)], 'build', 60)
        if result['build']['returncode']:
            raise RuntimeError('build stopped; retain diagnostics, no execution attempted')
        namespace = ['unshare', '--user', '--map-root-user', '--mount', '--propagation', 'private',
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
        command = ['unshare', '--user', '--map-root-user', '--mount', '--propagation', 'private',
                   '--fork', '--kill-child', str(binary), str(mountpoint)]
        result['execution'] = run(command, 'echo', 30)
        if result['execution']['returncode'] or 'BINDER_ECHO=PASS' not in result['execution']['stdout']:
            raise RuntimeError('private Binder echo did not pass; retain namespace/mount/driver diagnostics')
        result['exit_status'] = 0
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
        log('STOPPED: ' + result['error'])
    finally:
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
