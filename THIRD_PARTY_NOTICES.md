# Third-party components

The MoltenDaily application code is offered under GNU AGPL v3 or later, without warranty. The release includes complete application source and build instructions in source.zip. Retrieved papers and journal metadata retain their owners' terms and are not relicensed by the application.

The following components retain their respective copyright notices and licenses. Texts present in the installed distributions, together with GNU AGPL/GPL/LGPL texts, are in `licenses/`. Exact runtime versions are recorded in `components.json`; the full build environment is in the source archive's requirements-lock.txt.

| Component | License / upstream source |
|---|---|
| PyMuPDF and MuPDF | GNU AGPL v3, Artifex Software; [PyMuPDF source](https://github.com/pymupdf/PyMuPDF/tree/1.26.5), [MuPDF source](https://mupdf.com/releases/) |
| PySide6, Shiboken6 and Qt 6.8.3 | LGPL v3 / GPL and component licenses, The Qt Company and contributors; [PySide source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/), [Qt source archive](https://download.qt.io/archive/qt/6.8/6.8.3/single/) |
| CPython 3.12 | Python Software Foundation License; [CPython source](https://www.python.org/downloads/source/) |
| requests | Apache 2.0; [source](https://github.com/psf/requests) |
| urllib3 | MIT; [source](https://github.com/urllib3/urllib3) |
| certifi / certificate bundle | MPL 2.0; [source](https://github.com/certifi/python-certifi) |
| charset-normalizer | MIT; [source](https://github.com/jawah/charset_normalizer) |
| idna | BSD; [source](https://github.com/kjd/idna) |
| PyInstaller bootloader | GPL with distribution exception; [source](https://github.com/pyinstaller/pyinstaller/tree/v6.16.0) |

Qt/PySide libraries are dynamically loaded after PyInstaller extraction. You may modify or replace the LGPL-covered components, and debug those modifications. The source archive supplies the build path to relink/repackage the application with your modified component builds. The application places no additional restrictions on those rights. This app includes no DRM or code-signing lock preventing a rebuild.
