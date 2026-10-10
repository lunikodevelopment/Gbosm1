#!/usr/bin/env python3
"""Build and run a native Binder echo probe in temporary user/mount namespaces."""
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile


def main():
    if os.geteuid() == 0:
        sys.exit('Run as your ordinary user, without sudo.')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or os.sysconf('SC_PAGE_SIZE') != 16384:
        sys.exit('Requires the Fedora Asahi ARM64/16 KiB target.')
    for tool in ('cc', 'unshare'):
        if not shutil.which(tool):
            sys.exit(f'Missing {tool}; send this error. No packages were installed.')
    parent = Path('work/asahi')
    print('THIS STEP MODIFIES STORAGE', flush=True)
    print('Creates a new work/asahi/binder-probe-* directory containing a native probe and logs.', flush=True)
    print('Temporarily mounts binderfs only in new user/mount namespaces; namespace exit removes the mount.', flush=True)
    print('Existing files are retained. Delete the new result directory to reverse generated files.', flush=True)
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='binder-probe-', dir=parent)).resolve()
    mountpoint = out / 'binderfs'
    mountpoint.mkdir()
    source = Path(__file__).with_name('probe-binder.c').resolve()
    binary = out / 'binder-echo'
    result = {'page_size': os.sysconf('SC_PAGE_SIZE'), 'build': None, 'execution': None,
              'exit_status': 1, 'limitations': ['Native Linux UAPI echo only; Android libbinder integration remains untested.',
                                              'No Android services, existing Binder devices or system policy are changed.']}
    report = (out / 'report.txt').open('w')
    def log(text):
        print(text, flush=True)
        print(text, file=report, flush=True)
    def run(command, name, seconds):
        with (out / (name + '.stdout')).open('wb') as stdout, (out / (name + '.stderr')).open('wb') as stderr:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            try:
                code = process.wait(timeout=seconds)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
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
        for limitation in result['limitations']:
            log('LIMITATION: ' + limitation)
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        log(f'Send {out / "report.txt"} and {out / "summary.json"}')
        report.close()
    return result['exit_status']


if __name__ == '__main__':
    sys.exit(main())
