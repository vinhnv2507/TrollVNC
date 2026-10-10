"""Package the vendor GUI without signing keys or customer configuration."""
import argparse
import json
import subprocess
import zipfile
from pathlib import Path

from vendor_keygen import __version__
from vendor_keygen.core import PUBLIC_KEY_SHA256


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if not (args.source/'CTLIOS Keygen.exe').is_file():raise SystemExit('Executable missing')
    for file in args.source.rglob('*'):
        if file.name in ('controlios_private.pem','devices.json','shopee_accounts.json'):
            raise SystemExit('Private data must not be packaged')
        if file.suffix=='.pem' and b'PRIVATE KEY' in file.read_bytes():
            raise SystemExit('Private signing key must not be packaged')
    metadata={'version':__version__,'component':'CTLIOS Keygen','public_key_sha256':PUBLIC_KEY_SHA256,
              'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for file in sorted(args.source.rglob('*')):
            if file.is_file():archive.write(file,file.relative_to(args.source).as_posix())
        archive.writestr('version.json',json.dumps(metadata,indent=2))
        archive.write('docs/tao-key-ban-quyen.md','Huong-dan.md')
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        assert all(not n.startswith(('/','\\','./')) and '..' not in n.split('/') and '\\' not in n for n in archive.namelist())
    print('PACKAGED',args.output.resolve())


if __name__=='__main__':main()
