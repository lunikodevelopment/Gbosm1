#!/usr/bin/env python3
"""Graphics stage 2d: run the stage 2c Honeykrisp library under the image's Bionic linker.

Builds asahi/probe-honeykrisp-bionic.c as an Android executable with the stage 2c toolchain,
gathers the library's DT_NEEDED closure from the image's system_a (reusing the completed
bionic-probe runtime for linker64/libc/libm/libdl), and runs one GPU buffer fill and readback
as the ordinary user. No Android loader, property service, Binder or display is involved.
"""
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

LIMIT = 64 << 20


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def latest(pattern, test):
    for path in sorted(Path('work/asahi').glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
        if test(path):
            return path
    return None


def completed(summary):
    try:
        return json.loads(summary.read_text()).get('exit_status') == 0
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if os.geteuid() == 0:
        parser.error('run as your ordinary user, without sudo')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        parser.error('requires the Fedora Asahi ARM64 target')
    if not shutil.which('dump.erofs') or not shutil.which('clang'):
        parser.error('dump.erofs and clang are required')
    bionic = latest('bionic-probe-*/summary.json', completed)
    hk = latest('honeykrisp-android-*', lambda p: (p / 'vulkan.asahi.so').is_file())
    audit = latest('graphics-audit-*/summary.json', completed)
    if not (bionic and hk and audit):
        parser.error('needs completed bionic-probe, graphics-audit and stage 2c honeykrisp-android results')
    bionic = bionic.parent
    drm = sorted(hk.glob('build/subprojects/libdrm-*/libdrm.so'))
    if not drm:
        parser.error(f'no libdrm.so from the stage 2c build in {hk}')
    image = Path(json.loads(audit.read_text())['image']).resolve()
    helper = module('recovery', 'audit-recovery.py')
    _, parts = helper.partitions(image)
    extents = parts['system_a']['extents']
    if len(extents) != 1 or 'offset' not in extents[0]:
        parser.error('system_a must have a single linear extent')
    offset = extents[0]['offset']

    print('THIS STEP MODIFIES STORAGE', flush=True)
    print('Creates one new work/asahi/honeykrisp-bionic-* directory (probe, copied libraries, logs).', flush=True)
    print('Earlier results and the image are only read. Delete the new directory to reverse.', flush=True)
    out = Path(tempfile.mkdtemp(prefix='honeykrisp-bionic-', dir='work/asahi')).resolve()
    report = (out / 'report.txt').open('w')
    result = {'image': str(image), 'bionic_runtime': str(bionic), 'honeykrisp_build': str(hk),
              'libraries': [], 'issues': [], 'execution': None,
              'limitations': ['The probe dlopens the driver directly; Android libvulkan, its HAL loading and '
                              'properties are not exercised.',
                              'One buffer fill and readback only; no images, AHardwareBuffer, ANGLE or display.']}

    def log(text):
        print(text, flush=True)
        print(text, file=report, flush=True)

    code = 1
    try:
        log(f'Results: {out}')
        log(f'Bionic runtime: {bionic}')
        log(f'Honeykrisp build: {hk}')
        libs = out / 'lib64'
        shutil.copytree(bionic / 'runtime' / 'lib64', libs)
        linker = out / 'linker64'
        shutil.copy2(bionic / 'runtime' / 'linker64', linker)
        shutil.copy2(hk / 'vulkan.asahi.so', libs / 'vulkan.asahi.so')
        shutil.copy2(drm[0], libs / 'libdrm.so')
        log(f'libdrm.so from {drm[0]}')

        probe = out / 'probe-honeykrisp-bionic'
        build = ['clang', '--target=aarch64-linux-android35', f'--sysroot={hk / "ndk" / "sysroot"}',
                 f'-resource-dir={hk / "clang-resource"}', '-fuse-ld=lld', '-Wl,-z,max-page-size=16384',
                 '-O2', '-Wall', '-Wextra', str(Path(__file__).with_name('probe-honeykrisp-bionic.c')),
                 '-o', str(probe), '-ldl']
        built = subprocess.run(build, capture_output=True, text=True, timeout=300)
        log(f'build exit status: {built.returncode}\n{built.stdout}{built.stderr}')
        if built.returncode:
            raise ValueError('probe build failed')

        def runtime_apex(member):
            """Read one file from the image's com.android.runtime APEX payload (EROFS or ext4)."""
            graphics = module('graphics', 'audit-graphics.py')
            with tempfile.TemporaryDirectory(prefix='apex-scratch-', dir=out) as scratch:
                package, payload = Path(scratch) / 'runtime.apex', Path(scratch) / 'payload.img'
                r = subprocess.run(['dump.erofs', f'--offset={offset}', '--path=/system/apex/com.android.runtime.apex',
                                    '--cat', str(image)], capture_output=True, timeout=120)
                if r.returncode or len(r.stdout) > (512 << 20):
                    raise ValueError('could not read the runtime APEX')
                package.write_bytes(r.stdout)
                graphics.extract_payload(package, payload)
                with payload.open('rb') as f:
                    f.seek(1024)
                    sb = f.read(1024)
                if sb[:4] == bytes.fromhex('e2e1f5e0'):
                    command = ['dump.erofs', f'--path={member}', '--cat', str(payload)]
                elif sb[56:58] == bytes.fromhex('53ef') and shutil.which('debugfs'):
                    command = ['debugfs', '-R', 'cat ' + member, str(payload)]
                else:
                    raise ValueError('runtime APEX payload requires EROFS or ext4 with debugfs')
                r = subprocess.run(command, capture_output=True, timeout=120)
                if r.returncode or not r.stdout.startswith(b'\x7fELF') or len(r.stdout) > LIMIT:
                    raise ValueError(f'could not read runtime APEX:{member}')
                return r.stdout

        present = {p.name for p in libs.iterdir()} | {'ld-android.so'}
        pending = set()
        for path in (probe, libs / 'vulkan.asahi.so', libs / 'libdrm.so'):
            info = helper.elf_info(path.read_bytes())
            log(f'ELF {path.name}: aligned_16k={info["aligned_16k"]} DT_NEEDED=' + ', '.join(info['needed']))
            if info['machine'] != 183 or not info['aligned_16k']:
                raise ValueError(f'{path.name}: not ARM64 with 16 KiB load alignment')
            pending.update(info['needed'])
        while pending - present:
            name = sorted(pending - present)[0]
            if not re.fullmatch(r'[A-Za-z0-9_.@+-]+', name) or len(present) > 96:
                raise ValueError(f'unsupported dependency or closure too large: {name!r}')
            r = subprocess.run(['dump.erofs', f'--offset={offset}', f'--path=/system/lib64/{name}', '--cat',
                                str(image)], capture_output=True, timeout=120)
            if r.returncode or not r.stdout or len(r.stdout) > LIMIT:
                raise ValueError(f'could not read system_a:/system/lib64/{name}')
            data, origin = r.stdout, f'system_a:/system/lib64/{name}'
            if not data.startswith(b'\x7fELF'):
                # dump.erofs --cat of a symlink prints its target; Bionic libraries point into the runtime APEX.
                target = data.decode(errors='replace').strip()
                log(f'SYMLINK {origin} -> {target}')
                if not re.fullmatch(r'/apex/com\.android\.runtime/lib64/bionic/[A-Za-z0-9_.+-]+', target):
                    raise ValueError(f'{origin} is a symlink outside the runtime APEX Bionic directory: {target}')
                data, origin = runtime_apex(target.split('/apex/com.android.runtime', 1)[1]), \
                    'runtime APEX:' + target.split('/apex/com.android.runtime', 1)[1]
            (libs / name).write_bytes(data)
            info = helper.elf_info(data)
            log(f'ELF {origin}: aligned_16k={info["aligned_16k"]} DT_NEEDED=' + ', '.join(info['needed']))
            result['libraries'].append({'name': name, 'needed': info['needed'], 'aligned_16k': info['aligned_16k']})
            present.add(name)
            pending.update(info['needed'])

        linker.chmod(0o700)
        probe.chmod(0o700)
        command = [str(linker), str(probe), str(libs / 'vulkan.asahi.so')]
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LD_LIBRARY_PATH': str(libs)}
        log('Executing only: image linker64 + Android probe + stage 2c Honeykrisp')
        run = subprocess.run(command, cwd=out, env=environment, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=60)
        (out / 'probe.stdout').write_text(run.stdout)
        (out / 'probe.stderr').write_text(run.stderr)
        result['execution'] = {'command': command, 'returncode': run.returncode}
        log(f'probe exit status: {run.returncode}\n{run.stdout}{run.stderr}')
        if run.returncode == 0 and 'HK_BIONIC readback=PASS' in run.stdout:
            code = 0
            log('HONEYKRISP_BIONIC=PASS: Android-built Honeykrisp ran a GPU readback under Bionic on Fedora Asahi.')
        else:
            raise ValueError('probe did not pass; the output above is the result')
    except Exception as e:  # report every stop as a result
        result['issues'].append(f'{type(e).__name__}: {e}')
        log(f'STOPPED: {type(e).__name__}: {e}')
    finally:
        result['exit_status'] = code
        for limitation in result['limitations']:
            log('LIMITATION: ' + limitation)
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        log(f'Send {out / "report.txt"}')
        report.close()
    return code


if __name__ == '__main__':
    sys.exit(main())
