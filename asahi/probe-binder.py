#!/usr/bin/env python3
"""Build and run a scoped Binder probe in temporary user/mount/PID namespaces."""
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--android-service', action='store_true',
                        help='run the image service list client against a native empty-list stub')
    mode.add_argument('--object-fd', action='store_true',
                      help='verify native Binder object calls and a sealed memfd round trip')
    mode.add_argument('--android-parcel', action='store_true',
                      help='run image service call against a controlled object/FD Parcel endpoint')
    mode.add_argument('--real-servicemanager', action='store_true',
                      help="start only the image's own servicemanager in a private root and record what happens")
    parser.add_argument('--list-client', action='store_true',
                        help="with --real-servicemanager: also run the image's own `service list` against it")
    args = parser.parse_args()
    if args.list_client and not args.real_servicemanager:
        parser.error('--list-client requires --real-servicemanager')
    sm_mode = args.real_servicemanager
    android_mode = args.android_service or args.android_parcel
    if not args.temporary_policy:
        parser.error('this revision requires --temporary-policy; see NEXT_STEPS.md for scope and reversal')
    if os.geteuid() == 0:
        sys.exit('Run as your ordinary user, without sudo.')
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64') or os.sysconf('SC_PAGE_SIZE') != 16384:
        sys.exit('Requires the Fedora Asahi ARM64/16 KiB target.')
    required = ['cc', 'unshare', 'runcon', 'sudo', 'semodule', 'chcon', 'ldd']
    if android_mode or sm_mode:
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
    print('Creates a new work/asahi probe result directory containing a native probe and logs.', flush=True)
    print('Temporarily mounts binderfs only in new user/mount namespaces; namespace exit removes the mount.', flush=True)
    print('Temporarily installs gbos_binder_probe SELinux module and labels only the new probe executable.', flush=True)
    print('Cleanup restores the original executable context and removes only this new policy module.', flush=True)
    print('If interrupted during cleanup: sudo semodule -r gbos_binder_probe; see NEXT_STEPS.md.', flush=True)
    print('Existing files are retained. Delete the new result directory to reverse generated files.', flush=True)
    parent.mkdir(parents=True, exist_ok=True)
    prefix = ('real-servicemanager-' if sm_mode else 'android-parcel-probe-' if args.android_parcel else 'libbinder-probe-' if args.android_service
              else 'binder-transfer-probe-' if args.object_fd else 'binder-probe-')
    out = Path(tempfile.mkdtemp(prefix=prefix, dir=parent)).resolve()
    mountpoint = out / 'binderfs'
    mountpoint.mkdir()
    source_name = ('probe-servicemanager.c' if sm_mode else 'probe-android-parcel.c' if args.android_parcel else 'probe-libbinder.c' if args.android_service
                   else 'probe-binder-transfer.c' if args.object_fd else 'probe-binder.c')
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
        if sm_mode:
            import importlib.util
            spec = importlib.util.spec_from_file_location('sm_extract', Path(__file__).with_name('prepare-servicemanager.py'))
            extraction = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(extraction)
            result['android_runtime'] = extraction.prepare(out, log)
            binary = Path(result['android_runtime']['runtime']) / 'binder-echo'
            result['limitations'] = ['Only the image servicemanager is started, for six seconds, without a client: no service list, registration or HAL.',
                                    'Fixtures are copied VINTF and service-context files; odm/product/system_ext copies, the property service and logd are absent.',
                                    'An exit, crash or SELinux denial is a result to report, not a defect to hide; exit 0 means the observation completed.']
        if args.object_fd:
            result['limitations'] = ['Native Linux Binder UAPI object/FD prerequisite only; no Android executable is run in this mode.',
                                    'One sealed memfd and one returned Binder object; DMA-BUF, fences, death notifications, full object lifetimes, servicemanager and HALs remain untested.']
        if android_mode:
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
            if args.android_parcel:
                result['android_call'] = {'service': 'gbos.parcel', 'code': 5658,
                                          'arguments': ['i32', '5658', 'fd', '/proc/self/fd/0'],
                                          'input': 'sealed read-only memfd on client stdin only; created after server fork',
                                          'receipt_words': ['47504231', '00004000']}
                result['android_receipt_verified'] = False
                result['limitations'] = ['Image service CLI decodes/calls one returned Binder object and serializes an outgoing FD; native endpoint verifies the file.',
                                        'Android FD receipt/readback, Android local Binder object export, complete lifetimes, DMA-BUF/fences and real servicemanager/HALs remain untested.',
                                        'Readiness property, service lookup metadata and interface are private fixtures for this endpoint, not Android boot or service registration.']
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
        memfd_type = None
        if args.object_fd or args.android_parcel:
            # Inspect an anonymous file created in the same client domain. No
            # disk file is created and no transferred-file execute/write rule
            # is added to the dedicated server domain.
            fd = os.memfd_create('gbos-binder-transfer-context', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            try:
                memfd_context = os.getxattr(fd, 'security.selinux').decode().rstrip('\0')
            finally:
                os.close(fd)
            memfd_type = type_of(memfd_context)
            result['memfd_context'] = memfd_context
            log('Observed client memfd context: ' + memfd_context)
            rules += ['(allow gbos_binder_probe_t unconfined_t (binder (transfer)))',
                      f'(allow gbos_binder_probe_t {memfd_type} (file (read getattr map)))']
            # The template already allows gbos_binder_probe_t unconfined_t
            # fd(use), covering this received file's creator SID.
        if sm_mode:
            # Android runtime and fixture files share the run directory's type; nothing is
            # added for the host. Execute-without-transition is needed to run linker64.
            tree_types = set()
            for base in (Path(result['android_runtime']['runtime']), Path(result['android_runtime']['root'])):
                for path in [base, *base.rglob('*')]:
                    if path.is_symlink():
                        symlink_types.add(type_of(os.getxattr(path, 'security.selinux', follow_symlinks=False).decode().rstrip('\0')))
                    elif path.is_dir():
                        directory_types.add(type_of(file_context(path)))
                    else:
                        tree_types.add(type_of(file_context(path)))
            optional = {}
            for key, probe in (('proc', '/proc'), ('selinuxfs', '/sys/fs/selinux/access'), ('selinuxfs_dir', '/sys/fs/selinux'),
                               ('null', '/dev/null'), ('devdir', '/dev/shm')):
                try:
                    optional[key] = type_of(file_context(probe))
                except OSError:
                    optional[key] = None
            result['sm_policy_types'] = {'tree': sorted(tree_types), **optional}
            rules += [f'(allow gbos_binder_probe_t {t} (file (read open execute execute_no_trans map getattr)))' for t in sorted(tree_types)]
            rules += [f'(allow gbos_binder_probe_t {t} (dir (read open search getattr)))' for t in sorted(directory_types)]
            rules += [f'(allow gbos_binder_probe_t {t} (lnk_file (read getattr)))' for t in sorted(symlink_types)]
            rules += ['(allow gbos_binder_probe_t gbos_binder_probe_t (process (fork sigchld signal sigkill getsched setsched setpgid getattr setrlimit)))',
                      '(allow gbos_binder_probe_t gbos_binder_probe_t (file (read write open getattr map)))',
                      '(allow gbos_binder_probe_t gbos_binder_probe_t (dir (search getattr read open)))',
                      '(allow gbos_binder_probe_t gbos_binder_probe_t (lnk_file (read getattr)))',
                      '(allow gbos_binder_probe_t gbos_binder_probe_t (unix_stream_socket (create getattr setopt getopt read write connect)))',
                      '(allow gbos_binder_probe_t gbos_binder_probe_t (unix_dgram_socket (create getattr setopt getopt read write connect)))']
            if optional['proc']:
                rules += [f'(allow gbos_binder_probe_t {optional["proc"]} (dir (search getattr read open)))',
                          f'(allow gbos_binder_probe_t {optional["proc"]} (file (read open getattr)))']
            if optional['selinuxfs']:
                rules += [f'(allow gbos_binder_probe_t {optional["selinuxfs"]} (file (read write open getattr map)))',
                          f'(allow gbos_binder_probe_t {optional["selinuxfs"]} (security (compute_av)))']
            if optional['selinuxfs_dir']:
                rules += [f'(allow gbos_binder_probe_t {optional["selinuxfs_dir"]} (dir (search getattr)))']
            if optional['null']:
                rules += [f'(allow gbos_binder_probe_t {optional["null"]} (chr_file (open read write getattr ioctl)))']
            # Denials from run 4 (work/asahi/real-servicemanager-wsakyux_): search on the private
            # tmpfs /dev root (labelled like an anonymous memfd, user_tmp_t) and getattr on the
            # root filesystem (fs_t) for the linker's fstatfs of libbase.so.
            fd = os.memfd_create('gbos-sm-tmpfs-context', os.MFD_CLOEXEC)
            try:
                tmpfs_type = type_of(os.getxattr(fd, 'security.selinux').decode().rstrip('\0'))
            finally:
                os.close(fd)
            result['sm_policy_types']['private_dev_tmpfs'] = tmpfs_type
            rules += [f'(allow gbos_binder_probe_t {tmpfs_type} (dir (search getattr)))',
                      '(allow gbos_binder_probe_t fs_t (filesystem (getattr)))']
            if optional['devdir']:
                rules += [f'(allow gbos_binder_probe_t {optional["devdir"]} (dir (search getattr)))']
        policy = out / 'gbos_binder_probe.cil'
        policy.write_text(template + '\n'.join(rules) + '\n')
        result['policy'] = {'device_type': device_type, 'library_types': sorted(library_types),
                            'directory_types': sorted(directory_types), 'log_types': sorted(log_types),
                            'symlink_types': sorted(symlink_types)}
        if args.object_fd or args.android_parcel:
            result['policy']['memfd_type'] = memfd_type
            result['policy']['object_transfer'] = 'gbos_binder_probe_t -> unconfined_t only'
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
        if android_mode:
            command.append(result['android_runtime']['runtime'])
        if sm_mode:
            command.extend([result['android_runtime']['runtime'], result['android_runtime']['root']])
            if args.list_client:
                command.append('--list-client')
        result['execution'] = run(command, 'servicemanager-run' if sm_mode else 'parcel-call' if args.android_parcel else 'service-list' if args.android_service
                                  else 'transfer' if args.object_fd else 'echo', 40)
        marker = ('SM_LAUNCHER=DONE' if sm_mode else 'ANDROID_PARCEL=PASS' if args.android_parcel else 'LIBBINDER_LIST=PASS' if args.android_service
                  else 'BINDER_TRANSFER=PASS' if args.object_fd else 'BINDER_ECHO=PASS')
        output = result['execution']['stdout']
        if result['execution']['returncode']:
            detail = result['execution']['stderr'].strip() or 'retain stdout and trace diagnostics'
            raise RuntimeError(f'private Binder execution exited {result["execution"]["returncode"]}: {detail}')
        if args.android_parcel:
            actual = re.search(r'^BINDER_MEMFD_CONTEXT=([^\n]+)', output, re.M)
            if not actual or type_of(actual[1]) != memfd_type:
                raise RuntimeError('Android Parcel memfd type differs from the scoped policy type')
            client_path = Path(result['android_runtime']['runtime']) / 'parcel-client.stdout'
            if not client_path.is_file():
                raise RuntimeError('Android Parcel client output unavailable despite successful launcher exit')
            if client_path.stat().st_size > 65536:
                raise RuntimeError('Android Parcel client output exceeds 64 KiB limit')
            client_output = client_path.read_text(errors='replace')
            result['android_client_stdout'] = client_output
            log('ANDROID CLIENT OUTPUT:\n' + client_output)
            # The CLI ignores transact's status; require its actual reply dump
            # to contain the endpoint's receipt, not just a successful CLI exit.
            reply_dump = re.search(r'Result:\s*Parcel\(([^)]*)\)', client_output, re.I)
            # HexDump may render 32-bit words or raw byte groups. Both forms
            # below encode the same two little-endian receipt integers.
            receipt = bool(reply_dump and re.search(
                r'\b47504231\s+00004000\b|\b31\s*42\s*50\s*47\s+00\s*40\s*00\s*00\b',
                reply_dump[1], re.I))
            result['android_receipt_verified'] = receipt
            required_markers = ('STUB_READY_PROPERTY=CREATED', 'PARCEL_INPUT=READY', 'ANDROID_OBJECT_READ=PASS',
                                'ANDROID_FD_WRITE=PASS', 'PARCEL_ENDPOINT_REPLY=PASS', 'ANDROID_PARCEL=PASS')
            missing = [item for item in required_markers if item not in output]
            result['missing_markers'] = missing
            if missing or not receipt:
                raise RuntimeError('Android Parcel markers or printed receipt missing: ' + ', '.join(missing))
        if sm_mode:
            sm_trace = Path(result['android_runtime']['runtime']) / 'sm.trace'
            observed = re.search(r'^SERVICEMANAGER_OBSERVED=([^\n]+)', output, re.M)
            result['servicemanager'] = {'observed': observed[1] if observed else None, 'trace': str(sm_trace),
                                        'context_manager_lines': [], 'hint': 'the traced ioctl return values decide whether it became context manager'}
            if sm_trace.is_file():
                with sm_trace.open(errors='replace') as stream:
                    result['servicemanager']['context_manager_lines'] = [l.rstrip() for l in stream if 'CONTEXT_MGR' in l][:10]
            for item in result['servicemanager']['context_manager_lines']:
                log('TRACE ' + item)
            log('SERVICEMANAGER_OBSERVED=' + str(result['servicemanager']['observed']))
            if args.list_client:
                listing = Path(result['android_runtime']['runtime']) / 'list-client.stdout'
                text = listing.read_text(errors='replace')[:65536] if listing.is_file() else ''
                found = re.search(r'Found (\d+) services?:', text)
                result['servicemanager']['list_client_stdout'] = text
                result['servicemanager']['list_client_found'] = int(found[1]) if found else None
                log('LIST CLIENT OUTPUT:\n' + text)
                log('SERVICE_LIST_FOUND=' + (found[1] if found else 'no "Found N services" line'))
        if args.object_fd:
            actual = re.search(r'^BINDER_MEMFD_CONTEXT=([^\n]+)', output, re.M)
            if not actual or type_of(actual[1]) != memfd_type:
                raise RuntimeError('executed memfd type differs from the scoped policy type')
            required_markers = ('BINDER_FD_SERVER=PASS', 'BINDER_FD_CLIENT=PASS',
                                'BINDER_OBJECT_SERVER=PASS', 'BINDER_OBJECT_CLIENT=PASS',
                                'BINDER_TRANSFER_SERVER=PASS', 'BINDER_TRANSFER=PASS')
            missing = [item for item in required_markers if item not in output]
            result['missing_markers'] = missing
            if missing:
                raise RuntimeError('object/FD probe missing expected evidence: ' + ', '.join(missing))
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
        if android_mode or sm_mode:
            try:
                trace = out / 'android-runtime' / ('sm.trace' if sm_mode else 'client.trace')
                if trace.is_file():
                    result['client_trace'] = str(trace)
                    with trace.open(errors='replace') as stream:
                        tail = ''.join(deque(stream, maxlen=100))
                    (out / 'client-trace-tail.txt').write_text(tail)
                    list_trace = out / 'android-runtime' / 'list-client.trace'
                    if sm_mode and list_trace.is_file():
                        with list_trace.open(errors='replace') as stream:
                            list_tail = ''.join(deque(stream, maxlen=60))
                        (out / 'list-client-trace-tail.txt').write_text(list_tail)
                        log('LIST CLIENT TRACE TAIL:\n' + list_tail)
                    log('CLIENT TRACE TAIL:\n' + tail)
                else:
                    log('Client trace unavailable: execution may have stopped before the Android client started.')
            except BaseException as error:
                result['trace_error'] = str(error)
                result['exit_status'] = 1
                log('Trace collection failed; continuing policy cleanup: ' + str(error))
            if args.android_parcel:
                try:
                    client_path = out / 'android-runtime' / 'parcel-client.stdout'
                    if client_path.is_file() and 'android_client_stdout' not in result:
                        if client_path.stat().st_size > 65536:
                            raise RuntimeError('Android Parcel client output exceeds 64 KiB limit')
                        result['android_client_stdout'] = client_path.read_text(errors='replace')
                        log('ANDROID CLIENT OUTPUT:\n' + result['android_client_stdout'])
                except BaseException as error:
                    result['client_output_error'] = str(error)
                    result['exit_status'] = 1
                    log('Client output collection failed; continuing policy cleanup: ' + str(error))
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
