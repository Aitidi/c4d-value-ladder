"""Build and verify the installable Python plugin ZIP; no C4D runtime needed."""
import ast
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    repo = Path(__file__).resolve().parents[1]
    plugin = repo / 'plugin' / 'ValueLadder'
    entry = plugin / 'ValueLadder.pyp'
    source = entry.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(entry))
    version = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets))
    compile(source, str(entry), 'exec')
    files = {'ValueLadder/ValueLadder.pyp': entry.read_bytes()}
    for path in sorted((plugin / 'res').rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.h', '.res', '.str'):
            files['ValueLadder/' + path.relative_to(plugin).as_posix()] = path.read_bytes()
    for name in ('README.md', 'LICENSE', 'docs/redshift-camera-coverage.md'):
        files['ValueLadder/' + name] = (repo / name).read_bytes()
    instructions = f'''ValueLadder {version} / Cinema 4D 2027 / Windows

安装：先保存场景并退出 C4D，将本包完整的 ValueLadder 文件夹复制到 plugins 目录，
替换旧版后重新启动。不要同时安装两份插件。正式启动无需打开 dev 面板。
使用：数值框上按住中键，选择档位，左右拖动；松开确认，按住时点击右键取消。
启动设置：编辑 → 偏好设置 → ValueLadder。
当前会话开关：扩展 → ValueLadder。

本版包含 Redshift 相机联动与 Offset 修复。
探测顺序：+0.001、-0.001、+1、-1、原生方向键。旧模拟拖动探测保持关闭。
支持范围及验证记录见 README.md 与 docs/redshift-camera-coverage.md。
'''
    files['ValueLadder/安装说明.txt'] = instructions.encode('utf-8-sig')
    output = repo.parent / '_deliverables'
    output.mkdir(exist_ok=True)
    archive = output / f'ValueLadder-{version}.zip'
    if archive.exists():
        raise FileExistsError(f'Existing package preserved: {archive}')
    with ZipFile(archive, 'w', ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, data in sorted(files.items()):
            bundle.writestr(name, data)
    with ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert set(bundle.namelist()) == set(files)
        for name, data in files.items():
            assert bundle.read(name) == data, name
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / f'ValueLadder-{version}.sha256').write_text(
        f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'{archive}\n{len(files)} files, {archive.stat().st_size} bytes\nSHA256 {digest}')


if __name__ == '__main__':
    main()
