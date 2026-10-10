#!/usr/bin/env python3
"""Prepare the image's service client and dependency candidates without executing it."""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def prepare(out, log):
    summaries = sorted(Path('work/asahi').glob('bionic-probe-*/summary.json'),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    previous = None
    for path in summaries:
        data = json.loads(path.read_text())
        if data.get('exit_status') == 0 and data.get('execution', {}).get('returncode') == 0:
            previous = path
            break
    if previous is None:
        raise ValueError('no completed Bionic runtime found')
    spec = importlib.util.spec_from_file_location('recovery', Path(__file__).with_name('audit-recovery.py'))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    image = Path(data['image']).resolve()
    _, parts = helper.partitions(image)
    extents = parts['system_a']['extents']
    if len(extents) != 1 or 'offset' not in extents[0]:
        raise ValueError('system_a is not one linear extent')
    offset = extents[0]['offset']
    runtime = out / 'android-runtime'
    runtime.mkdir()
    libs = runtime / 'lib64'
    libs.mkdir()
    result = {'previous': str(previous.resolve()), 'image': str(image), 'elf': []}
    old = previous.parent / 'runtime'

    def inspect(path, origin):
        if path.stat().st_size > 64 << 20:
            raise ValueError('runtime member exceeds 64 MiB limit')
        info = helper.elf_info(path.read_bytes())
        if info['machine'] != 183 or not info['aligned_16k']:
            raise ValueError(f'{origin}: not ARM64/16 KiB ELF')
        info['origin'] = origin
        result['elf'].append(info)
        log('ELF ' + origin + ': DT_NEEDED=' + ', '.join(info['needed']))
        return info['needed']

    def copy_command(command, dest, limit=64 << 20):
        with dest.open('xb') as output, (out / 'extraction.stderr').open('ab') as error:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error)
            count = 0
            try:
                while True:
                    chunk = process.stdout.read(1 << 20)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit:
                        raise ValueError('extraction limit exceeded')
                    output.write(chunk)
                if process.wait(timeout=120):
                    raise ValueError('image file extraction failed: ' + repr(command))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()

    def extract(path, dest):
        copy_command(['dump.erofs', f'--offset={offset}', f'--path={path}', '--cat', str(image)], dest)

    def extract_extra_bionic(name, dest):
        # libdl_android may be a /system symlink into the runtime APEX.
        spec = importlib.util.spec_from_file_location('graphics', Path(__file__).with_name('audit-graphics.py'))
        graphics = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(graphics)
        with tempfile.TemporaryDirectory(prefix='apex-scratch-', dir=out) as directory:
            scratch = Path(directory)
            package = scratch / 'runtime.apex'
            copy_command(['dump.erofs', f'--offset={offset}', '--path=/system/apex/com.android.runtime.apex',
                          '--cat', str(image)], package, 512 << 20)
            payload = scratch / 'payload.img'
            graphics.extract_payload(package, payload)
            with payload.open('rb') as source:
                source.seek(1024)
                signature = source.read(1024)
            member = '/lib64/bionic/' + name
            if signature[:4] == bytes.fromhex('e2e1f5e0'):
                copy_command(['dump.erofs', f'--path={member}', '--cat', str(payload)], dest)
            elif signature[56:58] == bytes.fromhex('53ef') and shutil.which('debugfs'):
                copy_command(['debugfs', '-R', 'cat ' + member, str(payload)], dest)
            else:
                raise ValueError('extra Bionic library requires EROFS or ext4 with debugfs')

    linker = runtime / 'linker64'
    if (old / 'linker64').stat().st_size > 64 << 20:
        raise ValueError('previous linker exceeds limit')
    shutil.copyfile(old / 'linker64', linker)
    pending = set(inspect(linker, 'completed Bionic runtime:linker64'))
    service = runtime / 'service'
    extract('/system/bin/service', service)
    pending.update(inspect(service, 'system_a:/system/bin/service'))
    done = {'ld-android.so'}
    # Reuse only inspected dependencies, preserving the previously extracted Bionic libc.
    while pending:
        name = sorted(pending)[0]
        pending.remove(name)
        if name in done:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_.@+-]+', name) or len(done) >= 64:
            raise ValueError('unsupported dependency name or 64-library limit reached')
        destination = libs / name
        candidate = old / 'lib64' / name
        if candidate.is_file():
            if candidate.stat().st_size > 64 << 20:
                raise ValueError('previous library exceeds limit')
            shutil.copyfile(candidate, destination)
            origin = 'completed Bionic runtime:' + name
        else:
            if name == 'libdl_android.so':
                extract_extra_bionic(name, destination)
                origin = 'runtime APEX:/lib64/bionic/' + name
            else:
                extract('/system/lib64/' + name, destination)
                origin = 'system_a:/system/lib64/' + name
        pending.update(inspect(destination, origin))
        done.add(name)
    if not any(e['origin'].endswith('libbinder.so') for e in result['elf']):
        raise ValueError('service dependency closure did not include libbinder.so')
    linker.chmod(0o700)
    service.chmod(0o700)
    result['runtime'] = str(runtime)
    log('Prepared Android service client; no Android binary executed during extraction.')
    return result
