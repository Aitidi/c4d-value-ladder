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
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.h', '.res', '.str', '.png'):
            files['ValueLadder/' + path.relative_to(plugin).as_posix()] = path.read_bytes()
    for name in ('README.md', 'LICENSE', 'docs/redshift-camera-coverage.md'):
        files['ValueLadder/' + name] = (repo / name).read_bytes()
    instructions = f'''ValueLadder {version} / Cinema 4D 2027 / Windows

安装：先保存场景并退出 C4D，将本包完整的 ValueLadder 文件夹复制到 plugins 目录，
替换旧版后重新启动。不要同时安装两份插件。正式启动无需打开 dev 面板。
使用：数值框上按住中键，选择档位，左右拖动；松开确认，按住时点击右键取消。
设置：扩展 → ValueLadder Settings...（或 编辑 → 偏好设置 → ValueLadder），
可设置当前是否启用（仅本次会话）、是否随 C4D 启动、启用哪些阶梯挡位。
当前会话开关也可用：扩展 → ValueLadder（两者都不影响启动设置）。

参数识别：拖动数值框左侧的参数名称标签，读取 C4D 自带的参数 DescID，
支持对象、标签、工程设置、节点材质与场景节点端口（含 X/Y/Z 共用标签的向量行）。
整数参数自动隐藏小数挡位。没有可用标签的数值框不做调整，只在状态栏提示。
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
