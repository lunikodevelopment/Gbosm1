#!/usr/bin/env python3
"""Prepare the image's servicemanager, its libraries and its fixture files without executing anything.

Used by probe-binder.py --real-servicemanager. Everything is copied into the run
directory: android-runtime/ (linker, servicemanager, libraries) and sm-root/
(the private root with the Android-path fixtures).
"""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

LIMIT = 64 << 20
FIXTURE_LIMIT = 8 << 20


def load(name, label):
    spec = importlib.util.spec_from_file_location(label, Path(__file__).with_name(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def previous_runtime():
    """Newest earlier run that left a complete extracted Android runtime."""
    summaries = []
    for prefix in ('real-servicemanager', 'android-parcel-probe', 'libbinder-probe'):
        summaries += list(Path('work/asahi').glob(prefix + '-*/summary.json'))
    for path in sorted(summaries, key=lambda p: p.stat().st_mtime, reverse=True):
        record = json.loads(path.read_text()).get('android_runtime', {})
        if not record.get('runtime'):
            continue
        candidate = Path(record['runtime']).resolve()
        if candidate.parent == path.parent.resolve() and candidate.name == 'android-runtime' \
                and (candidate / 'linker64').is_file() and (candidate / 'lib64').is_dir():
            return candidate, record
    raise ValueError('no earlier extracted Android runtime found; run --android-service first')


def prepare(out, log):
    helper = load('audit-recovery.py', 'recovery')
    graphics = load('audit-graphics.py', 'graphics')
    old, record = previous_runtime()
    image = Path(record['image']).resolve()
    _, parts = helper.partitions(image)
    offsets = {}
    for name in ('system_a', 'vendor_a'):
        extents = parts[name]['extents']
        if len(extents) != 1 or 'offset' not in extents[0]:
            raise ValueError(name + ' is not one linear extent')
        offsets[name] = extents[0]['offset']
    runtime = out / 'android-runtime'
    libs = runtime / 'lib64'
    root = out / 'sm-root'
    for directory in (runtime, libs, root):
        directory.mkdir(parents=True)
    result = {'previous_runtime': str(old), 'image': str(image), 'elf': [], 'unresolved': [], 'fixtures': []}

    def dump(part, path, mode):
        return ['dump.erofs', f'--offset={offsets[part]}', f'--path={path}', mode, str(image)]

    def copy_command(command, dest, limit):
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
                        raise ValueError('extraction limit exceeded: ' + repr(command[2]))
                    output.write(chunk)
                if process.wait(timeout=120):
                    raise ValueError('image file extraction failed: ' + repr(command[2]))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()

    def extract(part, path, dest, limit=LIMIT):
        copy_command(dump(part, path, '--cat'), dest, limit)

    def listing(part, path):
        r = subprocess.run(dump(part, path, '--ls'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
        return graphics.listing_names(r.stdout.decode(errors='replace')) if r.returncode == 0 else []

    def inspect(path, origin):
        info = helper.elf_info(path.read_bytes())
        if info['machine'] != 183 or not info['aligned_16k']:
            raise ValueError(f'{origin}: not ARM64/16 KiB ELF')
        info['origin'] = origin
        result['elf'].append(info)
        log('ELF ' + origin + ': DT_NEEDED=' + ', '.join(info['needed']))
        return info['needed']

    linker = runtime / 'linker64'
    shutil.copyfile(old / 'linker64', linker)
    pending = set(inspect(linker, 'earlier extracted runtime:linker64'))
    manager = runtime / 'servicemanager'
    extract('system_a', '/system/bin/servicemanager', manager)
    pending.update(inspect(manager, 'system_a:/system/bin/servicemanager'))
    done = {'ld-android.so'}
    while pending:
        name = sorted(pending)[0]
        pending.remove(name)
        if name in done:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_.@+-]+', name) or len(done) >= 96:
            raise ValueError('unsupported dependency name or 96-library limit reached')
        done.add(name)
        destination = libs / name
        candidate = old / 'lib64' / name
        try:
            if candidate.is_file():
                if candidate.stat().st_size > LIMIT:
                    raise ValueError('earlier library exceeds limit')
                shutil.copyfile(candidate, destination)
                origin = 'earlier extracted runtime:' + name
            else:
                extract('system_a', '/system/lib64/' + name, destination)
                origin = 'system_a:/system/lib64/' + name
            pending.update(inspect(destination, origin))
        except ValueError as error:
            destination.unlink(missing_ok=True)
            result['unresolved'].append({'library': name, 'reason': str(error)})
            log(f'UNRESOLVED library {name}: {error}')
    linker.chmod(0o700)
    manager.chmod(0o700)

    def place(part, source, relative, keep, depth):
        """Copy regular files (kind 1) below source into root/relative; skip symlinks."""
        for kind, name in listing(part, source):
            path = source.rstrip('/') + '/' + name
            target = root / relative / name
            if kind == 2 and depth > 0:
                target.mkdir(parents=True, exist_ok=True)
                place(part, path, relative + '/' + name, keep, depth - 1)
            elif kind == 1 and keep(name):
                target.parent.mkdir(parents=True, exist_ok=True)
                extract(part, path, target, FIXTURE_LIMIT)
                result['fixtures'].append({'source': f'{part}:{path}', 'dest': f'/{relative}/{name}',
                                           'bytes': target.stat().st_size})
            elif kind not in (1, 2):
                log(f'Skipped {part}:{path}: entry type {kind}')

    place('system_a', '/system/etc/vintf', 'system/etc/vintf', lambda n: True, 2)
    place('system_a', '/system/etc/selinux', 'system/etc/selinux', lambda n: n.endswith('service_contexts'), 0)
    place('vendor_a', '/etc/vintf', 'vendor/etc/vintf', lambda n: True, 2)
    place('vendor_a', '/etc/selinux', 'vendor/etc/selinux', lambda n: n.endswith('service_contexts'), 0)
    if not any(f['dest'].endswith('plat_service_contexts') for f in result['fixtures']):
        raise ValueError('plat_service_contexts was not extracted')
    for item in result['fixtures']:
        log(f"FIXTURE {item['source']} -> {item['dest']} ({item['bytes']} bytes)")

    # Fedora's libselinux (used by runcon inside the root) treats SELinux as enabled
    # only when /etc/selinux/config exists; the first run's trace showed it checking.
    config = root / 'etc' / 'selinux' / 'config'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(Path('/etc/selinux/config').read_text() if Path('/etc/selinux/config').is_file()
                      else 'SELINUX=enforcing\nSELINUXTYPE=targeted\n')
    log('Copied the host /etc/selinux/config text into the private root (existence check only).')
    # Mount points and symlinks for the Fedora tools used inside the root.
    for relative in ('usr', 'dev', 'proc', 'sys/fs/selinux', str(runtime.relative_to('/'))):
        (root / relative).mkdir(parents=True, exist_ok=True)
    for name, target in (('lib', 'usr/lib'), ('lib64', 'usr/lib64'), ('bin', 'usr/bin'), ('sbin', 'usr/sbin')):
        (root / name).symlink_to(target)
    result['runtime'] = str(runtime)
    result['root'] = str(root)
    log('Prepared servicemanager, libraries and fixtures; no Android binary executed during extraction.')
    return result
