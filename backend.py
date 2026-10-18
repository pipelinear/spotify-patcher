#!/usr/bin/env python3
"""Local build worker. Emits JSON lines for the native Mac interface."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
import plistlib
from chroma import patch_chroma, latest_chroma

REPO = 'https://github.com/My-Name-Is-Jeff/vitrine.git'
HOME_DIR = Path.home()
# Theos rejects spaces in its project/toolchain paths, including "Application Support".
STATE = HOME_DIR / 'Library/Caches/VitrinePatcher'
OUTPUT = HOME_DIR / 'Downloads/Spotify Patcher'
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
ENV = dict(os.environ)
ENV['PATH'] = ':'.join([str(STATE / 'bin'), '/opt/homebrew/bin', '/usr/local/bin', str(HOME_DIR / '.local/bin'), '/usr/bin', '/bin', '/usr/sbin', '/sbin'])
ENV.update(HOMEBREW_NO_AUTO_UPDATE='1', HOMEBREW_NO_INSTALL_CLEANUP='1', GIT_TERMINAL_PROMPT='0', PYTHONUNBUFFERED='1')
LOG = None


def emit(kind, **data):
    record = dict(type=kind, **data)
    print(json.dumps(record), flush=True)
    if LOG:
        LOG.write(json.dumps(record) + '\n')
        LOG.flush()


def run(args, cwd=None, capture=False):
    p = subprocess.Popen([str(a) for a in args], cwd=cwd, env=ENV,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    lines = []
    for line in p.stdout:
        line = ANSI.sub('', line).rstrip()
        lines.append(line)
        if not capture:
            emit('log', message=line)
            if line.startswith('==> '):
                stage = line[4:]
                for prefix, title in [
                    ('Vitrine ', 'Preparing the Spotify build…'),
                    ('extracting the flag', 'Reading Spotify’s configuration (first build takes longer)…'),
                    ('building tweak', 'Compiling Vitrine…'),
                    ('building SpotifyGlassLiveActivity', 'Building the Live Activity extension…'),
                    ('building the App Group', 'Preparing app and widget integration…'),
                    ('injecting', 'Patching and packaging your IPA…'),
                    ('loading the App Group', 'Updating the Spotify widget…'),
                    ('adding ', 'Finishing icons and Live Activity support…'),
                    ('done:', 'Build finished. Checking the result…'),
                ]:
                    if stage.startswith(prefix):
                        emit('status', message=title)
                        break
    if p.wait():
        raise RuntimeError(f'{Path(str(args[0])).name} failed (exit {p.returncode}).\n' + '\n'.join(lines[-10:]))
    return '\n'.join(lines).strip()


def inspect_ipa(path):
    if not path.is_file() or path.suffix.lower() != '.ipa':
        raise ValueError('Choose a downloaded .ipa file, not a folder or a web link.')
    with zipfile.ZipFile(path) as z:
        for entry in z.infolist():
            p = PurePosixPath(entry.filename)
            if p.is_absolute() or '..' in p.parts:
                raise ValueError('The IPA contains an unsafe archive path.')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('This IPA contains symbolic links. Use a standard decrypted Spotify IPA.')
        infos = [n for n in z.namelist() if re.fullmatch(r'Payload/[^/]+\.app/Info.plist', n)]
        if len(infos) != 1:
            raise ValueError('This file does not contain a single iPhone application.')
        info = plistlib.loads(z.read(infos[0]))
        if info.get('CFBundleIdentifier') != 'com.spotify.client':
            raise ValueError('This is not the original Spotify IPA (com.spotify.client).')
        executable = info.get('CFBundleExecutable', '')
        if not executable or '/' in executable or executable in ('.', '..'):
            raise ValueError('The IPA has an invalid executable name.')
        app = infos[0].rsplit('/', 1)[0] + '/'
        binary = z.read(app + executable)
        if len(binary) < 32 or struct.unpack_from('<I', binary)[0] != 0xfeedfacf:
            raise ValueError('Expected a decrypted arm64 Spotify executable.')
        count = struct.unpack_from('<I', binary, 16)[0]
        offset = 32
        encrypted = None
        loads = []
        for _ in range(count):
            if offset + 8 > len(binary):
                raise ValueError('Damaged executable load commands.')
            cmd, size = struct.unpack_from('<II', binary, offset)
            if size < 8 or offset + size > len(binary):
                raise ValueError('Damaged executable load commands.')
            if cmd in (0x21, 0x2c):
                if size < 20:
                    raise ValueError('Damaged encryption command.')
                encrypted = struct.unpack_from('<I', binary, offset + 16)[0]
            if cmd in (0xc, 0x80000018, 0x8000001f) and size >= 24:
                start = offset + struct.unpack_from('<I', binary, offset + 8)[0]
                loads.append(binary[start:offset + size].split(b'\0', 1)[0].decode('utf-8', 'replace'))
            offset += size
        if encrypted != 0:
            raise ValueError('Spotify is still encrypted, or its decryption cannot be verified. Download a decrypted IPA first.')
        version = info.get('CFBundleShortVersionString', '')
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', version):
            raise ValueError('The IPA has an invalid Spotify version.')
        return version, app, info, loads


def check_version(flavor='vitrine'):
    if flavor == 'chroma':
        release = latest_chroma()
        emit('version', flavor=flavor, version=release['version'], commit=release['tag'], message='Official stable release')
        return
    request = urllib.request.Request('https://api.github.com/repos/My-Name-Is-Jeff/vitrine/commits/main', headers={'User-Agent': 'Vitrine-Patcher'})
    with urllib.request.urlopen(request, timeout=25) as response:
        sha = json.load(response)['sha']
    with urllib.request.urlopen(f'https://raw.githubusercontent.com/My-Name-Is-Jeff/vitrine/{sha}/version.txt', timeout=25) as response:
        version = response.read().decode().strip()
    emit('version', flavor=flavor, version=version, commit=sha, message='Latest on GitHub')


def prepare_tools():
    emit('status', message='Checking build tools…')
    sdk = run(['/usr/bin/xcrun', '--sdk', 'iphoneos', '--show-sdk-version'], capture=True)
    if int(sdk.split('.')[0]) < 26:
        raise RuntimeError('Xcode with an iOS 26 or newer SDK is required. Select it in Xcode Settings → Locations.')
    missing = [formula for cmd, formula in [('gmake', 'make'), ('ldid', 'ldid'), ('dpkg-deb', 'dpkg'), ('uv', 'uv')] if not shutil.which(cmd, path=ENV['PATH'])]
    if missing:
        brew = shutil.which('brew', path=ENV['PATH'])
        if not brew:
            raise RuntimeError('Install Homebrew from brew.sh, then reopen this app. Xcode is also required.')
        emit('status', message='Installing missing build tools…')
        run([brew, 'install', *missing])
    config_path = Path(__file__).with_name('config.json')
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    candidate = Path(config.get('existing_theos', '/nonexistent'))
    theos = candidate if (candidate / 'makefiles/common.mk').exists() else STATE / 'theos'
    if not (theos / 'makefiles/common.mk').exists():
        emit('status', message='Downloading Theos (first build only)…')
        # A cancelled clone may leave a directory behind; git can resume its submodules.
        if not (theos / '.git').exists():
            run(['git', 'clone', '--recursive', 'https://github.com/theos/theos.git', theos])
    run(['git', '-C', theos, 'submodule', 'update', '--init', '--recursive'])
    ENV['THEOS'] = str(theos)
    cyan = Path(config.get('existing_cyan', '/nonexistent'))
    if cyan.is_file():
        ENV['PATH'] = str(cyan.parent) + ':' + ENV['PATH']
    if not shutil.which('cyan', path=ENV['PATH']):
        emit('status', message='Installing the IPA patcher…')
        ENV.update(UV_TOOL_DIR=str(STATE / 'uv-tools'), UV_TOOL_BIN_DIR=str(STATE / 'bin'), UV_CACHE_DIR=str(STATE / 'uv-cache'))
        run(['uv', 'tool', 'install', 'cyan @ git+https://github.com/asdfzxcvbn/pyzule-rw'])


def build(ipa, flavor='vitrine'):
    global LOG
    STATE.mkdir(parents=True, exist_ok=True)
    lock = (STATE / 'build.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another Spotify patch is already running. Wait for it to finish.')
    LOG = (STATE / 'last-build.jsonl').open('w')
    emit('status', message='Checking your Spotify IPA…')
    spotify, _, _, loads = inspect_ipa(ipa)
    if any('spotifyglass.dylib' in name for name in loads):
        raise ValueError('This IPA already contains Vitrine or Chroma. Drop the original decrypted Spotify IPA instead.')
    emit('log', message=f'Validated decrypted Spotify {spotify}. Your original IPA will stay unchanged.')
    brand = 'Chroma' if flavor == 'chroma' else 'Vitrine'
    if flavor == 'vitrine':
        prepare_tools()
        emit('status', message='Downloading the latest Vitrine from GitHub…')
        source = STATE / 'source'
        if not (source / '.git').is_dir():
            run(['git', 'clone', '--depth', '1', REPO, source])
        else:
            run(['git', '-C', source, 'fetch', '--depth', '1', 'origin', 'main'])
            run(['git', '-C', source, 'checkout', '--detach', 'FETCH_HEAD'])
        version = (source / 'version.txt').read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9._-]+', version):
            raise RuntimeError('GitHub returned an invalid Vitrine version.')
        commit = run(['git', '-C', source, 'rev-parse', 'HEAD'], capture=True)
        emit('version', flavor=flavor, version=version, commit=commit, message='Building this GitHub version')
        emit('status', message=f'Building Vitrine {version} for Spotify {spotify}…')
    # Use a temporary build output; only a fully verified IPA is published to Downloads.
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='build-', dir=STATE) as work:
        built = Path(work) / 'patched.ipa'
        if flavor == 'chroma':
            version, commit = patch_chroma(ipa, built, Path(work), emit, inspect_ipa)
        else:
            run(['/bin/bash', source / 'scripts/pipeline.sh', ipa, '--no-flex', '-o', built], cwd=source)
        emit('status', message='Verifying the patched IPA…')
        built_version, app, info, dylibs = inspect_ipa(built)
        with zipfile.ZipFile(built) as z:
            bad = z.testzip()
            if bad:
                raise RuntimeError(f'Archive integrity check failed: {bad}')
            required = ['Frameworks/spotifyglass.dylib', 'Frameworks/SpotifyGlassAppGroups.dylib', 'PlugIns/SpotifyGlassLiveActivity.appex/Info.plist']
            if any(app + name not in z.namelist() for name in required):
                raise RuntimeError(f'The built IPA is missing a required {brand} component.')
        if built_version != spotify or not any('spotifyglass.dylib' in name for name in dylibs) or not info.get('NSSupportsLiveActivities'):
            raise RuntimeError('The built IPA failed verification of its injected code or Live Activity support.')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        dest = OUTPUT / f'{brand}-{version}-Spotify-{spotify}-{stamp}.ipa'
        # Exclusive creation means even an unlikely filename collision cannot overwrite a build.
        with tempfile.NamedTemporaryFile(prefix='.patching-', dir=OUTPUT) as staging:
            with built.open('rb') as src:
                shutil.copyfileobj(src, staging)
            staging.flush()
            os.fsync(staging.fileno())
            os.link(staging.name, dest)  # Atomically publish without replacing an existing file.
        sha = hashlib.sha256(dest.read_bytes()).hexdigest()
        dest.with_suffix('.ipa.sha256').write_text(f'{sha}  {dest.name}\n')
        dest.with_suffix('.build.json').write_text(json.dumps(dict(flavor=flavor, version=version, spotify=spotify, commit=commit, sha256=sha), indent=2) + '\n')
    emit('done', path=str(dest), flavor=flavor, version=version, spotify=spotify, message='Ready to sign and install')


def main():
    try:
        os.setsid()  # Cancel stops this worker and the compiler/patcher children together.
    except PermissionError:
        pass
    def cancelled(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check', 'build', 'validate'])
    parser.add_argument('ipa', nargs='?')
    parser.add_argument('--flavor', choices=['vitrine', 'chroma'], default='vitrine')
    args = parser.parse_args()
    try:
        if args.action == 'check':
            check_version(args.flavor)
        elif args.action == 'validate':
            version, _, _, _ = inspect_ipa(Path(args.ipa).resolve())
            emit('validated', version=version, name=Path(args.ipa).name, size=Path(args.ipa).stat().st_size)
        else:
            build(Path(args.ipa).resolve(), args.flavor)
    except KeyboardInterrupt:
        emit('cancelled', message='Build cancelled.')
        sys.exit(130)
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(1)


if __name__ == '__main__':
    main()
