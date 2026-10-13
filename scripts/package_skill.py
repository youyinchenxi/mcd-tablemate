"""Create a reproducible source Skill zip from the checked publishable files."""
from pathlib import Path
import sys
import zipfile

from release_check import ROOT, check
sys.path.insert(0, str(ROOT))
from maimai_team import __version__


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    errors, names = check()
    if errors:
        print('发布检查未通过，未打包。')
        return 1
    target = ROOT / f'artifacts/mcd-tablemate-v{__version__}.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            if Path(name).suffix.lower() == '.zip':
                continue
            data = (ROOT / name).read_bytes()
            info = zipfile.ZipInfo('mcd-tablemate/' + Path(name).as_posix(), date_time=(2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    print(f'Skill包已生成：{target.name}（不含凭证、私有数据或Git目录）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
