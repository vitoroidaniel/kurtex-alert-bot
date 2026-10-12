"""Small, deterministic multilingual task router for the maintenance assistant.

No external translation service and no hidden chain-of-thought: it selects the
right response contract and expands common maintenance vocabulary for retrieval.
"""
import re
import unicodedata

LANGUAGE_NAMES = {'en':'English','ro':'Romanian','ru':'Russian','es':'Spanish'}

# Whole words are deliberate: don't mistake part numbers, unit IDs, or names for a language.
MARKERS = {
 'ro': {'este','sunt','care','cum','unde','ce','vezi','acest','această','aceasta','cod','eroare','înțeleg','inteleg','avem','trebuie','pentru','poate','verifica','verifică','schimbat','piesă','piesa','piese','defect','scurgere','frână','frana','camion','remorcă','remorca','aer','problema','motorul','vreau','arată','arata','în','din','la','nu'},
 'es': {'qué','que','cómo','como','dónde','donde','ves','analiza','analizar','explica','explicar','muéstrame','muestrame','necesito','ayuda','este','esta','por','favor','tenemos','para','puede','revisar','verificar','cambiado','pieza','piezas','falla','avería','averia','fuga','freno','camión','camion','remolque','problema','quiero','muestra','hay','del','una','los','las'},
 'en': {'what','where','how','please','check','show','explain','find','driver','issue','fault','broken','leak','brake','truck','trailer','repair','replace','replaced','video','photo','picture','unit','similar','cases','need','is','are','the','for','with'},
}

# Canonical terms improve matching Russian/Romanian/Spanish descriptions to English fleet notes.
# Terms here are retrieval aids, not diagnoses or proof of matching components.
TERMS = {
 'air leak': ('pierdere de aer','pierde aer','scăpare de aer','scapare de aer','scurgere de aer','утечка воздуха','травит воздух','fuga de aire','pérdida de aire','perdida de aire'),
 'brake': ('frână','frâna','frana','frâne','frane','тормоз','тормоза','тормозная','freno','frenos'),
 'engine': ('motor','motorul','двигатель','двигателя','motor del camión'),
 'tire': ('anvelopă','anvelopa','roată','roata','шина','шины','колесо','neumático','neumatico','llanta'),
 'wheel rim': ('jantă','janta','обод','колесный диск','диск колеса','rin','aro de rueda'),
 'coolant leak': ('scurgere antigel','pierde antigel','утечка антифриза','утечка охлаждающей жидкости','fuga de refrigerante'),
 'oil leak': ('scurgere ulei','pierde ulei','утечка масла','течет масло','fuga de aceite'),
 'air compressor': ('compresor aer','компрессор воздуха','compresor de aire'),
 'battery': ('baterie','acumulator','аккумулятор','batería','bateria'),
 'turbo': ('turbină','turbina','турбина','турбокомпрессор','turbocompresor'),
 'reefer': ('frigider','agregat frigorific','рефрижератор','холодильная установка','refrigeración','refrigeracion'),
 'fault code': ('cod eroare','cod de eroare','cod defect','код ошибки','код неисправности','código de falla','codigo de falla'),
 'part number': ('cod piesă','cod piesa','număr piesă','numar piesa','артикул детали','номер детали','número de pieza','numero de pieza'),
}

TASK_WORDS = {
 'history': ('similar case','similar cases','previous case','past case','case history','fleet history','historical cases',
             'cazuri similare','cazuri asemănătoare','cazuri asemanatoare','istoric cazuri','cazuri precedente',
             'похожие случаи','похожие заявки','история ремонтов','предыдущие случаи',
             'casos similares','casos anteriores','historial de casos'),
 'fault_code': ('fault code','error code','spn','fmi','dtc','obd','cod eroare','cod de eroare','cod defect',
                'код ошибки','код неисправности','ошибка spn','código de falla','codigo de falla','código de error','codigo de error'),
 'parts': ('part number','replacement part','parts manual','part #','număr piesă','numar piesa','cod piesă','cod piesa',
           'номер детали','артикул детали','запчасть','número de pieza','numero de pieza','repuesto'),
 'visual': ('what do you see','describe the image','describe this picture','what is visible',
            'ce vezi','descrie imaginea','ce se vede','что видно','опиши фото','что видишь',
            'qué ves','que ves','describe la imagen','qué se ve','que se ve'),
}


def _normalize(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return re.sub(r'\s+', ' ', value).strip()


def detect_language(message, transcript=''):
    """Best-effort routing; unknown/short input defaults to English, not certainty."""
    value = _normalize(message)
    if re.search(r'[а-яё]', value):
        return 'ru'
    words = set(re.findall(r"[a-zăâîșşțţáéíóúüñ]+", value))
    scores = {lang:len(words & markers) for lang,markers in MARKERS.items()}
    if any(c in value for c in 'ăâîșşțţ'): scores['ro'] += 3
    if any(c in value for c in 'ñ¿¡'): scores['es'] += 3
    ranked = sorted(scores.items(),key=lambda x:x[1],reverse=True)
    if ranked[0][1] >= 2 and ranked[0][1] > ranked[1][1]:return ranked[0][0]
    # For a prompt like "Analyze this video", don't switch language because
    # the driver in the clip speaks Russian or Romanian.
    if not value or not re.search(r'[a-zăâîșşțţа-яё]',value):
        if re.search(r'[а-яё]', _normalize(transcript)):return 'ru'
    return 'en'


def classify_task(message, has_video=False, has_images=False):
    text=_normalize(message)
    if has_video:return 'video_inspection'
    for task in ('history','fault_code','parts','visual'):
        if any(phrase in text for phrase in TASK_WORDS[task]):return task
    if has_images:return 'image_inspection'
    return 'maintenance_question'


def retrieval_query(message, transcript='', max_extra=8):
    value=' '.join([str(message or ''),str(transcript or '')[:2400]]).strip()
    folded=_normalize(value)
    extras=[]
    for term,variants in TERMS.items():
        if term in folded or any(v in folded for v in variants):
            extras.append(term)
        if len(extras)>=max_extra:break
    return (value+' '+' '.join(extras)).strip()[:6800]


def response_policy(message, has_video=False, has_images=False, transcript=''):
    lang=detect_language(message,transcript)
    task=classify_task(message,has_video,has_images)
    sections={
        'history':'Lead with matching cases, then confirmed recorded repairs only. No generic troubleshooting unless requested.',
        'fault_code':'Lead with what the exact code means IF verified. State make/model ambiguity and the shortest useful checks; cite OEM sources when available.',
        'parts':'Lead with verified part identification or say identification is not confirmed. Do not invent OEM part numbers or compatibility.',
        'visual':'Describe visible evidence directly. Do not add an unrelated maintenance report.',
        'image_inspection':'State what is visibly damaged, what cannot be confirmed, and the next useful inspection.',
        'video_inspection':'Start with what the sampled video frames show and include timestamps for concrete observations. Then briefly separate what the driver said in audio, if transcribed. Note important discrepancies, uncertainty, and the next useful action. Never pretend all frames or continuous motion were inspected.',
        'maintenance_question':'Answer the exact question first; add only the highest-value next steps.'
    }
    return {'language':lang,'language_name':LANGUAGE_NAMES[lang],'task':task,
            'instructions':('Reply in '+LANGUAGE_NAMES[lang]+'. Preserve original fault codes, OEM terms and part numbers verbatim. '
                'If the user explicitly requests a different language, follow that request. '
                'Write like a concise senior fleet maintenance advisor, not a generic chatbot. '
                'FIRST LINE: direct answer or immediate safety action. '+sections[task]+' '
                'Default to 1-3 short paragraphs or at most 3 practical checks; expand only if complexity or the user asks for detail. '
                'Do not dump all case records, repeat the question, pad with disclaimers, or show empty headings. '
                'Separate observed evidence, driver-reported speech, historical records and hypotheses. '
                'When uncertainty affects safety or part selection, ask one precise clarifying question. '
                'Always verify safety-critical actions, model-specific codes and part numbers against OEM documentation.')}
