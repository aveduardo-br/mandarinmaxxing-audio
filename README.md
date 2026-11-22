# mandarinmaxxing-audio

One short recording for every word of the HSK lists, used by the [mandarinmaxxing](https://github.com/aveduardo-br/mandarinmaxxing) skill: click a word in a lesson's vocabulary table and you hear it.

Nobody installs this repository. The skill's table fetches a single file from here, through jsDelivr, when a word is clicked.

## What's here

| Path | What it is |
|---|---|
| `audio/` | One MP3 per word, named with the word itself: `你好.mp3`. A word with two readings has a second file named with its pinyin, such as `好_ha4o.mp3` |
| `words/` | The official word lists, HSK 1 to 6, copied from the skill repository |
| `status.csv` | The log: one line per word, with the text sent to the voice, the model, the date, what the check heard, and whether a person approved it |
| `special.csv` | Words that need another text sent to the voice, and the second readings of words with two readings |
| `redo.txt` | Words to record again, one per line |
| `scripts/generate.py` | Records, trims, levels, checks and logs |
| `.github/workflows/generate.yml` | Runs the script on GitHub, started by hand |

## How a batch is made

1. Open **Actions → Generate audio → Run workflow**, choose the level, and run it.
2. The script records every word of that level that has no line in `status.csv`, with the voice Sarah on ElevenLabs (`eleven_v4`).
3. Each file is trimmed, levelled, and saved as a small mono MP3.
4. Each file is transcribed and compared with the word. What doesn't match is flagged in the run's summary and in `status.csv`.
5. The files and the log are committed.

A run stops at the character cap you give it, so it can't spend more credits than you asked for. Run it again to continue where it stopped.

## Fixing a word

- **A bad recording:** add the word to `redo.txt` and run the level again.
- **A word the voice reads wrong:** add a line to `special.csv` with the text that makes it say the word right, then add the word to `redo.txt`.
- **A good recording:** set `approved` to `yes` in `status.csv`. It is never recorded again.

The transcription check catches a wrong word. It can't promise the tone is right, so people still listen: every flagged word, every word with two readings, and a sample of the rest.

## Setup

The workflow needs one secret in this repository: `ELEVENLABS_API_KEY`.

## License

The recordings were generated with ElevenLabs on a paid plan and belong to the mandarinmaxxing project. They are shared under the same terms as the skill: free to study with, not for reuse in other products. See [LICENSE](LICENSE). The word lists come from the HSK syllabus published by the Center for Language Education and Cooperation.
