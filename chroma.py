"""Apply Chroma's official release-kit manifest locally, preserving its shipped code."""
import copy
import hashlib
import json
import plistlib
import re
import shutil
import struct
import urllib.request
import zipfile
from pathlib import PurePosixPath
from xml.sax.saxutils import escape


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Spotify-Patcher/2.0', 'Accept': 'application/vnd.github+json' if 'api.github.com' in url else '*/*'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def latest_chroma():
    release = json.loads(fetch('https://api.github.com/repos/skopevoj/spoti.pw/releases/latest'))
    tag = release['tag_name']
    if not re.fullmatch(r'v?[0-9][A-Za-z0-9._-]*', tag):
        raise ValueError('The Chroma release has an invalid version.')
    asset = next((a for a in release['assets'] if a['name'].endswith('-kit.zip')), None)
    if not asset:
        raise ValueError('The current Chroma release has no official patch kit. Try again after the release finishes.')
    url = asset['browser_download_url']
    if not url.startswith('https://github.com/skopevoj/spoti.pw/releases/download/'):
        raise ValueError('Unexpected Chroma kit download location.')
    return dict(version=tag.removeprefix('v'), tag=tag, url=url, digest=asset.get('digest'))


def load_commands(data):
    if len(data) < 32 or struct.unpack_from('<I', data)[0] != 0xfeedfacf:
        raise ValueError('The IPA must contain arm64 Mach-O binaries.')
    count, size = struct.unpack_from('<II', data, 16)
    pos = 32
    commands = []
    for _ in range(count):
        cmd, length = struct.unpack_from('<II', data, pos)
        if length < 8 or pos + length > len(data):
            raise ValueError('Invalid Mach-O command.')
        commands.append((pos, cmd, length))
        pos += length
    if pos != 32 + size:
        raise ValueError('Invalid Mach-O header length.')
    return commands


def inject(data, dylibs, rpath=None):
    binary = bytearray(data)
    existing = set()
    first_section = len(binary)
    for pos, cmd, length in load_commands(binary):
        if cmd in (0xc, 0x80000018, 0x8000001f, 0x80000023, 0x8000001c):
            start = pos + struct.unpack_from('<I', binary, pos + 8)[0]
            existing.add(bytes(binary[start:pos + length]).split(b'\0', 1)[0].decode())
        if cmd == 0x19:
            sections = struct.unpack_from('<I', binary, pos + 64)[0]
            for index in range(sections):
                offset = struct.unpack_from('<I', binary, pos + 72 + 80 * index + 48)[0]
                if offset:
                    first_section = min(first_section, offset)
    count, total = struct.unpack_from('<II', binary, 16)
    requested = ([(0x8000001c, 12, rpath)] if rpath else []) + [(0x80000018, 24, lib) for lib in dylibs]
    for cmd, header_size, name in requested:
        if name in existing:
            continue
        encoded = name.encode() + b'\0'
        length = (header_size + len(encoded) + 7) & ~7
        end = 32 + total
        if end + length > first_section or any(binary[end:end + length]):
            raise ValueError('This IPA has no space for the patch. Try a fresh decrypted Spotify IPA.')
        command = bytearray(length)
        struct.pack_into('<III', command, 0, cmd, length, header_size)
        command[header_size:header_size + len(encoded)] = encoded
        binary[end:end + length] = command
        total += length
        count += 1
        existing.add(name)
    struct.pack_into('<II', binary, 16, count, total)
    return binary


def merge_intents(original, extra):
    merged = dict(original)
    for key in ('actions', 'entities', 'queries'):
        merged[key] = {**original.get(key, {}), **extra.get(key, {})}
    for key, identity in [('enums', 'identifier'), ('autoShortcuts', 'actionIdentifier')]:
        newer = extra.get(key, [])
        ids = {item.get(identity) for item in newer}
        merged[key] = [x for x in original.get(key, []) if x.get(identity) not in ids] + newer
    for key in ('negativePhrases', 'assistantEntities', 'assistantIntents', 'assistantIntentNegativePhrases'):
        merged[key] = list(original.get(key, []))
        for item in extra.get(key, []):
            if item not in merged[key]:
                merged[key].append(item)
    if extra.get('autoShortcutProviderMangledName'):
        merged['autoShortcutProviderMangledName'] = extra['autoShortcutProviderMangledName']
    return merged


def patch_chroma(ipa, output, work, emit, inspect):
    emit('status', message='Downloading the official Chroma patch kit…')
    release = latest_chroma()
    raw = fetch(release['url'])
    if release['digest'] and release['digest'] != 'sha256:' + hashlib.sha256(raw).hexdigest():
        raise ValueError('Chroma’s download checksum did not match GitHub. Please retry.')
    kit_path = work / 'chroma-kit.zip'
    kit_path.write_bytes(raw)
    spotify, app, info, _ = inspect(ipa)
    with zipfile.ZipFile(kit_path) as kit, zipfile.ZipFile(ipa) as src:
        names = kit.namelist()
        if len(names) != len(set(names)) or any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts for n in names):
            raise ValueError('Invalid Chroma kit paths.')
        manifest = json.loads(kit.read('kit.json'))
        if manifest.get('format') != 1:
            raise ValueError('This Chroma kit needs a newer Spotify Patcher.')
        version = manifest['version']
        if version != release['version']:
            raise ValueError('The kit version does not match its release.')
        supported = manifest.get('spotifyVersions', [manifest['spotify']])
        if spotify not in supported:
            raise ValueError(f'Chroma {version} supports Spotify {", ".join(supported)}. Your IPA is {spotify}.')
        assets = {n for n in names if n.startswith(('files/', 'appintents/')) and not n.endswith('/')}
        checksums = manifest.get('integrity', {})
        if assets != set(checksums):
            raise ValueError('Chroma’s kit has an incomplete integrity manifest.')
        for name in assets:
            if hashlib.sha256(kit.read(name)).hexdigest() != checksums[name]:
                raise ValueError('Chroma’s kit failed its integrity check.')
        emit('version', flavor='chroma', version=version, commit=release['tag'], message='Official stable release')
        emit('status', message=f'Patching Spotify with Chroma {version}…')
        overlay = manifest['infoPlist']
        info.update(overlay.get('set', {}))
        for key, values in overlay.get('union', {}).items():
            info[key] = list(dict.fromkeys(info.get(key, []) + values))
        for key, value in overlay.get('default', {}).items():
            if not info.get(key):
                info[key] = value
        removals = [app + p for p in manifest.get('remove', [])]
        additions = {app + n[6:]: n for n in assets if n.startswith('files/')}
        loads = {app + (info['CFBundleExecutable'] if entry['binary'] == '@main' else entry['binary']): entry for entry in manifest['load']}
        for name, entry in loads.items():
            if not entry.get('optional') and name not in src.namelist():
                raise ValueError('A required Spotify executable is missing.')
        intents_key = app + 'Metadata.appintents/extract.actionsdata'
        intent_folder = manifest.get('appIntents')
        extra_intents = json.loads(kit.read(intent_folder + '/extract.actionsdata')) if intent_folder else None
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as dest:
            for entry in src.infolist():
                name = entry.filename
                if not name.startswith('Payload/') or any(p.startswith('.') for p in PurePosixPath(name).parts) or name in additions or any(name == p or name.startswith(p + '/') for p in removals):
                    continue
                entry = copy.copy(entry)
                entry.compress_type = zipfile.ZIP_DEFLATED
                if name == app + 'Info.plist':
                    dest.writestr(entry, plistlib.dumps(info))
                elif name in loads:
                    change = loads[name]
                    dest.writestr(entry, inject(src.read(name), change['dylibs'], manifest.get('rpath') if change['binary'] == '@main' else None))
                elif name == intents_key and extra_intents:
                    dest.writestr(entry, json.dumps(merge_intents(json.loads(src.read(name)), extra_intents)).encode())
                else:
                    with src.open(entry.filename) as reader, dest.open(entry, 'w') as writer:
                        shutil.copyfileobj(reader, writer)
            emit('status', message='Adding Chroma’s artwork and Live Activity support…')
            replacements = {'HOST_BUNDLE_ID': info['CFBundleIdentifier'], 'HOST_SHORT_VERSION': spotify, 'HOST_VERSION': info['CFBundleVersion']}
            for name, original in additions.items():
                data = kit.read(original)
                if original[6:] in manifest.get('templates', []):
                    template = data.decode()
                    for token, value in replacements.items():
                        template = template.replace(token, escape(str(value)))
                    data = template.encode()
                entry = copy.copy(kit.getinfo(original))
                entry.filename = name
                dest.writestr(entry, data)
            if extra_intents and intents_key not in src.namelist():
                dest.writestr(intents_key, json.dumps(extra_intents).encode())
                dest.writestr(app + 'Metadata.appintents/version.json', kit.read(intent_folder + '/version.json'))
    # Libraries are copied byte-for-byte from the publisher; paid feature gates stay as shipped.
    with zipfile.ZipFile(output) as final:
        for name in ('files/Frameworks/spotifyglass.dylib', 'files/Frameworks/SpotifyGlassAppGroups.dylib'):
            if hashlib.sha256(final.read(app + name[6:])).hexdigest() != checksums[name]:
                raise ValueError('The packaged Chroma library failed verification.')
    return version, release['tag']
