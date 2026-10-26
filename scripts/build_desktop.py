"""Create the portable Windows application without copying any case files."""
import argparse
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def build(output, work):
    project=Path(__file__).resolve().parents[1]
    output=output.resolve();work=work.resolve()
    output.mkdir(parents=True,exist_ok=True);work.mkdir(parents=True,exist_ok=True)
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QImage,QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image
    image=QImage(256,256,QImage.Format_ARGB32);image.fill(0)
    painter=QPainter(image);QSvgRenderer(str(project/'q4r/ui/mark.svg')).render(painter);painter.end()
    image.save(str(work/'mark.png'))
    Image.open(work/'mark.png').save(work/'mark.ico',sizes=[(16,16),(32,32),(48,48),(128,128),(256,256)])
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir','--name','Q4R',
             '--icon',str(work/'mark.ico'),'--collect-data','q4r','--collect-all','playwright',
             '--hidden-import','PySide6.QtWebEngineWidgets','--hidden-import','PySide6.QtWebChannel',
             '--distpath',str(output/'portable'),'--workpath',str(work/'build'),'--specpath',str(work),str(project/'desktop_entry.py')]
    subprocess.run(command,cwd=project,check=True)
    folder=output/'portable/Q4R'
    # Qt 6.12 uses Windows' ICU forwarding library. Some development PATHs
    # contain an incompatible unversioned Conda ICU which PyInstaller resolves
    # first. Never ship it over the Windows library (QtCore otherwise fails).
    for name in ('icuuc.dll', 'icuin.dll', 'icudt78.dll'):
        stray = folder/'_internal'/name
        if stray.is_file():stray.unlink()
    # Freeze the OpenSSL pair belonging to this Python runtime. A foreign
    # Conda/Poppler pair on PATH can import successfully but fail SSLContext()
    # initialization with FileNotFoundError before the first scan starts.
    for name in ('libssl-3-x64.dll', 'libcrypto-3-x64.dll'):
        native = Path(sys.base_prefix)/'DLLs'/name
        if not native.is_file():raise RuntimeError('Python TLS runtime missing: '+name)
        shutil.copy2(native, folder/'_internal'/name)
    # Some Python distributions extend ssl.SSLContext with a bundled trust
    # policy file. Preserve the distribution's policy instead of weakening TLS.
    import ssl
    trust_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
    if trust_policy.is_file():shutil.copy2(trust_policy, folder/'_internal'/trust_policy.name)
    shutil.copy2(project/'LICENSE',folder/'LICENSE-Q4R.txt')
    (folder/'START-HERE.txt').write_text('Q4R 0.8.0\nExtract the entire folder, then open Q4R.exe. Keep _internal next to it.\nCases are stored in your local application-data folder, not next to the executable.\nThe photo is a local reference only. Scores normalize text evidence, not identity probability.\nDesktop browser search requires its Playwright browser runtime; public connectors work without it.\n\nQt/PySide6 libraries are dynamically bundled under LGPLv3; sources and notices:\nhttps://www.qt.io/licensing/open-source-lgpl-obligations\nhttps://code.qt.io/cgit/pyside/pyside-setup.git/\nhttps://code.qt.io/cgit/qt/\n',encoding='utf-8')
    for package in ('PySide6_Essentials','PySide6_Addons','PySide6_WebEngine','shiboken6','playwright'):
        dist=importlib.metadata.distribution(package)
        for item in dist.files or []:
            if 'license' not in str(item).lower() and 'copying' not in str(item).lower():continue
            source=Path(dist.locate_file(item))
            if not source.is_file():continue
            relative=Path(*[part for part in item.parts if part not in ('..','.')])
            target=folder/'licenses'/package/relative
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    archive=output/'Q4R-Windows-0.8.0.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in folder.rglob('*'):
            if path.is_file():z.write(path,path.relative_to(folder.parent))
    print(archive)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--work',type=Path,required=True)
    args=parser.parse_args();build(args.output,args.work)
