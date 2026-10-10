"""One-time import of data from ControlIOS PC after the Manager rename."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)
MARKER = '.controlios-pc-import-v1.json'


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _json(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')


def _merge_registry(old, current, settings_defaults, device_defaults):
    if not isinstance(old, dict) or not isinstance(current, dict):
        raise ValueError('Invalid device registry')
    if not isinstance(old.get('devices', []), list) or not isinstance(current.get('devices', []), list):
        raise ValueError('Invalid device list')
    # Existing intentional Manager edits win; defaults/empty fields inherit
    # the previous application's settings, names, groups and notes.
    def fields(legacy, newer, defaults):
        if not isinstance(legacy, dict) or not isinstance(newer, dict):
            raise ValueError('Invalid registry entry')
        result = dict(legacy)
        for key, value in newer.items():
            if key not in result or (value not in ('', None) and value != defaults.get(key)):
                result[key] = value
        return result

    def identity(device):
        return device['host'], device.get('port', 5901)
    existing = {identity(d): d for d in current.get('devices', [])}
    devices = []
    for device in old.get('devices', []):
        newer = existing.pop(identity(device), {})
        defaults = dict(device_defaults, name=device['host'])
        devices.append(fields(device, newer, defaults))
    devices.extend(existing.values())
    return dict(current, settings=fields(old.get('settings', {}), current.get('settings', {}), settings_defaults), devices=devices)


def migrate_user_data(destination: Path, sources: list[Path], settings_defaults: dict,
                      device_defaults: dict) -> dict:
    """Copy missing data and recover registry defaults without repeated imports.

    Never changes sources. Changed target files have byte-for-byte backups.
    Failed imports remain retryable and do not create a completion marker.
    """
    destination = Path(destination)
    marker = destination / 'config' / MARKER
    if marker.exists():
        return {'already_imported': True}
    report = {'copied': 0, 'updated': 0, 'errors': 0}
    seen = set()
    found = False
    registry_imported = False
    for source in sources:
        source = Path(source)
        resolved = source.resolve()
        if resolved == destination.resolve() or resolved in seen:
            continue
        seen.add(resolved)
        if not (source / 'config').is_dir():
            continue
        found = True
        for directory in ('config', 'cookies', 'captures'):
            for path in sorted((source / directory).rglob('*')):
                relative = path.relative_to(source)
                if not path.is_file() or path.is_symlink() or '_media_tmp' in relative.parts:
                    continue
                if path.name.startswith('.') or path.suffix in ('.tmp', '.bak'):
                    continue
                target = destination / relative
                try:
                    data = path.read_bytes()
                    if target.exists():
                        original = target.read_bytes()
                        if relative == Path('config/devices.json'):
                            if registry_imported:
                                continue
                            old = json.loads(data.decode('utf-8-sig'))
                            current = json.loads(original.decode('utf-8-sig'))
                            data = _json(_merge_registry(old, current, settings_defaults, device_defaults))
                            registry_imported = True
                            if json.loads(data) == current:
                                continue
                        elif relative in (Path('config/scripts.json'), Path('config/autoclick_js.json')):
                            old = json.loads(data.decode('utf-8-sig'))
                            current = json.loads(original.decode('utf-8-sig'))
                            if not isinstance(old, dict) or not isinstance(current, dict):
                                raise ValueError('Invalid script library')
                            data = _json(dict(old, **current))
                            if json.loads(data) == current:
                                continue
                        elif relative == Path('config/shopee_accounts.json'):
                            old = json.loads(data.decode('utf-8-sig'))
                            current = json.loads(original.decode('utf-8-sig'))
                            if not isinstance(old, dict) or not isinstance(current, dict):
                                raise ValueError('Invalid Shopee account library')
                            if current.get('accounts') or not old.get('accounts'):
                                continue
                        else:
                            continue
                        digest = hashlib.sha256(original).hexdigest()[:12]
                        backup = destination / 'migration-backups' / relative.parent / (relative.name + '.' + digest + '.bak')
                        if not backup.exists():
                            _write(backup, original)
                        _write(target, data)
                        report['updated'] += 1
                    else:
                        # Validate JSON before importing it into the live app.
                        if path.suffix == '.json':
                            json.loads(data.decode('utf-8-sig'))
                        _write(target, data)
                        report['copied'] += 1
                        if relative == Path('config/devices.json'):
                            registry_imported = True
                except (OSError, ValueError, KeyError, TypeError):
                    report['errors'] += 1
                    log.warning('Could not import legacy data file %s', relative)
    if found and not report['errors']:
        try:
            _write(marker, _json(report))
        except OSError:
            report['errors'] += 1
            log.warning('Could not save data import completion marker')
    return report
