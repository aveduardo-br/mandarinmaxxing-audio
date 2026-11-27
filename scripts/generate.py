"""Generate one audio file per HSK word with ElevenLabs, clean it, check it, and log it.

Usage:
    python3 scripts/generate.py --level 1 [--limit 400] [--max-chars 1500] [--no-check] [--dry-run]

Reads   words/hsk-N.csv   the official word lists (copied from the skill repository)
        special.csv       words that need another text sent to the voice, or a second reading
        redo.txt          words to record again, one per line
        status.csv        the log: one line per word and reading
Writes  audio/<word>.mp3  and updates status.csv and redo.txt

The API key comes from the environment variable ELEVENLABS_API_KEY. Nothing is generated twice:
a word with a line in status.csv is skipped unless it is listed in redo.txt, and a line marked
approved=yes is never recorded again. A line recorded with another voice counts as not recorded,
approved or not, so that every file ends up in the same voice.
"""
import argparse
import csv
import datetime
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_ID = '4AfodMgwXps9oZFhHzoj'  # a native Mandarin voice, chosen by ear in the trial of 27 Nov 2026
VOICE_NAME = VOICE_ID  # what goes in the voice column of status.csv
RANK = ['ok', 'listen', 'not checked', 'tone?', 'differs', 'nothing heard']  # best check first
TTS_MODEL = 'eleven_v4'
STT_MODEL = 'scribe_v2'
API = 'https://api.elevenlabs.io/v1'
FIELDS = ['word', 'pinyin', 'level', 'file', 'text', 'model', 'voice', 'chars', 'seconds',
          'generated', 'check', 'heard', 'approved']
TONES = {'̄': '1', '́': '2', '̌': '3', '̀': '4'}
HAN = re.compile(r'[㐀-鿿]')


def clean_word(word):
    return re.sub(r'\d+$', '', word.strip())


def first_reading(pinyin):
    return pinyin.split('/')[0].strip()


def tone_ascii(pinyin):
    """hào -> ha4o, nǚ'ér -> nv3e2r: plain letters, each tone mark turned into a digit after its vowel."""
    out = []
    for ch in unicodedata.normalize('NFD', first_reading(pinyin).lower()):
        if ch in TONES:
            out.append(TONES[ch])
        elif ch == '̈':  # the dots of ü
            out[-1] = 'v'
        elif ch.isalpha() and ch.isascii():
            out.append(ch)
    return ''.join(out)


def plain(pinyin):
    return re.sub(r'\d', '', tone_ascii(pinyin))


def load_words(levels):
    """One entry per word and reading, in list order. The reading met first is the default one
    and gets the plain file name; any other reading of the same word gets word_pinyin."""
    entries, seen, default = [], set(), {}
    for level in levels:
        path = os.path.join(ROOT, 'words', f'hsk-{level}.csv')
        for row in csv.DictReader(open(path, encoding='utf-8-sig')):
            word, pinyin = clean_word(row['word']), first_reading(row['pinyin'])
            key = (word, tone_ascii(pinyin))
            if key in seen:
                continue
            seen.add(key)
            default.setdefault(word, key[1])
            is_default = default[word] == key[1]
            stem = word if is_default else f'{word}_{key[1]}'
            entries.append({'word': word, 'pinyin': pinyin, 'level': str(level),
                            'file': f'audio/{stem}.mp3', 'default': is_default})
    readings = {}
    for e in entries:
        readings[e['word']] = readings.get(e['word'], 0) + 1
    for e in entries:
        e['two'] = readings[e['word']] > 1  # the transcriber can't tell two readings apart: a person must listen
    return entries


def load_status():
    path = os.path.join(ROOT, 'status.csv')
    if not os.path.exists(path):
        return {}
    return {r['file']: r for r in csv.DictReader(open(path, encoding='utf-8-sig'))}


def save_status(status):
    with open(os.path.join(ROOT, 'status.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for key in sorted(status, key=lambda k: (int(status[k]['level']), status[k]['word'], k)):
            w.writerow({k: status[key].get(k, '') for k in FIELDS})


def load_special():
    path = os.path.join(ROOT, 'special.csv')
    out = {}
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding='utf-8-sig')):
            if r.get('word') and r.get('text'):
                out[(clean_word(r['word']), tone_ascii(r.get('pinyin', '')))] = r['text'].strip()
    return out


def load_redo():
    path = os.path.join(ROOT, 'redo.txt')
    if not os.path.exists(path):
        return []
    return [clean_word(l) for l in open(path, encoding='utf-8') if l.strip() and not l.startswith('#')]


def request(method, url, key, tries=5, **kw):
    import requests
    for attempt in range(tries):
        r = requests.request(method, url, headers={'xi-api-key': key}, timeout=120, **kw)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            time.sleep(2 ** attempt * 2)
            continue
        return r
    return r


LANG = {'send': True}


def tts(text, key, voice=None):
    body = {'text': text, 'model_id': TTS_MODEL}
    url = f'{API}/text-to-speech/{voice or VOICE_ID}?output_format=mp3_44100_128'
    if LANG['send']:
        r = request('POST', url, key, json=dict(body, language_code='zh'))
        if r.status_code in (400, 422) and 'language' in r.text.lower():
            LANG['send'] = False  # this model takes no language code: go on without it
            print('LANGUAGE: the voice model refused language_code=zh:', r.text[:200])
        else:
            if r.status_code >= 400:
                raise RuntimeError(f'{r.status_code} {r.text[:300]}')
            if not LANG.get('said'):
                LANG['said'] = True
                print('LANGUAGE: language_code=zh accepted')
            return r.content
    r = request('POST', url, key, json=body)
    if r.status_code >= 400:
        raise RuntimeError(f'{r.status_code} {r.text[:300]}')
    return r.content


def stt(path, key):
    with open(path, 'rb') as f:
        r = request('POST', f'{API}/speech-to-text', key,
                    data={'model_id': STT_MODEL, 'language_code': 'zh'},
                    files={'file': (os.path.basename(path), f, 'audio/mpeg')})
    r.raise_for_status()
    return r.json().get('text', '')


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def finish(raw_path, out_path):
    """Trim the silence at both ends, bring the peak to -2 dB, and save a small mono MP3."""
    trim = ('silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,areverse,'
            'silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08,areverse')
    probe = run(['ffmpeg', '-hide_banner', '-i', raw_path, '-af', trim + ',volumedetect', '-f', 'null', '-'])
    m = re.search(r'max_volume: (-?[\d.]+) dB', probe.stderr)
    gain = max(min(-2.0 - float(m.group(1)), 20.0), -20.0) if m else 0.0
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    done = run(['ffmpeg', '-hide_banner', '-y', '-i', raw_path, '-af', f'{trim},volume={gain:.1f}dB',
                '-ac', '1', '-ar', '22050', '-b:a', '32k', out_path])
    if done.returncode != 0:
        raise RuntimeError(done.stderr[-400:])
    length = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', out_path])
    return round(float(length.stdout.strip() or 0), 2)


def finish_take(raw_path, take_path):
    """finish() for a file whose name doesn't end in .mp3."""
    tmp = take_path + '.mp3'
    seconds = finish(raw_path, tmp)
    os.replace(tmp, take_path)
    return seconds


def rank(check):
    for i, start in enumerate(RANK):
        if check.startswith(start):
            return i
    return len(RANK)


def judge(word, pinyin, heard, seconds):
    """Compare what the transcriber heard with the word. It catches a wrong word, not a wrong tone."""
    han = ''.join(HAN.findall(heard))
    notes = []
    if seconds < 0.15:
        notes.append('too short')
    if seconds > 0.9 + 0.75 * len(word):
        notes.append('slow')
    if not han:
        result = 'nothing heard'
    elif han == word:
        result = 'ok'
    else:
        try:
            from pypinyin import lazy_pinyin, Style
            sound = ''.join(lazy_pinyin(han, style=Style.TONE))
            if tone_ascii(sound) == tone_ascii(pinyin):
                result = 'ok same sound'
            elif plain(sound) == plain(pinyin):
                result = 'tone?'
            else:
                result = 'differs'
        except ImportError:
            result = 'differs'
    # one syllable alone is where the transcriber guesses most: it hears 爱 as "I" and 八 as 吧.
    # Those go to a person's ear instead of the flagged list.
    if len(word) == 1 and not result.startswith('ok'):
        result = 'listen, one syllable'
    return '; '.join([result] + notes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--level', required=True, help='1 to 6, or several: 1,2')
    ap.add_argument('--limit', type=int, default=400, help='most words to record in this run')
    ap.add_argument('--max-chars', type=int, default=1500, help='most characters to send to the voice in this run')
    ap.add_argument('--no-check', action='store_true', help='skip the transcription check')
    ap.add_argument('--dry-run', action='store_true', help='show what would be recorded and stop')
    args = ap.parse_args()

    levels = [int(x) for x in args.level.split(',')]
    # default readings are decided across every level up to the highest asked for
    entries = [e for e in load_words(range(1, max(levels) + 1)) if int(e['level']) in levels]
    status, special, redo = load_status(), load_special(), load_redo()

    todo, waiting = [], []
    for e in entries:
        key = (e['word'], tone_ascii(e['pinyin']))
        old = status.get(e['file'])
        again = e['word'] in redo
        same_voice = bool(old) and old.get('voice') == VOICE_NAME
        if same_voice and old.get('approved', '').lower() == 'yes':
            continue
        if same_voice and old.get('generated') and not again:
            continue
        if key in special:
            e['text'] = special[key]
        elif e['default']:
            e['text'] = e['word'] + '。'
        else:
            waiting.append(e)  # a second reading: needs a line in special.csv
            status.setdefault(e['file'], dict(e, check='needs special.csv', generated=''))
            continue
        todo.append(e)

    chosen, chars = [], 0
    for e in todo:
        if len(chosen) >= args.limit or chars + len(e['text']) > args.max_chars:
            break
        chosen.append(e)
        chars += len(e['text'])

    print(f'levels {levels}: {len(entries)} words and readings, {len(todo)} to record, '
          f'{len(chosen)} in this run ({chars} characters), {len(waiting)} waiting for special.csv')
    if args.dry_run:
        for e in chosen[:40]:
            print(f"  {e['file']}  <-  {e['text']}  ({e['pinyin']})")
        if waiting:
            print('waiting:', ' '.join(f"{e['word']}({e['pinyin']})" for e in waiting))
        return 0

    key = os.environ.get('ELEVENLABS_API_KEY')
    if not key:
        sys.exit('ELEVENLABS_API_KEY is not set.')

    made, flagged, failed, to_hear = 0, [], [], []
    for e in chosen:
        out_path = os.path.join(ROOT, e['file'])
        try:
            # a word of two syllables or more that the check doesn't recognise is recorded again,
            # up to three takes. The first take that passes is kept; if none passes, the best one is.
            # Later takes of a plain word drop the full stop, which sometimes makes the voice repeat it.
            takes = 1 if args.no_check or len(e['word']) == 1 else 3
            best = None
            for take in range(takes):
                text = e['text']
                if take and text == e['word'] + '。':
                    text = e['word']
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
                    tmp.write(tts(text, key))
                trial_path = out_path + f'.take{take}'
                seconds = finish_take(tmp.name, trial_path)
                os.unlink(tmp.name)
                heard, check = '', 'not checked'
                if not args.no_check:
                    heard = stt(trial_path, key).strip()
                    check = judge(e['word'], e['pinyin'], heard, seconds)
                score = (rank(check), 'slow' in check, take)
                if best is None or score < best[0]:
                    best = (score, trial_path, seconds, heard, check, text, take)
                if rank(check) <= 2:
                    break
            _, kept, seconds, heard, check, e['text'], kept_take = best
            os.replace(kept, out_path)
            for n in range(takes):
                if os.path.exists(out_path + f'.take{n}'):
                    os.unlink(out_path + f'.take{n}')
            if take:
                check += f'; take {kept_take + 1} of {take + 1}'
            if e.get('two'):
                check = 'two readings, listen; ' + check
        except Exception as err:  # keep going: one bad word must not stop the run
            failed.append(f"{e['word']}: {str(err)[:160]}")
            continue
        status[e['file']] = {'word': e['word'], 'pinyin': e['pinyin'], 'level': e['level'], 'file': e['file'],
                             'text': e['text'], 'model': TTS_MODEL, 'voice': VOICE_NAME,
                             'chars': str(len(e['text'])), 'seconds': str(seconds),
                             'generated': datetime.date.today().isoformat(), 'check': check,
                             'heard': heard, 'approved': ''}
        made += 1
        if check.startswith('listen') or check.startswith('two readings'):
            to_hear.append(e['word'])
        elif not check.startswith('ok') and not check.startswith('not checked'):
            flagged.append(f"{e['word']} ({e['pinyin']}): {check}, heard “{heard}”")
        if made % 25 == 0:
            save_status(status)
    save_status(status)

    done_words = {status[k]['word'] for k in status if status[k].get('generated') == datetime.date.today().isoformat()}
    left = [w for w in redo if w not in done_words]
    with open(os.path.join(ROOT, 'redo.txt'), 'w', encoding='utf-8') as f:
        f.write(''.join(w + '\n' for w in left))

    lines = [f'Recorded {made} words ({chars} characters planned). Flagged {len(flagged)}. Failed {len(failed)}.',
             f'Still to record in these levels: {len(todo) - made}. Waiting for special.csv: {len(waiting)}.']
    lines += ['', 'To listen to (one syllable or two readings): ' + ' '.join(to_hear)] if to_hear else []
    lines += ['', 'Flagged:'] + [f'- {x}' for x in flagged] if flagged else []
    lines += ['', 'Failed:'] + [f'- {x}' for x in failed] if failed else []
    print('\n'.join(lines))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        open(summary, 'a', encoding='utf-8').write('\n'.join(lines) + '\n')
    return 0 if made or not chosen else 1


if __name__ == '__main__':
    sys.exit(main())
