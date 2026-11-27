"""Record the same short list of words with several voices, to compare them by ear.

Usage: python3 scripts/trial.py --voices key=voice_id,key=voice_id --words 普通,好,办理

Writes trials/<key>/<word>.mp3 and trials/report.csv (voice, word, seconds, heard, check).
One take per word, no retries: the point is to hear what each voice does on its own.
"""
import argparse
import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate as g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--voices', required=True)
    ap.add_argument('--words', required=True)
    args = ap.parse_args()
    key = os.environ.get('ELEVENLABS_API_KEY') or sys.exit('ELEVENLABS_API_KEY is not set.')
    pinyin = {e['word']: e['pinyin'] for e in reversed(g.load_words(range(1, 7)))}
    rows = []
    for pair in args.voices.split(','):
        name, voice = pair.split('=')
        for word in args.words.split(','):
            out = os.path.join(g.ROOT, 'trials', name, word + '.mp3')
            try:
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
                    tmp.write(g.tts(word + '。', key, voice))
                seconds = g.finish(tmp.name, out)
                os.unlink(tmp.name)
                heard = g.stt(out, key).strip()
                check = g.judge(word, pinyin.get(word, ''), heard, seconds)
            except Exception as err:
                seconds, heard, check = '', '', 'failed: ' + str(err)[:200]
                print(name, word, check)
            rows.append({'voice': name, 'word': word, 'pinyin': pinyin.get(word, ''),
                         'seconds': seconds, 'heard': heard, 'check': check})
    with open(os.path.join(g.ROOT, 'trials', 'report.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['voice', 'word', 'pinyin', 'seconds', 'heard', 'check'])
        w.writeheader()
        w.writerows(rows)
    for name in sorted({r['voice'] for r in rows}):
        mine = [r for r in rows if r['voice'] == name]
        ok = sum(1 for r in mine if r['check'].startswith('ok'))
        print(f'{name}: {ok} of {len(mine)} recognised by the check')


if __name__ == '__main__':
    main()
