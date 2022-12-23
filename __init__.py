import anki.notes
import requests
import time
import datetime
from aqt import mw
from aqt.operations import QueryOp
import aqt
from aqt import gui_hooks

config = mw.addonManager.getConfig(__name__)
headers = {'Authorization': 'Bearer '+config['API_KEY']}
include_audio = config['include_audio']
deckName = config['deck_name']
modelName = 'WaniKaniVocabAddOn'


# WaniKani API part



def load_subjects(url):
    response = requests.get(url, headers=headers)
    if response.status_code == 422:
        print('Data is still fresh, not refreshing')
        return []
    else:
        result = response.json()
        subject_ids = list(map(lambda data: data['data']['subject_id'], result['data']))
        next_url = result['pages']['next_url']
        if next_url is not None:
            subject_ids.extend(load_subjects(next_url))
        return subject_ids


def map_subject(subject_id):
    response = requests.get('https://api.wanikani.com/v2/subjects/' + str(subject_id), headers=headers)
    while response.status_code == 429:
        current_time = time.time()
        reset_timestamp = float(response.headers['RateLimit-Reset'])
        wait_time = reset_timestamp - current_time
        print('going to sleep for ', (wait_time + 5), 'seconds, waiting for request limit to reset')
        time.sleep(wait_time + 5)
        response = requests.get('https://api.wanikani.com/v2/subjects/' + str(subject_id), headers=headers)

    subject = response.json()
    sub_data = subject['data']
    print('loading subject', str(subject_id) + ';', sub_data['characters'])
    return {'characters': sub_data['characters'],
            'meanings': get_meanings(sub_data),
            'readings': get_readings(sub_data),
            'audio': get_audio(sub_data),
            'subject_id': subject_id}


def get_meanings(sub_data):
    meanings = list(map(lambda meaning: meaning['meaning'],
                        filter(lambda meaning: meaning['accepted_answer'], sub_data['meanings'])))
    meanings.extend(list(map(lambda meaning: meaning['meaning'],
                             filter(lambda meaning: meaning['type'] != 'blacklist', sub_data['auxiliary_meanings']))))
    return meanings


def get_readings(sub_data):
    return list(map(lambda reading: reading['reading'],
                    filter(lambda reading: reading['accepted_answer'], sub_data['readings'])))


def get_audio(sub_data):
    return sub_data['pronunciation_audios'][0]['url']




# ANKI PART



def download(col, filename, url):
    client = anki.sync.AnkiRequestsClient()
    client.timeout = 5

    resp = client.get(url)
    if resp.status_code != 200:
        raise Exception('{} download failed with return code {}'.format(url, resp.status_code))
    col.media.write_data(filename, client.stream_content(resp))

    return


def subject_to_anki_note(col, deck_id, model, subject):
    anki_note = anki.notes.Note(col, model)
    anki_note.note_type()['did'] = deck_id

    meanings = ', '.join(subject['meanings'])
    readings = ', '.join(subject['readings'])
    anki_note['Meanings'] = meanings
    anki_note['Kanji'] = subject['characters']
    anki_note['Reading'] = readings
    anki_note['WaniKaniSubjectId'] = str(subject['subject_id'])

    if include_audio:
        audio_filename = subject['characters']+'.mp3'
        download(col, audio_filename, subject['audio'])
        anki_note['Pronunciation'] += u'[sound:{}]'.format(audio_filename)

    return anki_note

def is_duplicate(col, deck_id, new_note):
    csum = anki.utils.field_checksum(new_note['WaniKaniSubjectId'])
    query = 'select n.id from notes n join cards c on n.id = c.nid where n.csum=? and c.did=?'
    queryArgs = [csum, deck_id]
    return len(list(map(lambda id: col.get_note(id), col.db.list(query, *queryArgs)))) > 0


def create_model_if_not_exists(col):
    model = col.models.by_name(modelName)
    if model is None:
        models = col.models
        m = models.new(modelName)
        models.add_field(m, models.new_field('WaniKaniSubjectId'))
        models.add_field(m, models.new_field('Meanings'))
        models.add_field(m, models.new_field('Kanji'))
        models.add_field(m, models.new_field('Reading'))
        models.add_field(m, models.new_field('Pronunciation'))

        template = models.new_template('sampleWKTemplate')
        template['qfmt'] = '{{Meanings}}'
        template['afmt'] = '{{Meanings}}<hr>{{Kanji}}<hr>{{Reading}}<hr>{{Pronunciation}}'
        models.addTemplate(m, template)
        models.add(m)

def add_cards_anki(col, subjects_mapped):
    create_model_if_not_exists(col)
    deck = col.decks.by_name(deckName)
    if deck is None:
        did = col.decks.id(deckName)
        deck = col.decks.get(did)
    model = col.models.by_name(modelName)

    #mw.requireReset()
    for subject in subjects_mapped:
        anki_note = subject_to_anki_note(col, deck['id'], model, subject)
        if not is_duplicate(col, deck['id'], anki_note):
            col.addNote(anki_note)
    col.autosave()
    #mw.maybeReset()

def on_success(col):
    config['last_sync'] = datetime.datetime.now().isoformat()
    mw.addonManager.writeConfig(__name__, config)
    print('Sync completed')
    # showInfo('Data imported!Thanks for waiting! <3')

def import_vocab_from_wanikani(col):
    all_subjects = load_subjects(
        'https://api.wanikani.com/v2/assignments?started=true&subject_types=vocabulary&updated_after=' + str(
            config['last_sync']))

    all_subjects_mapped = []
    i=0
    for subject in all_subjects:
        all_subjects_mapped.append(map_subject(subject))
        i+=1
        aqt.mw.taskman.run_on_main(
            lambda: aqt.mw.progress.update(
                label=f"Loading WaniKani vocabulary: {all_subjects_mapped[-1]['characters']} ({i}/{len(all_subjects)})",
                value=i,
                max=len(all_subjects),
            )
        )

    add_cards_anki(col, all_subjects_mapped)

# MAIN CODE

def main():

    QueryOp(
        parent=mw,
        op=import_vocab_from_wanikani,
        success=on_success,
    ).with_progress().run_in_background()


# Execution starts here
gui_hooks.main_window_did_init.append(main)

