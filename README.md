<img src="assets/icon_256.png" width="96" alt="Backtalk">

# Backtalk

Highlight text anywhere on Windows. Hear it read aloud.

Capture text from any application, pick a voice, adjust the speed, keep working. Backtalk speaks while you do something else.

## Features

- 55 voices across 8 languages
- English, Chinese, Spanish, Japanese, Hindi, French, Portuguese, Italian
- Real time translation (pick a Japanese voice, highlight English, hear Japanese)
- Speed control, adjustable mid-sentence
- Compact floating player
- Export to WAV
- Sortable reading history
- Light and dark themes

## Built with

Python, PySide6, PyTorch, Kokoro-82M, Kokoro-FastAPI, Argos Translate, pynput, NumPy, sounddevice

## Thank you

Built by Q Sterling with help from Claude and ChatGPT.

Voices by hexgrad (Kokoro-82M) and remsky (Kokoro-FastAPI). Translation by Argos Translate. UI built on Qt for Python (PySide6). Neural inference by PyTorch. Input capture by pynput. Audio playback by sounddevice and NumPy.

Thank you to Anthropic, OpenAI, the Kokoro community, and every open source contributor behind these tools. Backtalk exists because these projects are free and accessible.

## License

Backtalk's own code is MIT. See [LICENSE](LICENSE).

The install also contains other people's work under other licences, including
two LGPL libraries in the application and a GPLv3 voice engine that runs as a
separate program. What is bundled, under what terms, and where to get its
source is in [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md).
