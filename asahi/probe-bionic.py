#!/usr/bin/env python3
"""Extract a focused Android runtime and execute only Toybox uname as an ordinary user."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', help='completed graphics audit directory; defaults to latest')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('run as your ordinary user, without sudo')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        parser.error('requires the Fedora Asahi ARM64 target')
    if os.sysconf('SC_PAGE_SIZE') != 16384:
        parser.error('requires the previously observed 16 KiB page-size target')
    if not shutil.which('dump.erofs'):
        parser.error('dump.erofs is missing')
    parent = Path('work/asahi')
    candidates = [Path(args.previous)] if args.previous else sorted(
        parent.glob('graphics-audit-*/summary.json'), key=lambda x: x.stat().st_mtime, reverse=True)
    previous = None
    for candidate in candidates:
        path = candidate / 'summary.json' if candidate.is_dir() else candidate
        data = json.loads(path.read_text())
        if data.get('exit_status') == 0:
            previous = path
            break
    if previous is None:
        parser.error('no completed graphics audit found')
    image = Path(data['image']).resolve()
    if not image.is_file():
        parser.error(f'image missing: {image}')
    helper = module('recovery', 'audit-recovery.py')
    graphics = module('graphics', 'audit-graphics.py')
    _, parts = helper.partitions(image)
    extents = parts['system_a']['extents']
    if len(extents) != 1 or 'offset' not in extents[0]:
        parser.error('system_a must have a single linear extent')
    offset = extents[0]['offset']
    print('THIS STEP MODIFIES STORAGE', flush=True)
    print('Creates only a new work/asahi/bionic-probe-* directory on the home filesystem.', flush=True)
    print('Retains extracted runtime copies and logs; temporary APEX copies are removed.', flush=True)
    print('Source image and earlier results are retained. Delete the new directory to reverse.', flush=True)
    out = Path(tempfile.mkdtemp(prefix='bionic-probe-', dir=parent)).resolve()
    report = (out / 'report.txt').open('w')
    result = {'image': str(image), 'previous': str(previous.resolve()), 'elf': [],
              'issues': [], 'execution': None, 'page_size': os.sysconf('SC_PAGE_SIZE'),
              'limitations': ['Toybox uname only; this does not demonstrate Android boot, Binder, HALs or graphics.',
                              'Collected DT_NEEDED candidates do not prove every Android linker namespace or dlopen path.']}

    def log(text):
        print(text, flush=True)
        print(text, file=report, flush=True)

    def read_command(command, destination, limit):
        # Bound output while copying; always reap a terminated producer.
        with destination.open('xb') as target, tempfile.TemporaryFile() as diagnostics:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=diagnostics)
            total = 0
            try:
                while True:
                    block = process.stdout.read(1 << 20)
                    if not block:
                        break
                    total += len(block)
                    if total > limit:
                        raise ValueError(f'copy exceeds {limit // (1 << 20)} MiB limit')
                    target.write(block)
                status = process.wait(timeout=120)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()
                diagnostics.seek(0)
                with (out / 'tool-diagnostics.txt').open('ab') as saved:
                    saved.write((repr(command) + '\n').encode())
                    shutil.copyfileobj(diagnostics, saved)
            if status:
                raise ValueError(f'tool exit {status}: {command[0]}')

    def image_command(path):
        return ['dump.erofs', f'--offset={offset}', f'--path={path}', '--cat', str(image)]

    def inspect(path, origin):
        info = helper.elf_info(path.read_bytes())
        if info['machine'] != 183 or not info['aligned_16k']:
            raise ValueError(f'{origin}: not ARM64 with 16 KiB load alignment')
        info['origin'] = origin
        result['elf'].append(info)
        log(f'ELF {origin}: ARM64/16KiB; DT_NEEDED=' + ', '.join(info['needed']))
        return info['needed']

    code = 1
    try:
        log(f'Results: {out}')
        runtime = out / 'runtime'
        runtime.mkdir()
        libs = runtime / 'lib64'
        libs.mkdir()
        pending = set()
        with tempfile.TemporaryDirectory(prefix='apex-scratch-', dir=out) as scratch:
            scratch = Path(scratch)
            package = scratch / 'runtime.apex'
            read_command(image_command('/system/apex/com.android.runtime.apex'), package, 512 << 20)
            payload = scratch / 'payload.img'
            graphics.extract_payload(package, payload)
            with payload.open('rb') as f:
                f.seek(1024)
                sb = f.read(1024)
            if sb[:4] == bytes.fromhex('e2e1f5e0'):
                def payload_command(path):
                    return ['dump.erofs', f'--path={path}', '--cat', str(payload)]
            elif sb[56:58] == bytes.fromhex('53ef') and shutil.which('debugfs'):
                def payload_command(path):
                    return ['debugfs', '-R', 'cat ' + path, str(payload)]
            else:
                raise ValueError('runtime payload requires EROFS or ext4 with read-only debugfs')
            linker = runtime / 'linker64'
            read_command(payload_command('/bin/linker64'), linker, 64 << 20)
            pending.update(inspect(linker, 'runtime APEX:/bin/linker64'))
            for name in ('libc.so', 'libm.so', 'libdl.so'):
                dest = libs / name
                read_command(payload_command('/lib64/bionic/' + name), dest, 64 << 20)
                pending.update(inspect(dest, 'runtime APEX:/lib64/bionic/' + name))

        toybox = runtime / 'toybox'
        read_command(image_command('/system/bin/toybox'), toybox, 64 << 20)
        pending.update(inspect(toybox, 'system_a:/system/bin/toybox'))
        copied = {'libc.so', 'libm.so', 'libdl.so'}
        # Bionic's ld-android.so names the linker's own exported pseudo-library.
        copied.add('ld-android.so')
        log('ld-android.so is supplied by the Android linker, not copied from the host.')
        while pending:
            name = sorted(pending)[0]
            pending.remove(name)
            if name in copied:
                continue
            if not re.fullmatch(r'[A-Za-z0-9_.@+-]+', name):
                raise ValueError(f'unsupported dependency name: {name!r}')
            if len(copied) >= 64:
                raise ValueError('dependency closure exceeds 64-library limit')
            dest = libs / name
            read_command(image_command('/system/lib64/' + name), dest, 64 << 20)
            pending.update(inspect(dest, 'system_a:/system/lib64/' + name))
            copied.add(name)
        linker.chmod(0o700)
        toybox.chmod(0o700)
        # Invoke the image's linker explicitly: no /system or /apex host installation.
        command = [str(linker), str(toybox), 'uname', '-a']
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LD_LIBRARY_PATH': str(libs)}
        log('Executing only: extracted linker64 + extracted toybox uname -a')
        with (out / 'uname.stdout').open('wb') as stdout, (out / 'uname.stderr').open('wb') as stderr:
            completed = subprocess.run(command, cwd=runtime, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, timeout=20)
        output = (out / 'uname.stdout').read_text(errors='replace')
        errors = (out / 'uname.stderr').read_text(errors='replace')
        result['execution'] = {'command': command, 'returncode': completed.returncode,
                               'stdout': output, 'stderr': errors}
        log(f'Toybox exit status: {completed.returncode}\n{output}{errors}')
        if completed.returncode != 0 or not output.startswith('Linux '):
            raise ValueError('Toybox uname did not complete successfully; retain linker diagnostics')
        code = 0
        log('BIONIC_UNAME=PASS: Android Toybox executed through its Android linker on Fedora Asahi.')
    except Exception as e:
        message = f'{type(e).__name__}: {e}'
        result['issues'].append(message)
        log('STOPPED: ' + message)
    finally:
        result['exit_status'] = code
        for limitation in result['limitations']:
            log('LIMITATION: ' + limitation)
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        log(f'Send {out / "report.txt"} and {out / "summary.json"}')
        report.close()
    return code


if __name__ == '__main__':
    sys.exit(main())
