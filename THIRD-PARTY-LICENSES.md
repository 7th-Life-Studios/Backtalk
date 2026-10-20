# Third-party licenses

Backtalk is free software. So is everything it is built on. This file records
what is bundled, who wrote it, what licence it carries, and where to get its
source.

All of it was read off the shipped build on 2026-09-18, not recalled from
memory. The complete machine-generated list of all 147 packages is in
[licenses/INVENTORY.md](licenses/INVENTORY.md). This file covers the parts that
carry obligations or that a user would recognise.

## Backtalk itself

Backtalk's own code, the part in `backtalk/`, is MIT. See [LICENSE](LICENSE).

## The install has two halves, and they are licensed differently

This matters, so it is worth being exact about it.

```
C:\Program Files\Backtalk\
    Backtalk\     the application   MIT, plus LGPL libraries
    engine\       the voice engine  GPLv3, a separate program
```

The application and the voice engine are **two separate programs**. They do not
share a process, an interpreter or an address space. The application talks to
the engine the way a browser talks to a web server: over HTTP, on the local
machine, on port 8880.

They are shipped in one installer for convenience, the same way a Linux
distribution ships programs under different licences on one disc.

## The application

MIT, with these libraries alongside it:

| Library | Licence | Source |
|---|---|---|
| PySide6 (Qt for Python) | LGPL-3.0 | https://download.qt.io/official_releases/QtForPython/ |
| shiboken6 | LGPL-3.0 | https://download.qt.io/official_releases/QtForPython/ |
| pynput | LGPL-3.0 | https://github.com/moses-palmer/pynput |
| NumPy | BSD-3-Clause | https://github.com/numpy/numpy |
| sounddevice | MIT | https://github.com/spatialaudio/python-sounddevice |
| requests | Apache-2.0 | https://github.com/psf/requests |

PySide6 and pynput are LGPL-3.0. Three things follow from that, and Backtalk
does all three:

1. The licence text ships with the program. It is in
   [licenses/LGPL-3.0.txt](licenses/LGPL-3.0.txt), alongside
   [licenses/GPL-3.0.txt](licenses/GPL-3.0.txt), which LGPL-3.0 builds on.
2. The libraries are named here, and so is the fact that they are LGPL.
3. You can replace them. Backtalk is built in PyInstaller's one-folder mode, so
   Qt arrives as ordinary DLL files at
   `Backtalk\_internal\PySide6\Qt6Core.dll` and so on. Swap in your own build of
   Qt and the application will load it. Nothing is statically linked and nothing
   is locked.

Neither library has been modified.

## The voice engine

The engine is [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI), which
is Apache-2.0, running the [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)
model, which is Apache-2.0.

**The engine bundle as a whole is GPL-3.0-or-later**, because it contains and
loads two GPLv3 components:

| Component | Licence | Why it is there |
|---|---|---|
| espeak-ng (`espeak-ng.dll`, `espeak-ng-data`) | GPL-3.0-or-later | Turns written text into phonemes |
| phonemizer-fork | GPL-3.0-or-later | The Python wrapper the engine imports for that |

That is not an accident or an oversight. Phonemisation is how any text-to-speech
system knows that "read" in "I read it" and "I will read it" are different
sounds, and espeak-ng is the tool the Kokoro project uses for it.

The consequence is that the engine directory is distributed under GPLv3. The
GPL text ships at [licenses/GPL-3.0.txt](licenses/GPL-3.0.txt). The full source
for everything in it is public:

- Kokoro-FastAPI: https://github.com/remsky/Kokoro-FastAPI
- Kokoro-82M: https://huggingface.co/hexgrad/Kokoro-82M
- espeak-ng: https://github.com/espeak-ng/espeak-ng
- phonemizer: https://github.com/bootphon/phonemizer
- Everything else: each package in `engine\python\Lib\site-packages` keeps its
  own `.dist-info` folder with its metadata, and each is a published package on
  PyPI under its own name and version. The versions are listed in
  [licenses/INVENTORY.md](licenses/INVENTORY.md).

`setuptools` (84.0.0, MIT) is in the engine and carries copies of other
packages inside itself, under `setuptools\_vendor`. The copies that ship there:

| Package | Version | Licence |
|---|---|---|
| autocommand | 2.2.2 | LGPLv3 |
| backports.tarfile | 1.2.0 | MIT |
| importlib_metadata | 8.7.1 | Apache-2.0 |
| jaraco.context | 6.1.0 | MIT |
| jaraco.functools | 4.4.0 | MIT |
| jaraco.text | 4.0.0 | MIT |
| more-itertools | 10.8.0 | MIT |
| packaging | 26.0 | Apache-2.0 OR BSD-2-Clause |
| platformdirs | 4.4.0 | MIT |
| tomli | 2.4.0 | MIT |
| wheel | 0.46.3 | MIT |
| zipp | 3.23.0 | MIT |

Each copy keeps its own `.dist-info` folder with its metadata, as the other
engine packages do.

Nothing in the engine has been modified except `run_engine.py`, which is
Backtalk's own launcher and is MIT.

If you would rather have the engine source as one archive than assemble it from
those links, ask and it will be provided. That offer is good for three years
from the date you received the software.

## Translation

Translation is [Argos Translate](https://github.com/argosopentech/argos-translate),
MIT. The language packs are downloaded models from the Argos Translate package
index, each carrying its own upstream licence, and they are not redistributed by
Backtalk. The installer fetches them from Argos, unmodified, only for the
languages you tick.

Language detection is [py3langid](https://github.com/adbar/py3langid), BSD-3-Clause.

## Weak-copyleft components, for completeness

These are file-level copyleft. They ship unmodified, which is all their licences
ask for.

| Component | Licence | Ships in |
|---|---|---|
| autocommand | LGPLv3 | engine (inside setuptools) |
| certifi | MPL-2.0 | engine |
| tqdm | MPL-2.0 | app and engine |
| num2words | LGPL | engine |
| mutagen | GPL-2.0-or-later | engine |

`mutagen` and `num2words` arrive as dependencies of other packages and are not
imported by any Backtalk or Kokoro-FastAPI code. They are listed anyway, because
they are on the disc and a list that leaves things out is not worth keeping.

Licence texts: [MPL-2.0](licenses/MPL-2.0.txt), [LGPL-3.0](licenses/LGPL-3.0.txt),
[GPL-3.0](licenses/GPL-3.0.txt).

## Everything else

The remaining packages are MIT, BSD, Apache-2.0 or PSF. Their texts are
[MIT](licenses/MIT.txt), [BSD-3-Clause](licenses/BSD-3-Clause.txt) and
[Apache-2.0](licenses/Apache-2.0.txt). Every one of them, with its version and
licence, is in [licenses/INVENTORY.md](licenses/INVENTORY.md).

## If something here is wrong

Licence metadata is sometimes wrong at the source, and this list is only as good
as what the packages declare about themselves. If you find a component that is
mislabelled or missing, open an issue at
https://github.com/7th-Life-Studios/Backtalk/issues and it will be fixed.
